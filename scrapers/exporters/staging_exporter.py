"""Staging exporter: upsert directo a Supabase via PostgREST.

Reemplaza el export via Vercel (/api/aportes) por escritura directa a
Supabase. Auth via custom JWT firmado con rol ``scraper_ingest``
(``Authorization: Bearer``) + publishable key en header ``apikey``.
Cada batch de registros (post-PII, post-score, post-minor-protection)
se upserta con ``Prefer: resolution=merge-duplicates``; la idempotencia
por external_id absorbe re-envios sin duplicar.

El payload es el ``aportes`` canonico (issue #256): emite ``artifact_id``
(FK NOT NULL -> raw_artifacts, stampado por el pipeline como ``_artifact_id``
tras registrar la pagina en Bronze via ``ProvenanceExporter``) y ya NO emite
``run_id``/``scraper_id``/``source_url``/``parser_version`` (la procedencia de
corrida y la URL viven en ``raw_artifacts``).

Sin red real en tests: el httpx.Client es inyectable via el parametro
``client`` del constructor (los tests pasan httpx.Client(transport=...)).
Si faltan las env vars SUPABASE_*, el exporter entra en dry-run silencioso:
no abre cliente, calcula payloads para validarlos, loguea a INFO lo que
enviaria, y devuelve ExportResult vacio.

El envio concurrente de batches se activa pasando ``max_concurrent_posts > 1``;
usa ``concurrent.futures.ThreadPoolExecutor``. El watermark avanza si no hubo
errores de parseo/PII/enriquecimiento previos al export y se envio al menos un
registro (sent > 0); puede avanzar aunque algun batch de aportes haya fallado
(esos errores quedan en result.errors, no se pliegan a source_errors).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from scrapers.adapters._shared import retry_post, sha256_hex
from scrapers.adapters.http_client import USER_AGENT
from scrapers.dedup import specs

log = logging.getLogger(__name__)

_DEFAULT_WATERMARK = "1970-01-01T00:00:00Z"
_APORTES_UPSERT_PATH = "/rest/v1/aportes?on_conflict=source_id,external_id"
_SOURCES_PATH = "/rest/v1/sources"

# Placeholder de artifact_id para dry-run: el aporte canonico exige artifact_id
# NOT NULL, pero en dry-run no hay red ni Bronze, asi que se usa este valor solo
# para poder construir el payload de log. NUNCA viaja a la DB (dry-run no POSTea).
_DRYRUN_ARTIFACT_ID = "00000000-0000-0000-0000-000000000000"

_WATERMARK_SAFETY_MARGIN = timedelta(minutes=5)
_FETCHED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_DEFAULT_BATCH_SIZE = 100


@dataclass(frozen=True)
class StagingConfig:
    """Configuracion del exporter leida del entorno.

    El source_id NO vive aqui: una sola corrida del pipeline procesa
    multiples fuentes (ver run_pipeline._run_source), asi que cada llamada a
    get_watermark/export_source recibe su propio source_id (source.id, el UUID
    de la tabla sources).
    """

    supabase_url: str
    publishable_key: str
    ingest_jwt: str

    @classmethod
    def from_env(cls) -> StagingConfig | None:
        """Construye la config desde SUPABASE_*; None si falta alguna.

        Distingue el dry-run intencional (NINGUNA SUPABASE_* seteada, dev local)
        de una config parcial en prod (algunas seteadas, otras no): la primera
        loguea a INFO, la segunda a ERROR listando las faltantes. En ambos casos
        devuelve None (gatilla el dry-run) sin abortar el pipeline.
        """
        values = {
            "SUPABASE_URL": os.getenv("SUPABASE_URL"),
            "SUPABASE_PUBLISHABLE_KEY": os.getenv("SUPABASE_PUBLISHABLE_KEY"),
            "SUPABASE_INGEST_JWT": os.getenv("SUPABASE_INGEST_JWT"),
        }
        present = [k for k, v in values.items() if v]
        if not present:
            log.info(
                "staging_exporter deshabilitado: ninguna SUPABASE_* seteada "
                "(dry-run intencional)"
            )
            return None
        if len(present) < len(values):
            missing = [k for k, v in values.items() if not v]
            log.error(
                "staging_exporter mal configurado: faltan %s; entrando en dry-run",
                missing,
            )
            return None
        supabase_url = str(values["SUPABASE_URL"]).rstrip("/")
        if not supabase_url.lower().startswith("https://"):
            log.error(
                "staging_exporter: SUPABASE_URL debe ser https:// (recibido %r); "
                "entrando en dry-run para no enviar credenciales/PII en claro",
                supabase_url,
            )
            return None
        return cls(
            supabase_url=supabase_url,
            publishable_key=str(values["SUPABASE_PUBLISHABLE_KEY"]),
            ingest_jwt=str(values["SUPABASE_INGEST_JWT"]),
        )


@dataclass
class ExportResult:
    """Resultado agregado de exportar los records de una fuente.

    ``duplicates`` siempre es 0: con ``resolution=merge-duplicates``
    PostgREST nunca devuelve 409. El contador se conserva para no romper
    el contrato de ``run_pipeline`` pero ya no se incrementa en el nuevo
    esquema de upsert.
    """

    sent: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _content_hash(body: dict[str, object]) -> str:
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_hex(raw.encode("utf-8"))


def _apply_safety_margin(watermark_at: str) -> str:
    try:
        dt = datetime.strptime(watermark_at, _FETCHED_AT_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("watermark con formato inesperado, sin margen de seguridad: %s", watermark_at)
        return watermark_at
    return (dt - _WATERMARK_SAFETY_MARGIN).strftime(_FETCHED_AT_FORMAT)


def _aporte_external_id(
    entity_type: str,
    source_id: str,
    source_record_id: str | None,
    content_hash: str,
) -> str:
    """Identidad del aporte = registro-fuente, nunca su contenido ni identidad real.

    Por el modelo medallion (``.agents/CONTEXT.md``: "silver nunca colapsa"): dos
    registros DISTINTOS de una misma fuente que compartan cedula, fingerprint o
    incluso nombre NO deben fundirse en un solo aporte al ingerir. La
    deduplicacion vive en los edges (``dedup_candidates``) y en gold, jamas en
    silver.

    ``external_id`` es por-registro-de-fuente para TODO tipo de entidad:
    ``sha256(entity | source | source_record_id)`` cuando la fuente da un id de
    registro nativo, o ``sha256(entity | source | content_hash)`` cuando no lo da.
    Las senales de dedup (``dedup_hash``, ``deterministic_id``, fingerprint,
    fonetica) siguen viajando por ``block_keys`` y ``raw_json``: alimentan el
    linkeo, ya no la identidad del aporte.
    """
    basis = source_record_id if source_record_id else content_hash
    seed = f"{entity_type.lower()}|{source_id}|{basis}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class StagingExporter:
    """Upserta aportes a Supabase via PostgREST y avanza el watermark de la fuente."""

    def __init__(
        self,
        config: StagingConfig | None,
        *,
        client: httpx.Client | None = None,
        run_id: str | None = None,
    ) -> None:
        self.config = config
        self.enabled = config is not None
        # run_id se conserva como handle de correlacion de la corrida (compartido
        # con QuarantineExporter en run_pipeline); ya NO se emite en el payload de
        # aportes: la procedencia de corrida vive en raw_artifacts.run_id, al que
        # el aporte llega via artifact_id (issue #256).
        self.run_id = run_id or str(uuid.uuid4())
        self._owns_client = client is None
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

    # -- payload --------------------------------------------------------------

    def _build_payload(self, rec: dict[str, object], source_id: str) -> dict[str, object]:
        entity_type = str(rec.get("_entity_type") or "Person")
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        spec = specs.spec_for_entity_type(entity_type)

        # Identidad del aporte = registro-fuente, uniforme para todo tipo. El
        # fingerprint/deterministic_id sigue en dedup_hash + block_keys para el
        # linkeo en gold, pero ya no keyea aportes (ver _aporte_external_id).
        content_hash = _content_hash(clean)
        src_rec_id = _opt_str(rec.get("_source_record_id"))
        external_id = _aporte_external_id(entity_type, source_id, src_rec_id, content_hash)
        dedup_hash = specs.dedup_key(rec, entity_type)

        # ``source_id`` ES el UUID de la tabla sources (source.id): el config lo
        # trae resuelto, asi que el aporte ya no necesita un GET slug -> id.
        # artifact_id (FK NOT NULL -> raw_artifacts) lo stampa el pipeline como
        # meta-campo _artifact_id, tras registrar la pagina en Bronze
        # (ProvenanceExporter). Sin el no puede existir el aporte canonico: en
        # modo enabled se falla cerrado (el registro no se envia y no cuenta como
        # sent, asi el watermark no avanza); en dry-run se usa un placeholder solo
        # para poder construir el payload de log. La corrida y la URL de origen
        # ahora viven en raw_artifacts (via artifact_id), ya no en el aporte:
        # por eso se dejaron de emitir run_id/scraper_id/source_url/parser_version
        # (issue #256).
        artifact_id = _opt_str(rec.get("_artifact_id"))
        if artifact_id is None:
            if self.enabled:
                raise ValueError(
                    "falta _artifact_id: el aporte canonico exige artifact_id "
                    "NOT NULL (raw_artifacts); el pipeline debe registrar la "
                    "pagina en Bronze antes de exportar el aporte"
                )
            artifact_id = _DRYRUN_ARTIFACT_ID
        payload: dict[str, object] = {
            "entity_type": _entity_type_slug(entity_type),
            "external_id": external_id,
            "dedup_version": spec.version,
            "block_keys": specs.block_keys(rec, entity_type),
            "content_hash": content_hash,
            "source_id": source_id,
            "artifact_id": artifact_id,
            "raw_json": clean,
        }
        for key, value in (
            ("dedup_hash", dedup_hash),
            ("source_record_id", src_rec_id),
            ("source_url", _opt_str(rec.get("_source_url"))),
            ("parser_version", _opt_str(rec.get("_parser_version"))),
            ("normalizer_version", _opt_str(rec.get("_normalizer_version"))),
        ):
            if value is not None:
                payload[key] = value
        return payload

    # -- watermark ------------------------------------------------------------

    def get_watermark(self, source_id: str) -> str:
        # El watermark vive en la propia fila de la fuente (sources.watermark_at),
        # ya no en una tabla aparte: se lee filtrando por source_id (UUID).
        if not self.enabled or self._client is None:
            return _DEFAULT_WATERMARK
        try:
            resp = self._client.get(
                _SOURCES_PATH,
                params={"source_id": f"eq.{source_id}", "select": "watermark_at"},
            )
            if resp.status_code in (401, 403):
                raise PermissionError(
                    f"get_watermark {source_id}: sin permiso (status {resp.status_code}); "
                    "verificar SUPABASE_INGEST_JWT y grants del rol scraper_ingest"
                )
            if resp.status_code == 200:
                rows = resp.json()
                if isinstance(rows, list) and len(rows) > 0:
                    return str(rows[0].get("watermark_at") or _DEFAULT_WATERMARK)
            else:
                log.warning(
                    "get_watermark %s: status %s body=%r",
                    source_id, resp.status_code, resp.text[:300],
                )
            return _DEFAULT_WATERMARK
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            log.warning("no se pudo leer watermark de %s: %s", source_id, exc)
            response = getattr(exc, "response", None)
            if response is not None:
                log.warning(
                    "respuesta HTTP de %s: status=%s body=%r",
                    source_id,
                    response.status_code,
                    response.text[:300],
                )
            return _DEFAULT_WATERMARK

    def _set_watermark(self, source_id: str, watermark_at: str) -> bool:
        assert self._client is not None
        # PATCH sobre la fila existente de la fuente (no upsert): la fila de sources
        # ya existe (la sembro el maintainer). return=representation -> el body trae
        # las filas afectadas. Un PATCH que no matchea ninguna fila (p.ej. un
        # source_id que no es un UUID valido) devuelve 200 con un array vacio; ese
        # caso NO es exito: el watermark no avanzo y debe reportarse, no tragarse.
        resp = self._post_with_retry(
            f"{_SOURCES_PATH}?source_id=eq.{source_id}",
            {"watermark_at": watermark_at},
            method="PATCH",
            headers={"Prefer": "return=representation"},
        )
        if resp is None:
            return False
        if resp.status_code in (401, 403):
            raise PermissionError(
                f"_set_watermark {source_id}: sin permiso (status {resp.status_code}); "
                "verificar SUPABASE_INGEST_JWT y grants del rol scraper_ingest"
            )
        if resp.status_code not in (200, 201):
            return False
        try:
            updated = resp.json()
        except ValueError:
            return False
        # Exito solo si realmente se actualizo >=1 fila.
        return isinstance(updated, list) and len(updated) > 0

    def _post_with_retry(
        self,
        path: str,
        payload: list[dict[str, object]] | dict[str, object],
        *,
        method: str = "POST",
        timeout: httpx.Timeout | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response | None:
        assert self._client is not None
        return retry_post(
            self._client, path, payload,
            method=method, timeout=timeout, headers=headers, log=log,
        )

    def advance_watermark(
        self, source_id: str, source_fetched_ats: list[str], source_errors: bool, sent: int
    ) -> str | None:
        """Avanza el watermark si se envió ≥1 registro sin errores pre-export.

        Bloquea solo si ``source_errors`` (parse/PII/enriquecimiento) están
        presentes o si ``sent == 0``; un fallo del PATCH se reporta como error
        pero no lanza. Usado tanto por ``export_source`` como por el loop de
        streaming en ``_run_source``.
        """
        if source_errors or not source_fetched_ats or sent == 0:
            return None
        new_watermark = _apply_safety_margin(max(source_fetched_ats))
        try:
            if not self._set_watermark(source_id, new_watermark):
                return "no se pudo actualizar el watermark"
        except PermissionError as exc:
            return str(exc)
        return None

    # -- export ---------------------------------------------------------------

    def export_batch(
        self,
        records: list[dict[str, object]],
        *,
        source_id: str,
        batch_size: int | None = None,
        max_concurrent_posts: int | None = None,
    ) -> ExportResult:
        """Exporta un lote de records a Supabase sin avanzar el watermark.

        Llamado por el loop de streaming en ``_run_source`` (una página a la vez).
        El caller acumula los resultados y llama ``advance_watermark`` al final.
        ``batch_size`` controla el tamaño del lote (default: _DEFAULT_BATCH_SIZE).
        ``max_concurrent_posts`` controla cuántos batches enviar en paralelo
        (default 1 = secuencial). Usa ``ThreadPoolExecutor`` internamente.
        """
        result = ExportResult()
        size = batch_size or _DEFAULT_BATCH_SIZE

        if not self.enabled or self._client is None or self.config is None:
            for rec in records:
                try:
                    payload = self._build_payload(rec, source_id)
                except ValueError as exc:
                    log.warning("DRY-RUN saltando registro: %s", exc)
                    continue
                log.info(
                    "DRY-RUN staging_exporter: enviaria entity_type=%s external_id=%s",
                    payload["entity_type"],
                    payload["external_id"],
                )
            return result

        payloads: list[dict[str, object]] = []
        for rec in records:
            try:
                payloads.append(self._build_payload(rec, source_id))
            except ValueError as exc:
                result.errors.append(str(exc))
        if not payloads:
            return result

        chunks = [payloads[i : i + size] for i in range(0, len(payloads), size)]
        _batch_timeout = httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)
        batch_headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}

        workers = max(1, max_concurrent_posts or 1)

        if max_concurrent_posts is None or max_concurrent_posts <= 1:
            log.info(
                "[%s] exportando %d batches secuencial (max_concurrent_posts=%s); ",
                source_id, len(chunks), max_concurrent_posts,
            )
            for chunk in chunks:
                _sent, _errors = self._post_chunk(chunk, _batch_timeout, batch_headers)
                result.sent += _sent
                result.errors.extend(_errors)

        else:
            log.info(
                "export_source %s: enviando %d batches con %d workers concurrentes",
                source_id, len(chunks), workers,
            )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(self._post_chunk, chunk, _batch_timeout, batch_headers) for chunk in chunks]
                for future in as_completed(futures):
                    _sent, _errors = future.result()
                    result.sent += _sent
                    result.errors.extend(_errors)

        return result

    def _post_chunk(
        self,
        chunk: list[dict[str, object]],
        timeout: httpx.Timeout,
        headers: dict[str, str],
    ) -> tuple[int, list[str]]:
        """POSTea un solo chunk; reintenta registro a registro si el batch es rechazado.

        Returns
        -------
        Tuple de (sent, errors) para este chunk.
        """
        sent = 0
        errors: list[str] = []
        try:
            resp = self._post_with_retry(
                _APORTES_UPSERT_PATH, chunk, timeout=timeout, headers=headers,
            )
            if resp is None:
                errors.append(f"POST {_APORTES_UPSERT_PATH} batch fallo: reintentos agotados")
                return sent, errors

            if resp.status_code in (200, 201):
                sent += len(chunk)
                return sent, errors

            if len(chunk) > 1:
                log.warning(
                    "POST %s status=%s body=%s en batch de %d — reintentando individualmente",
                    _APORTES_UPSERT_PATH, resp.status_code, resp.text[:500], len(chunk),
                )
                for single in chunk:
                    r = self._post_with_retry(
                        _APORTES_UPSERT_PATH, [single],
                        timeout=timeout, headers=headers,
                    )
                    if r is None:
                        errors.append("POST individual fallo: reintentos agotados")
                        continue
                    if r.status_code in (200, 201):
                        sent += 1
                    else:
                        log.warning(
                            "POST %s status=%s external_id=%s body=%s",
                            _APORTES_UPSERT_PATH, r.status_code,
                            single.get("external_id"), r.text[:300],
                        )
                        errors.append(
                            f"{_APORTES_UPSERT_PATH} status {r.status_code} "
                            f"(external_id={single.get('external_id')})"
                        )
                return sent, errors

            log.warning(
                "POST %s status=%s body=%s",
                _APORTES_UPSERT_PATH,
                resp.status_code,
                resp.text[:300],
            )
            errors.append(
                f"{_APORTES_UPSERT_PATH} status {resp.status_code} "
                f"(external_id={chunk[0].get('external_id')})"
            )
        except Exception as exc:
            log.error("_post_chunk: error inesperado no capturado: %s", exc)
            errors.append(f"POST {_APORTES_UPSERT_PATH} error inesperado: {exc}")

        return sent, errors

    def export_source(
        self,
        records: list[dict[str, object]],
        *,
        source_id: str,
        source_fetched_ats: list[str],
        source_errors: list[str] | None = None,
        batch_size: int | None = None,
        max_concurrent_posts: int | None = None,
    ) -> ExportResult:
        """Exporta los records de ``source_id``; avanza su watermark si todo OK.

        ``source_errors`` son errores previos de la fuente (parse, PII,
        enriquecimiento y el fail-closed de proteccion de menores). Si no estan
        vacios, o si hubo errores de insert, el watermark NO avanza.
        ``max_concurrent_posts`` controla cuántos batches enviar en paralelo.
        """
        result = self.export_batch(
            records,
            source_id=source_id,
            batch_size=batch_size,
            max_concurrent_posts=max_concurrent_posts,
        )
        if not self.enabled:
            return result
        watermark_err = self.advance_watermark(
            source_id, source_fetched_ats, bool(source_errors), result.sent
        )
        if watermark_err is not None:
            result.errors.append(watermark_err)
        return result

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> StagingExporter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _opt_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


_ENTITY_TYPE_SLUGS = {
    "Event": "event",
    "AcopioCenter": "acopio",
    "Person": "person",
}

def _entity_type_slug(entity_type: str) -> str:
    return _ENTITY_TYPE_SLUGS.get(entity_type, entity_type.lower())
