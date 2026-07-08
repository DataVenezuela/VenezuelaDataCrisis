"""Silver materializer: proyecta ``aportes`` a sus filas tipadas 1:1.

Issue #257. Primera etapa del cron de consolidacion (``consolidate.yml``), antes
de la generacion de aristas. Proyeccion casi-pura, SIN decisiones de dedup:

  - ``aportes`` de tipo ``person``  -> una fila ``persons``        (PK = aportes.id).
  - ``aportes`` de tipo ``acopio``  -> una fila ``acopio_centers`` (PK = aportes.id).
  - ``events`` es un catalogo COMPARTIDO, no una proyeccion por-aporte: se siembra
    una fila desde el ``event_id`` de config (``project.event_id``) y esa fila debe
    existir antes de proyectar persons/acopio (FK ``*.event_id`` -> ``events``).
    Los aportes de tipo ``event`` NO se proyectan (Fase 1: ningun parser emite
    Event; ver ``.agents/CONTEXT.md``).

Idempotencia (criterio de aceptacion): re-correr no duplica ni churnea filas. El
alcance de #257 es proyectar cada aporte NO-proyectado (una fila por PK), asi que
el upsert usa ``resolution=ignore-duplicates`` (ON CONFLICT DO NOTHING): una PK ya
proyectada no se reescribe. ``return=representation`` devuelve solo las filas
realmente insertadas, con lo que el job cuenta cuantas proyecto de nuevo.

Limitacion conocida (fuera de alcance de #257): si un aporte con ``source_record_id``
estable se re-scrapea con contenido nuevo, ``staging_exporter`` hace
``merge-duplicates`` sobre su ``raw_json`` conservando el ``aporte.id``, pero la
fila tipada NO se re-proyecta (DO NOTHING la salta). Re-proyectar aportes mutados
es un follow-up (gated por ``content_hash``). Los aportes SIN ``source_record_id``
no sufren esto: contenido nuevo => ``external_id`` nuevo => ``aporte.id`` nuevo =>
proyeccion nueva.

Un batch rechazado por una sola fila mala (p.ej. un valor de enum que la BD no
acepta) se reintenta fila a fila (como ``StagingExporter._post_chunk``) para no
perder las filas buenas del lote.

Espeja el patron de ``ProvenanceExporter`` / ``StagingExporter``: reusa
``StagingConfig`` (mismas SUPABASE_*), el ``httpx.Client`` es inyectable para
tests sin red, entra en dry-run silencioso si falta la config, exige HTTPS y no
sigue redirects, y reintenta con backoff en status transitorios. El rol
``scraper_ingest`` necesita SELECT sobre ``aportes`` e INSERT sobre ``events`` /
``persons`` / ``acopio_centers``.

Seguridad: ``raw_json`` puede llevar PII (``full_name``, ``cedula_masked``). Este
job NUNCA la loguea: solo ids de aporte, conteos y status HTTP.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from scrapers.adapters._shared import backoff_delay
from scrapers.adapters.http_client import USER_AGENT
from scrapers.exporters.staging_exporter import StagingConfig

log = logging.getLogger(__name__)

_APORTES_PATH = "/rest/v1/aportes"
_EVENTS_UPSERT_PATH = "/rest/v1/events?on_conflict=event_id"
_PERSONS_UPSERT_PATH = "/rest/v1/persons?on_conflict=person_record_id"
_ACOPIO_UPSERT_PATH = "/rest/v1/acopio_centers?on_conflict=acopio_id"
# Cursor incremental (una sola fila): la frontera durable (created_at,id) del
# ultimo aporte proyectado. Deja de reescanear ~100k aportes desde offset 0 en
# cada corrida; solo pagina lo nuevo. Si la tabla no existe aun (DDL en el cuerpo
# del PR), se degrada a scan completo (Part A ya lo hace terminar dentro del cron).
_CURSOR_PATH = "/rest/v1/silver_materialize_state"
_CURSOR_UPSERT_PATH = "/rest/v1/silver_materialize_state?on_conflict=singleton"

# events.event_type es integer NOT NULL. Fase 1 tiene un unico evento real (el
# terremoto del 2026-06-24), asi que se siembra con este codigo fijo. No hay aun
# un mapeo string->int definido en el esquema; cuando exista, este valor migra.
_EARTHQUAKE_EVENT_TYPE = 1
_EVENT_DESCRIPTION = "Terremoto Venezuela 2026-06-24"

# Columnas de raw_json que se copian tal cual a cada tabla tipada (ver
# docs/schema.md). Se incluye solo la clave presente y no-nula; el resto queda en
# el DEFAULT de la BD. Proyeccion casi-pura: sin transformar valores.
_PERSON_FIELDS = (
    "full_name",
    "alternate_names",
    "cedula_hmac",
    "cedula_masked",
    "cedula_partial",
    "cedula_partial_pattern",
    "identity_kind",
    "pii_provenance",
    "name_truncated",
    "age_range",
    "sex",
    "is_minor",
    "last_known_location",
    "status",
    "trust_tier",
    "dedup_confidence",
    "confidence_score",
)
_ACOPIO_FIELDS = (
    "name",
    "location_text",
    "coordinates",
    "status",
    "trust_tier",
    "confidence_score",
    "managing_org",
    "contact_public",
    "current_load",
)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_POST_RETRIES = 4
_DEFAULT_BATCH_SIZE = 500
_MAX_PAGES = 100_000  # backstop anti-loop; el cron real nunca se acerca.


@dataclass
class MaterializeResult:
    """Resumen de una corrida del materializer (conteos, sin PII)."""

    persons_projected: int = 0
    acopio_projected: int = 0
    events_seeded: int = 0
    events_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _typed_payload(raw: dict[str, object], fields: tuple[str, ...]) -> dict[str, object]:
    """Copia las columnas presentes (no ``_*``, no None) de raw_json a la fila tipada."""
    return {
        k: raw[k]
        for k in fields
        if k in raw and raw[k] is not None and not k.startswith("_")
    }


def _row_event_id(raw: dict[str, object], default_event_id: str) -> str:
    """event_id del aporte, con fallback al event_id de config (ya sembrado)."""
    value = raw.get("event_id")
    return value if isinstance(value, str) and value else default_event_id


class SilverMaterializer:
    """Proyecta aportes a persons/acopio_centers y siembra el catalogo events."""

    def __init__(
        self,
        config: StagingConfig | None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.enabled = config is not None
        self._owns_client = client is None
        # Se vuelve True si la tabla del cursor no existe (DDL pendiente): a partir
        # de ahi no se reintenta persistir el cursor y se opera en scan completo.
        self._cursor_unavailable = False
        self._client: httpx.Client | None = client
        if self.enabled and config is not None and client is None:
            self._client = httpx.Client(
                base_url=config.supabase_url,
                headers={
                    "apikey": config.publishable_key,
                    "Authorization": f"Bearer {config.ingest_jwt}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
                follow_redirects=False,
            )

    # -- POST con retry -------------------------------------------------------

    def _post(
        self, path: str, payload: list[dict[str, object]]
    ) -> httpx.Response | None:
        """Upsert idempotente (ON CONFLICT DO NOTHING); devuelve las filas insertadas.

        NUNCA loguea el payload ni ``resp.text`` (pueden contener PII de raw_json).
        Devuelve None si se agotan los reintentos por error de red.
        """
        assert self._client is not None
        # ``missing=default``: cada aporte copia solo sus columnas presentes
        # (``_typed_payload``), asi que las filas de un batch NO comparten el mismo
        # set de claves. Sin este preferente PostgREST rechaza el lote entero con
        # 400 PGRST102 ("All object keys must match") y el fallback cae a fila-a-fila
        # (lento: satura el timeout del cron con 100k aportes). Con el, PostgREST
        # toma la union de claves y rellena las ausentes con el DEFAULT de cada
        # columna, aceptando el batch heterogeneo de una sola vez.
        headers = {
            "Prefer": "resolution=ignore-duplicates,return=representation,missing=default"
        }
        resp: httpx.Response | None = None
        for attempt in range(1, _MAX_POST_RETRIES + 1):
            try:
                resp = self._client.post(path, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < _MAX_POST_RETRIES:
                    delay = backoff_delay(attempt)
                    log.warning(
                        "%s en POST %s intento %d/%d — reintento en %.1fs",
                        type(exc).__name__, path, attempt, _MAX_POST_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue
                log.warning("POST %s agoto reintentos por error de red: %s", path, exc)
                return None
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_POST_RETRIES:
                delay = backoff_delay(attempt)
                log.warning(
                    "HTTP %s en POST %s intento %d/%d — reintento en %.1fs",
                    resp.status_code, path, attempt, _MAX_POST_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            return resp
        return resp

    @staticmethod
    def _inserted_count(resp: httpx.Response | None) -> int:
        """Cuenta las filas devueltas por return=representation (ON CONFLICT DO NOTHING)."""
        if resp is None:
            return 0
        try:
            data = resp.json()
        except ValueError:
            return 0
        return len(data) if isinstance(data, list) else 0

    # -- fetch aportes --------------------------------------------------------

    def _fetch_aportes_page(
        self, limit: int, cursor: tuple[str, str] | None
    ) -> list[dict[str, object]]:
        """Trae una pagina de aportes con paginado keyset por (created_at, id).

        Con ``cursor`` (la frontera durable ya proyectada) trae solo
        ``(created_at, id) > cursor``: no reescanea lo ya proyectado. Sin cursor
        (primera corrida o cursor no disponible) arranca desde el principio.
        """
        assert self._client is not None
        params: dict[str, object] = {
            "select": "id,entity_type,raw_json,created_at",
            # Desempate por id: created_at NO es unico (un batch de scrape commitea
            # muchas filas con el mismo timestamp). Sin clave estable el paginado
            # podria saltarse o repetir una fila en el borde de pagina.
            "order": "created_at.asc,id.asc",
            "limit": limit,
        }
        if cursor is not None:
            ts, cid = cursor
            # Keyset estricto (created_at,id) > cursor. El cursor apunta SIEMPRE a
            # una fila ya proyectada (solo se persiste tras confirmar la pagina),
            # asi que el '>' estricto nunca se salta un aporte sin proyectar.
            params["or"] = f"(created_at.gt.{ts},and(created_at.eq.{ts},id.gt.{cid}))"
        resp = self._client.get(_APORTES_PATH, params=params)
        if resp.status_code in (401, 403):
            raise PermissionError(
                f"fetch aportes: sin permiso (status {resp.status_code}); verificar "
                "SUPABASE_INGEST_JWT y el grant SELECT del rol scraper_ingest sobre aportes"
            )
        if resp.status_code != 200:
            log.warning("fetch aportes: status inesperado %s (cursor=%s)", resp.status_code, cursor)
            return []
        try:
            rows = resp.json()
        except ValueError:
            log.warning("fetch aportes: cuerpo 200 no es JSON valido (cursor=%s)", cursor)
            return []
        return rows if isinstance(rows, list) else []

    # -- cursor incremental ---------------------------------------------------

    def _read_cursor(self) -> tuple[str, str] | None:
        """Lee la frontera durable (created_at, id); None => scan completo.

        Se degrada a None (scan completo, comportamiento previo) ante cualquier
        problema: tabla ausente (DDL pendiente), sin permiso, o error de red.
        """
        assert self._client is not None
        try:
            resp = self._client.get(
                _CURSOR_PATH,
                params={"select": "cursor_created_at,cursor_id", "limit": 1},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning("materializer: no se pudo leer el cursor (%s); scan completo", type(exc).__name__)
            return None
        if resp.status_code in (404, 406):
            log.info(
                "materializer: tabla de cursor ausente (status %s); scan completo "
                "(aplicar el DDL del PR para habilitar el paginado incremental)",
                resp.status_code,
            )
            self._cursor_unavailable = True
            return None
        if resp.status_code != 200:
            log.warning("materializer: lectura de cursor status %s; scan completo", resp.status_code)
            return None
        try:
            rows = resp.json()
        except ValueError:
            return None
        if isinstance(rows, list) and rows:
            ca = rows[0].get("cursor_created_at")
            cid = rows[0].get("cursor_id")
            if isinstance(ca, str) and ca and cid:
                return (ca, str(cid))
        return None

    def _write_cursor(self, created_at: str, cursor_id: str) -> bool:
        """Persiste la frontera durable (upsert de la fila unica). Best-effort.

        No es PII (timestamp + UUID de aporte). Si la tabla no existe, lo marca y
        no vuelve a intentar en esta corrida. Un fallo aqui no aborta el job: las
        filas ya estan proyectadas; la proxima corrida re-deriva desde el cursor
        persistido (o rehace, idempotente por PK)."""
        if self._cursor_unavailable:
            return False
        assert self._client is not None
        payload = [{"singleton": True, "cursor_created_at": created_at, "cursor_id": cursor_id}]
        try:
            resp = self._client.post(
                _CURSOR_UPSERT_PATH,
                json=payload,
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning("materializer: POST cursor error de red (%s)", type(exc).__name__)
            return False
        if resp.status_code in (404, 406):
            log.info(
                "materializer: tabla de cursor ausente; sin paginado incremental "
                "(aplicar el DDL del PR)"
            )
            self._cursor_unavailable = True
            return False
        if resp.status_code not in (200, 201, 204):
            log.warning("materializer: no se pudo persistir el cursor (status %s)", resp.status_code)
            return False
        return True

    @staticmethod
    def _page_cursor(page: list[dict[str, object]]) -> tuple[str, str] | None:
        """(created_at, id) de la ultima fila de la pagina (orden asc); None si falta."""
        if not page:
            return None
        last = page[-1]
        ca = last.get("created_at")
        pid = last.get("id")
        if isinstance(ca, str) and ca and pid:
            return (ca, str(pid))
        return None

    # -- seed events ----------------------------------------------------------

    def _seed_event(self, event_id: str, seeded: set[str], result: MaterializeResult) -> bool:
        """Siembra (idempotente) una fila de catalogo ``events``. True si la fila existe."""
        if event_id in seeded:
            return True
        resp = self._post(
            _EVENTS_UPSERT_PATH,
            [{
                "event_id": event_id,
                "event_type": _EARTHQUAKE_EVENT_TYPE,
                "description": _EVENT_DESCRIPTION,
            }],
        )
        if resp is None or resp.status_code not in (200, 201):
            result.errors.append(
                f"seed events {event_id}: status {getattr(resp, 'status_code', 'n/a')}"
            )
            return False
        seeded.add(event_id)
        result.events_seeded += self._inserted_count(resp)
        return True

    # -- materialize ----------------------------------------------------------

    def materialize(
        self, *, event_id: str, batch_size: int = _DEFAULT_BATCH_SIZE
    ) -> MaterializeResult:
        """Proyecta todos los aportes a sus filas tipadas 1:1; siembra el catalogo.

        ``event_id`` es la constante de config (``project.event_id``): siembra la
        fila del catalogo antes de proyectar. Ademas siembra (defensivo) cualquier
        ``event_id`` referenciado por un aporte, para no romper la FK si difiere.
        Idempotente por PK: re-proyectar un aporte ya materializado es no-op.

        Paginado keyset con cursor durable: arranca desde la frontera persistida
        (``_read_cursor``) y solo avanza el cursor persistido tras confirmar cada
        pagina de forma contigua. Una pagina que falla a nivel de batch (error
        transitorio de red) NO avanza el cursor durable: la proxima corrida la
        reintenta. Asi no se reescanean ~100k aportes desde el principio en cada
        corrida ni se salta ninguno por una pagina a medio commitear.
        """
        result = MaterializeResult()
        if not self.enabled or self._client is None:
            log.info("materializer deshabilitado (dry-run): no se proyecta nada")
            return result

        seeded: set[str] = set()
        # La fila de catalogo de config debe existir antes que cualquier proyeccion.
        self._seed_event(event_id, seeded, result)

        size = max(1, batch_size)
        cursor = self._read_cursor()
        # Solo se persiste el cursor mientras las paginas confirmen en orden
        # contiguo; tras la primera pagina fallida, el cursor durable se congela
        # (aunque el cursor en memoria siga avanzando para intentar mas esta corrida).
        contiguous = True
        try:
            for page_num in range(_MAX_PAGES):
                page = self._fetch_aportes_page(size, cursor)
                if not page:
                    break
                page_ok = self._project_page(page, event_id, seeded, result)
                last = self._page_cursor(page)
                if last is None:
                    # Sin (created_at,id) utilizable no hay clave keyset estable;
                    # cortamos para no arriesgar un salto. La proxima corrida reintenta.
                    log.warning("materializer: pagina sin created_at/id; se corta el paginado")
                    break
                if not page_ok:
                    contiguous = False
                elif contiguous:
                    self._write_cursor(*last)  # avanza la frontera durable
                cursor = last  # el cursor en memoria avanza siempre
                if len(page) < size:
                    break
                if page_num == _MAX_PAGES - 1:
                    log.warning(
                        "materializer: se alcanzo el backstop de %d paginas; "
                        "puede quedar backlog sin proyectar (cursor=%s)",
                        _MAX_PAGES, cursor,
                    )
        except PermissionError as exc:
            log.error("%s", exc)
            result.errors.append(str(exc))
        return result

    def _project_page(
        self,
        page: list[dict[str, object]],
        default_event_id: str,
        seeded: set[str],
        result: MaterializeResult,
    ) -> bool:
        """Proyecta una pagina. Devuelve True si commiteo por completo.

        True => cada aporte de la pagina fue atendido de forma definitiva (exito o
        fila-poison permanente): el cursor puede avanzar sobre esta pagina. False =>
        hubo un fallo transitorio (red / seed del catalogo) que dejo aportes sin
        proyectar: el cursor durable NO debe pasar de aqui para reintentar luego.
        """
        persons: list[dict[str, object]] = []
        acopios: list[dict[str, object]] = []
        ok = True
        for aporte in page:
            entity_type = str(aporte.get("entity_type") or "")
            raw = aporte.get("raw_json")
            aporte_id = aporte.get("id")
            if not isinstance(raw, dict) or not aporte_id:
                # Aporte malformado: poison permanente, no bloquea el avance.
                result.errors.append(f"aporte sin id/raw_json valido: {aporte_id!r}")
                continue
            if entity_type == "event":
                # Catalogo compartido, no proyeccion por-aporte (Fase 1).
                result.events_skipped += 1
                continue
            row_event_id = raw.get("event_id")
            if isinstance(row_event_id, str) and row_event_id:
                # FK-safe: asegura la fila de catalogo antes de proyectar. Un fallo
                # de seed es transitorio: no proyectamos este aporte y no avanzamos.
                if not self._seed_event(row_event_id, seeded, result):
                    ok = False
                    continue
            if entity_type == "person":
                persons.append(self._person_row(str(aporte_id), raw, default_event_id))
            elif entity_type == "acopio":
                acopios.append(self._acopio_row(str(aporte_id), raw, default_event_id))
            else:
                # Tipo desconocido: poison permanente, no bloquea el avance.
                result.errors.append(f"entity_type desconocido: {entity_type!r}")

        if persons:
            ok = self._upsert_rows(_PERSONS_UPSERT_PATH, persons, "persons", result) and ok
        if acopios:
            ok = self._upsert_rows(_ACOPIO_UPSERT_PATH, acopios, "acopio", result) and ok
        return ok

    def _upsert_rows(
        self,
        path: str,
        rows: list[dict[str, object]],
        kind: str,
        result: MaterializeResult,
    ) -> bool:
        """Upserta un lote; si el batch es rechazado, reintenta fila a fila.

        Aisla la fila mala (p.ej. un valor de enum que la BD rechaza) para no
        perder las filas buenas del lote, como ``StagingExporter._post_chunk``.
        NUNCA loguea el payload (PII); la PK (``*_record_id``/``acopio_id``) es un
        UUID de aporte, no PII, asi que si se puede identificar la fila fallida.

        Devuelve True si cada fila fue atendida de forma definitiva por la BD
        (insertada, ya-presente, o rechazo permanente tipo enum). Devuelve False
        solo ante un fallo transitorio de red (``_post`` -> None) que impidio
        atender alguna fila: el llamador no debe avanzar el cursor durable.
        """
        resp = self._post(path, rows)
        if resp is not None and resp.status_code in (200, 201):
            self._add_projected(kind, self._inserted_count(resp), result)
            return True
        if len(rows) > 1:
            # El batch fallo: rechazo HTTP (resp con status de error) o error de red
            # tras agotar reintentos (resp None). Ambos casos activan el fallback
            # fila a fila para aislar la fila mala sin descartar todo el lote; un
            # error de red no debe saltarse el retry individual.
            log.warning(
                "proyeccion %s: batch de %d rechazado (status %s); reintentando fila a fila",
                kind, len(rows), getattr(resp, "status_code", "n/a"),
            )
            ok = True
            for row in rows:
                r = self._post(path, [row])
                if r is not None and r.status_code in (200, 201):
                    self._add_projected(kind, self._inserted_count(r), result)
                else:
                    pk = row.get("person_record_id") or row.get("acopio_id")
                    log.warning(
                        "proyeccion %s fila fallo: pk=%s status=%s",
                        kind, pk, getattr(r, "status_code", "n/a"),
                    )
                    result.errors.append(
                        f"POST {path}: status {getattr(r, 'status_code', 'n/a')} (pk={pk})"
                    )
                    # r is None => red transitoria (reintentar en la proxima corrida);
                    # r con status de error => rechazo permanente (no bloquea el cursor).
                    if r is None:
                        ok = False
            return ok
        log.warning(
            "proyeccion %s fallo: %d filas, status %s",
            kind, len(rows), getattr(resp, "status_code", "n/a"),
        )
        result.errors.append(
            f"POST {path}: status {getattr(resp, 'status_code', 'n/a')} ({len(rows)} filas)"
        )
        # Fila unica: rechazo definitivo de la BD (resp != None) es poison permanente
        # y no debe bloquear el cursor; solo un fallo de red (resp None) lo bloquea.
        return resp is not None

    @staticmethod
    def _add_projected(kind: str, inserted: int, result: MaterializeResult) -> None:
        if kind == "persons":
            result.persons_projected += inserted
        else:
            result.acopio_projected += inserted

    @staticmethod
    def _person_row(
        aporte_id: str, raw: dict[str, object], default_event_id: str
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "person_record_id": aporte_id,
            "entity_type": "person",
            # Cae al event_id de config (ya sembrado) si el aporte no lo trae, para
            # no dejar la fila tipada sin referencia al catalogo. En la practica
            # raw_json siempre lo lleva (campo NOT NULL validado del modelo).
            "event_id": _row_event_id(raw, default_event_id),
        }
        row.update(_typed_payload(raw, _PERSON_FIELDS))
        return row

    @staticmethod
    def _acopio_row(
        aporte_id: str, raw: dict[str, object], default_event_id: str
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "acopio_id": aporte_id,
            "entity_type": "acopio",
            "event_id": _row_event_id(raw, default_event_id),
        }
        row.update(_typed_payload(raw, _ACOPIO_FIELDS))
        return row

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> "SilverMaterializer":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["SilverMaterializer", "MaterializeResult"]
