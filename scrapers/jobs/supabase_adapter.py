"""Adapter concreto de `ConsolidationDataPort` contra Supabase PostgREST.

Faceta #91 del EPIC #82: auto-merge de Event/AcopioCenter por dedup_hash. Este
adapter implementa el acceso a datos REAL del job (`consolidation_job`) contra la
Data API de Supabase (PostgREST), corriendo desde GitHub Actions con acceso
DIRECTO (no via Vercel), segun la decision del equipo (Mathias, #82).

Espeja el patron de `scrapers/exporters/staging_exporter.py`:
  - `SupabaseConsolidationConfig.from_env` entra en dry-run (devuelve None) si
    faltan las env vars, distinguiendo el dry-run intencional (ninguna seteada)
    de una config parcial en prod (loguea a ERROR las faltantes).
  - Exige HTTPS: nunca manda la key ni datos sobre HTTP plano.
  - El `httpx.Client` es inyectable via el constructor (`client=`) para tests
    offline con `httpx.MockTransport`, igual que el exporter.
  - `follow_redirects=False`: httpx NO descarta cabeceras custom (apikey /
    Authorization) al seguir un redirect cross-host, asi que un 30x filtraria la
    credencial; el endpoint es fijo y un redirect inesperado se trata como error.
  - Reintentos con `backoff_delay` en status transitorios y errores de red.

Variables de entorno (patron acordado en #200)
----------------------------------------------
El auth sigue el patron de rol dedicado + JWT acordado en #200 (ver el comentario
de cierre): NADA de service_role en el path de consolidacion. Se leen:
  - ``SUPABASE_URL``: base de la Data API (PostgREST) del proyecto.
  - ``SUPABASE_PUBLISHABLE_KEY``: va en el header ``apikey`` (identifica el
    proyecto; NO otorga permisos por si sola: la seguridad la da RLS).
  - ``SUPABASE_CONSOLIDATION_JWT``: JWT HS256 con claim ``role=consolidation_job``,
    va en ``Authorization: Bearer <...>``. El adapter SOLO lo LEE del entorno; NO
    lo firma. PostgREST valida la firma local y hace SET ROLE al rol del claim.
``consolidate.yml`` (workflow, #96) es quien debera inyectarlos.

DEPENDENCIA DE BACKEND (critico)
--------------------------------
Este auth depende de una migracion de backend (en DataVenezuela/dataVenezuela)
que cree el rol ``consolidation_job`` (NOBYPASSRLS + grant al authenticator +
policies dedicadas TO consolidation_job sobre events/acopio_centers/
dedup_candidates/dedup_decisions y aportes.consolidated_at) y de la credencial
``SUPABASE_CONSOLIDATION_JWT``, ambos AUN INEXISTENTES. Sin esa migracion los
requests contra Supabase real dan permission-denied. El cambio de credencial NO
altera el criterio de winner-selection/trust_tier ni el schema.

FIDELIDAD DE SCHEMA (critico)
-----------------------------
El mapeo se construye contra el schema REAL del backend
(DataVenezuela/dataVenezuela supabase/migrations 0001/0004/0008/0009), NO contra
supuestos. Ver ``_CANONICAL_COLUMNS`` y ``fetch_unconsolidated`` para las
columnas exactas. IMPORTANTE: ``aportes.trust_tier`` NO existe en ninguna
migracion publicada; la decision del equipo (tier como columna de aportes,
A=1..D=4, menor gana) DEPENDE de una migracion de backend aun pendiente. El
adapter la lee de todos modos (`select=*`) y degrada de forma segura si falta
(ver ``_MISSING_TRUST_TIER``). Lo mismo aplica a ``fetched_at`` y
``confidence_score``, que en el schema real viven en otras tablas (person_sources
/ persons / acopio_centers), no en ``aportes``.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx

from scrapers.adapters._shared import backoff_delay, now_utc
from scrapers.adapters.http_client import USER_AGENT
from scrapers.jobs.ports import Record

log = logging.getLogger(__name__)

# --- Endpoints PostgREST ----------------------------------------------------
_APORTES_PATH = "/rest/v1/aportes"

# Nombre interno del tipo (Event/AcopioCenter) -> (slug de aportes.entity_type,
# path PostgREST de la tabla canonica). Los slugs replican _ENTITY_TYPE_SLUGS del
# staging_exporter y el enum del backend (0008): "event" | "acopio" | "person".
_ENTITY_TABLES: dict[str, tuple[str, str]] = {
    "Event": ("event", "/rest/v1/events"),
    "AcopioCenter": ("acopio", "/rest/v1/acopio_centers"),
}

# Columnas canonicas reales de cada tabla (supabase/migrations 0004 + dedup_hash
# de 0009). El upsert proyecta el payload del ganador SOLO sobre estas columnas:
# nunca inventa columnas (evita el falso-verde #90/#104/#187). dedup_hash se
# fuerza aparte (es el on_conflict), asi que no se lista aqui.
_CANONICAL_COLUMNS: dict[str, frozenset[str]] = {
    # public.events (0004) + dedup_hash (0009).
    "Event": frozenset(
        {
            "name",
            "event_type",
            "occurred_at",
            "affected_states",
            "magnitude",
            "depth_km",
            "status",
            "external_ids",
        }
    ),
    # public.acopio_centers (0004) + dedup_hash (0009).
    "AcopioCenter": frozenset(
        {
            "event_id",
            "name",
            "location",
            "confidence_score",
            "status",
            "needs",
            "last_verified_at",
            "managing_org",
            "contact_hmac",
            "contact_masked",
            "capacity",
            "current_load",
        }
    ),
}

# Campos que el job necesita del aporte, mapeados a los nombres del Record que
# consume la logica pura (pick_winner / canonical_from_winner). Las claves son
# los nombres REALES de columna de public.aportes (0001 + 0008); los valores son
# las claves del Record. raw_json -> payload es el unico rename semantico.
#   - id, created_at, source_id, entity_type, dedup_hash: 0001 / 0008.
#   - raw_json: 0001 (el contenido canonico que dejo el exporter).
# trust_tier / fetched_at / confidence_score se agregan aparte porque NO son
# columnas garantizadas de aportes (ver docstring del modulo y _read_optional).
_APORTE_FIELD_MAP: dict[str, str] = {
    "id": "id",
    "created_at": "created_at",
    "source_id": "source_id",
    "entity_type": "entity_type",
    "dedup_hash": "dedup_hash",
    "raw_json": "payload",
}

# Marcador para "aportes.trust_tier ausente en la respuesta" (columna no migrada
# aun). pick_winner lo trata como tier desconocido (rango 0, el mas bajo), asi que
# el desempate por fetched_at / confidence_score sigue siendo determinista.
_MISSING_TRUST_TIER = ""

# Status HTTP transitorios que ameritan reintento (espeja staging_exporter).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 4
_DEFAULT_TIMEOUT = 30.0
# Tamano de chunk para el PATCH de mark_consolidated: acota el largo de la URL
# (id=in.(...)) para no pasarse del limite practico de PostgREST.
_MARK_CHUNK_SIZE = 100


@dataclass(frozen=True)
class SupabaseConsolidationConfig:
    """Configuracion del adapter leida del entorno (patron #200).

    Espeja ``StagingConfig`` del exporter: HTTPS obligatorio y ``from_env``
    devuelve None (=> dry-run) si falta la config, sin abortar. El auth usa el
    par acordado en #200: ``publishable_key`` en el header ``apikey`` y
    ``consolidation_jwt`` en ``Authorization: Bearer`` (rol dedicado
    ``consolidation_job``, sin service_role). El JWT solo se LEE del entorno.
    """

    supabase_url: str
    publishable_key: str
    consolidation_jwt: str

    @classmethod
    def from_env(cls) -> "SupabaseConsolidationConfig | None":
        """Construye la config desde el entorno; None si falta o no es https.

        Lee ``SUPABASE_URL`` + ``SUPABASE_PUBLISHABLE_KEY`` (apikey) +
        ``SUPABASE_CONSOLIDATION_JWT`` (Bearer). Distingue el dry-run intencional
        (NINGUNA env seteada, dev local) de una config parcial en prod (alguna
        seteada, otra no): la primera loguea a INFO, la segunda a ERROR listando
        las faltantes. En ambos casos devuelve None (gatilla dry-run) sin abortar
        el job. NUNCA loguea el valor de la key ni del JWT.
        """
        values = {
            "SUPABASE_URL": os.getenv("SUPABASE_URL"),
            "SUPABASE_PUBLISHABLE_KEY": os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            "SUPABASE_CONSOLIDATION_JWT": os.getenv("SUPABASE_CONSOLIDATION_JWT"),
        }
        present = [k for k, v in values.items() if v]
        if not present:
            log.info(
                "supabase_adapter deshabilitado: ninguna SUPABASE_* seteada "
                "(dry-run intencional)"
            )
            return None
        if len(present) < len(values):
            missing = [k for k, v in values.items() if not v]
            log.error(
                "supabase_adapter mal configurado: faltan %s; entrando en dry-run",
                missing,
            )
            return None
        base_url = str(values["SUPABASE_URL"]).rstrip("/")
        # La key/JWT y (potencialmente) PII viajan en cada request. Sobre HTTP
        # plano serian interceptables (MITM); exigir HTTPS. Config errada =>
        # dry-run, nunca enviar a un endpoint inseguro.
        if not base_url.lower().startswith("https://"):
            log.error(
                "supabase_adapter: SUPABASE_URL debe ser https:// (recibido %r); "
                "entrando en dry-run para no enviar credenciales/PII en claro",
                base_url,
            )
            return None
        return cls(
            supabase_url=base_url,
            publishable_key=str(values["SUPABASE_PUBLISHABLE_KEY"]),
            consolidation_jwt=str(values["SUPABASE_CONSOLIDATION_JWT"]),
        )


def _aporte_to_record(row: dict[str, object]) -> Record:
    """Proyecta una fila de aportes al Record que consume la logica pura.

    Mapea las columnas reales (ver ``_APORTE_FIELD_MAP``) y adjunta los campos de
    desempate que la decision del equipo pide (trust_tier / fetched_at /
    confidence_score). Esos tres NO son columnas garantizadas de aportes; si la
    respuesta no los trae, se degrada de forma segura (trust_tier vacio => rango
    0; fetched_at / confidence_score None => desempate neutro). ``row.get`` ya
    devuelve None si la columna esta ausente o es null.
    """
    record: Record = {}
    for column, key in _APORTE_FIELD_MAP.items():
        if column in row:
            record[key] = row[column]
    # trust_tier: columna de aportes segun la decision del equipo (A=1..D=4). NO
    # existe todavia en el schema real (depende de migracion pendiente); si falta,
    # cae a _MISSING_TRUST_TIER y pick_winner lo trata como tier desconocido.
    trust_tier = row.get("trust_tier")
    record["trust_tier"] = trust_tier if trust_tier is not None else _MISSING_TRUST_TIER
    # Desempates de la decision del equipo. En el schema real fetched_at vive en
    # person_sources/raw_artifacts y confidence_score en persons/acopio_centers,
    # no en aportes; se leen de forma opcional para no romper si estan ausentes.
    record["fetched_at"] = row.get("fetched_at")
    record["confidence_score"] = row.get("confidence_score")
    return record


class SupabaseConsolidationAdapter:
    """Implementacion de `ConsolidationDataPort` sobre Supabase PostgREST.

    Cubre el flujo Event/AcopioCenter del job de #91. El camino Person (#92) tiene
    su propio adapter (`SupabasePersonDedupAdapter` en `consolidation_job`) y no se
    toca aqui.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @classmethod
    def from_config(
        cls, config: SupabaseConsolidationConfig
    ) -> "SupabaseConsolidationAdapter":
        """Crea el adapter con un httpx.Client propio a partir de la config."""
        client = httpx.Client(
            base_url=config.supabase_url,
            headers={
                # Patron #200: apikey = publishable key (identifica el proyecto);
                # Bearer = JWT del rol dedicado consolidation_job. NO service_role.
                "apikey": config.publishable_key,
                "Authorization": f"Bearer {config.consolidation_jwt}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
            # Ver docstring del modulo: no seguir redirects para no filtrar la key.
            follow_redirects=False,
        )
        return cls(client)

    # -- reintentos -----------------------------------------------------------

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Ejecuta una request con backoff en status transitorios / errores de red.

        Reintenta en 429/5xx y en TimeoutException/NetworkError usando
        ``backoff_delay``. Devuelve la ultima response; relanza la ultima
        excepcion de transporte si se agotan los reintentos sin response.
        """
        last_exc: httpx.HTTPError | None = None
        resp: httpx.Response | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._client.request(
                    method, path, params=params, json=json, headers=headers
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    delay = backoff_delay(attempt)
                    log.warning(
                        "%s en %s %s intento %d/%d; reintento en %.1fs",
                        type(exc).__name__, method, path, attempt, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                delay = backoff_delay(attempt)
                log.warning(
                    "HTTP %s en %s %s intento %d/%d; reintento en %.1fs",
                    resp.status_code, method, path, attempt, _MAX_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            return resp
        if resp is not None:
            return resp
        assert last_exc is not None
        raise last_exc

    # -- ConsolidationDataPort ------------------------------------------------

    def fetch_unconsolidated(self, entity_type: str, batch_size: int) -> list[Record]:
        """GET aportes NO consolidados de ``entity_type``, orden estable.

        Filtra ``consolidated_at=is.null`` y ``entity_type=eq.<slug>``, ordena por
        ``created_at.asc,id.asc`` (orden total estable => pick_winner determinista)
        y limita a ``batch_size``. El job es incremental: cada batch se relee tras
        marcar el anterior, asi que basta el filtro por consolidated_at (no hay
        cursor keyset explicito, igual que el flujo Event/Acopio del FakeAdapter).
        """
        slug, _ = _entity_tables(entity_type)
        resp = self._request_with_retry(
            "GET",
            _APORTES_PATH,
            params={
                "select": "*",
                "consolidated_at": "is.null",
                "entity_type": f"eq.{slug}",
                "order": "created_at.asc,id.asc",
                "limit": str(batch_size),
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise TypeError("respuesta de aportes debe ser una lista")
        return [_aporte_to_record(row) for row in payload if isinstance(row, dict)]

    def upsert_canonical(self, entity_type: str, record: Record) -> None:
        """POST upsert de la fila canonica del ganador (ON CONFLICT dedup_hash).

        Usa ``on_conflict=dedup_hash`` + ``Prefer: resolution=merge-duplicates``
        para el auto-merge atomico contra los indices UNIQUE de 0009
        (events_dedup_uniq / acopio_centers_dedup_uniq). ``return=minimal`` evita
        traer la fila de vuelta. Proyecta el payload SOLO sobre columnas canonicas
        reales (``_CANONICAL_COLUMNS``): nunca inventa columnas.
        """
        _, table_path = _entity_tables(entity_type)
        body = _project_canonical(entity_type, record)
        resp = self._request_with_retry(
            "POST",
            f"{table_path}?on_conflict=dedup_hash",
            json=body,
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
        )
        resp.raise_for_status()

    def mark_consolidated(self, aporte_ids: list[str]) -> None:
        """PATCH aportes ``aporte_ids`` -> consolidated_at=now (en chunks).

        Idempotente: re-marcar un aporte ya consolidado reescribe el timestamp sin
        error. Se chunkea para no pasar el limite practico de largo de URL de
        ``id=in.(...)``.
        """
        if not aporte_ids:
            return
        now = now_utc()
        for start in range(0, len(aporte_ids), _MARK_CHUNK_SIZE):
            chunk = aporte_ids[start : start + _MARK_CHUNK_SIZE]
            ids = ",".join(chunk)
            resp = self._request_with_retry(
                "PATCH",
                f"{_APORTES_PATH}?id=in.({ids})",
                json={"consolidated_at": now},
                headers={"Prefer": "return=minimal"},
            )
            resp.raise_for_status()

    # -- Person: candidatos por block keys para el scorer (#92) ---------------

    def fetch_person_candidates(
        self,
        block_keys: list[str],
        event_id: str,
    ) -> list[Record]:
        """GET aportes de tipo person cuyo block_keys solapa con los dados.

        Filtra entity_type='person', consolidated_at IS NULL, y block_keys
        contiene al menos una de las claves dadas (OR sobre cs PostgREST).
        Si block_keys esta vacio devuelve lista vacia sin red.
        """
        if not block_keys:
            return []
        cs_clauses = ",".join(f"block_keys.cs.{{{key}}}" for key in block_keys)
        resp = self._request_with_retry(
            "GET",
            _APORTES_PATH,
            params={
                "select": "*",
                "consolidated_at": "is.null",
                "entity_type": "eq.person",
                "or": f"({cs_clauses})",
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list):
            raise TypeError("respuesta de aportes debe ser una lista")
        return [_aporte_to_record(row) for row in payload if isinstance(row, dict)]

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SupabaseConsolidationAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _entity_tables(entity_type: str) -> tuple[str, str]:
    """(slug, table_path) para ``entity_type``; ValueError si no es auto-merge."""
    tables = _ENTITY_TABLES.get(entity_type)
    if tables is None:
        raise ValueError(
            f"entity_type {entity_type!r} no soportado por el adapter de "
            f"auto-merge; permitidos: {sorted(_ENTITY_TABLES)}"
        )
    return tables


def _project_canonical(entity_type: str, record: Record) -> dict[str, object]:
    """Proyecta el canonical del ganador sobre columnas reales + dedup_hash.

    Copia SOLO las claves que son columnas canonicas reales de la tabla destino
    (``_CANONICAL_COLUMNS``) y fuerza ``dedup_hash`` (el on_conflict). Descarta la
    metadata interna del job (dedup_version, winner_aporte_id) que NO son columnas
    del backend. Falla si el ganador no trae dedup_hash: sin el, el upsert atomico
    no puede deduplicar.
    """
    allowed = _CANONICAL_COLUMNS[entity_type]
    dedup_hash = record.get("dedup_hash")
    if not isinstance(dedup_hash, str) or not dedup_hash:
        raise ValueError("upsert_canonical requiere un dedup_hash no vacio")
    body: dict[str, object] = {
        key: value for key, value in record.items() if key in allowed
    }
    body["dedup_hash"] = dedup_hash
    return body
