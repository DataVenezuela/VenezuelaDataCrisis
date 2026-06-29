"""Staging exporter: POST de aportes a /api/aportes de dataVenezuela.

Reemplaza el export JSONL en disco. Cada record sanitizado (post-PII,
post-score, post-minor-protection) se manda como un aporte idempotente:
el external_id determinista permite al backend hacer upsert sin duplicar.

Sin red real en tests: el httpx.Client es inyectable via el parametro
``client`` del constructor (los tests pasan httpx.Client(transport=...)).
Si faltan las env vars STAGING_*, el exporter entra en dry-run silencioso:
no abre cliente, calcula payloads para validarlos, loguea a INFO lo que
enviaria, y devuelve ExportResult vacio.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from scrapers.adapters._shared import backoff_delay, sha256_hex
from scrapers.adapters.http_client import USER_AGENT
from scrapers.dedup import specs

log = logging.getLogger(__name__)

_DEFAULT_WATERMARK = "1970-01-01T00:00:00Z"
_APORTES_PATH = "/api/aportes"
_WATERMARKS_PATH = "/api/source-watermarks"

# fetched_at es el wall-clock local de cuando el adapter termino de
# descargar la pagina, no el updated_at del registro en el servidor de la
# fuente. Un registro puede actualizarse del lado del servidor mientras el
# fetch esta en vuelo y no quedar reflejado en la respuesta que ya recibimos;
# si el watermark avanza exactamente hasta fetched_at, la siguiente corrida
# (updated_after=watermark) nunca volveria a pedirlo. Este margen crea una
# ventana de overlap; la idempotencia por external_id en dataVenezuela
# absorbe los registros re-enviados en ese overlap sin duplicar.
_WATERMARK_SAFETY_MARGIN = timedelta(minutes=5)
_FETCHED_AT_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Status HTTP transitorios que ameritan reintento del POST a /api/aportes.
# Definido localmente (no se mueve a _shared para no chocar con PR #61).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_POST_RETRIES = 4


@dataclass(frozen=True)
class StagingConfig:
    """Configuracion del exporter leida del entorno.

    El source_slug NO vive aqui: una sola corrida del pipeline procesa
    multiples fuentes (ver run_pipeline._run_source), asi que cada llamada a
    get_watermark/export_source recibe su propio source_slug (source.id).
    """

    api_key: str
    base_url: str

    @classmethod
    def from_env(cls) -> StagingConfig | None:
        """Construye la config desde STAGING_*; None si falta alguna.

        Distingue el dry-run intencional (NINGUNA STAGING_* seteada, dev local)
        de una config parcial en prod (algunas seteadas, otras no): la primera
        loguea a INFO, la segunda a ERROR listando las faltantes. En ambos casos
        devuelve None (gatilla el dry-run) sin abortar el pipeline.
        """
        values = {
            "STAGING_API_KEY": os.getenv("STAGING_API_KEY"),
            "STAGING_BASE_URL": os.getenv("STAGING_BASE_URL"),
        }
        present = [k for k, v in values.items() if v]
        if not present:
            log.info(
                "staging_exporter deshabilitado: ninguna STAGING_* seteada "
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
        return cls(
            api_key=str(values["STAGING_API_KEY"]),
            base_url=str(values["STAGING_BASE_URL"]).rstrip("/"),
        )


@dataclass
class ExportResult:
    """Resultado agregado de exportar los records de una fuente."""

    sent: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _content_hash(body: dict[str, object]) -> str:
    """sha256 con prefijo del repo ("sha256:") sobre json canonico del payload.

    Delega en scrapers.adapters._shared.sha256_hex para mantener el formato
    consistente con el resto del pipeline (adapters rss/pdf/playwright/html/api).
    """
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_hex(raw.encode("utf-8"))


def _apply_safety_margin(watermark_at: str) -> str:
    """Resta ``_WATERMARK_SAFETY_MARGIN`` al watermark antes de persistirlo.

    Ver comentario junto a ``_WATERMARK_SAFETY_MARGIN``. Si el formato no es
    el esperado (``now_utc()`` de todos los adapters), devuelve el valor
    intacto en vez de fallar — no vale la pena tumbar el pipeline por esto.
    """
    try:
        dt = datetime.strptime(watermark_at, _FETCHED_AT_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        log.warning("watermark con formato inesperado, sin margen de seguridad: %s", watermark_at)
        return watermark_at
    return (dt - _WATERMARK_SAFETY_MARGIN).strftime(_FETCHED_AT_FORMAT)


def compute_external_id(rec: dict[str, object], entity_type: str) -> str:
    """external_id determinista por tipo de entidad (idempotencia, upsert).

    Event/AcopioCenter: el fingerprint v1. Person: deterministic_id si esta
    presente; si no, fallback estable por cedula_hmac o por content_hash para
    no colapsar todos los Person sin det_id en una misma clave.
    """
    if entity_type == "Event":
        return specs.event_dedup_key(rec)
    if entity_type == "AcopioCenter":
        return specs.acopio_dedup_key(rec)
    det = rec.get("deterministic_id")
    if det:
        return str(det)
    event_id = str(rec.get("event_id") or "")
    cedula_hmac = rec.get("cedula_hmac")
    if isinstance(cedula_hmac, str) and cedula_hmac.strip():
        seed = f"person|{event_id}|{cedula_hmac}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()
    clean = {k: v for k, v in rec.items() if not k.startswith("_")}
    seed = f"person|{event_id}|{_content_hash(clean)}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class StagingExporter:
    """Envia aportes a /api/aportes y avanza el watermark de la fuente."""

    def __init__(
        self,
        config: StagingConfig | None,
        *,
        client: httpx.Client | None = None,
        run_id: str | None = None,
    ) -> None:
        self.config = config
        self.enabled = config is not None
        self.run_id = run_id or str(uuid.uuid4())
        self._owns_client = client is None
        self._client: httpx.Client | None = client
        if self.enabled and config is not None and client is None:
            self._client = httpx.Client(
                base_url=config.base_url,
                headers={
                    "x-api-key": config.api_key,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )

    # -- payload --------------------------------------------------------------

    def _build_payload(self, rec: dict[str, object], source_slug: str) -> dict[str, object]:
        entity_type = str(rec.get("_entity_type") or "Person")
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        spec = specs.spec_for_entity_type(entity_type)

        # Event/AcopioCenter: external_id y dedup_hash derivan ambos del mismo
        # fingerprint v1, asi que se calcula UNA sola vez y se reusa (evita el
        # doble computo: compute_external_id + specs.dedup_key). Person no
        # comparte: external_id tiene fallbacks (cedula_hmac/content_hash) que
        # no coinciden con dedup_key (deterministic_id), asi que se calcula por
        # separado. Los valores resultantes no cambian.
        if entity_type == "Event":
            fingerprint = specs.event_dedup_key(rec)
            external_id: str = fingerprint
            dedup_hash: str | None = fingerprint
        elif entity_type == "AcopioCenter":
            fingerprint = specs.acopio_dedup_key(rec)
            external_id = fingerprint
            dedup_hash = fingerprint
        else:
            external_id = compute_external_id(rec, entity_type)
            dedup_hash = specs.dedup_key(rec, entity_type)

        return {
            "runId": self.run_id,
            "entityType": _entity_type_slug(entity_type),
            "externalId": external_id,
            "dedupHash": dedup_hash,
            "dedupVersion": spec.version,
            "blockKeys": specs.block_keys(rec, entity_type),
            "contentHash": _content_hash(clean),
            "sourceSlug": source_slug,
            "sourceRecordId": _opt_str(rec.get("_source_record_id")),
            "sourceUrl": _opt_str(rec.get("_source_url")),
            "parserVersion": _opt_str(rec.get("_parser_version")),
            "normalizerVersion": _opt_str(rec.get("_normalizer_version")),
            "rawJson": clean,
        }

    # -- watermark ------------------------------------------------------------

    def get_watermark(self, source_slug: str) -> str:
        """Watermark actual de ``source_slug``; usado por run_pipeline ANTES
        del fetch para filtrar la ventana (``updated_after``).

        Fail-open al default (backfill completo) si el exporter esta
        deshabilitado (dry-run) o si la lectura falla: nunca debe bloquear el
        fetch, y re-fetchear de mas es preferible a perder registros. Cubre
        tanto errores de red/HTTP como respuestas 2xx con body invalido (ej.
        JSON malformado), que no son httpx.HTTPError pero igual deben
        degradar al default en vez de propagarse.
        """
        if not self.enabled or self._client is None:
            return _DEFAULT_WATERMARK
        try:
            resp = self._client.get(f"{_WATERMARKS_PATH}/{source_slug}")
            if resp.status_code == 404:
                return _DEFAULT_WATERMARK
            resp.raise_for_status()
            payload = resp.json()
            return str(payload.get("watermarkAt", _DEFAULT_WATERMARK))
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            log.warning("no se pudo leer watermark de %s: %s", source_slug, exc)
            return _DEFAULT_WATERMARK

    def _set_watermark(self, source_slug: str, watermark_at: str) -> bool:
        assert self._client is not None
        resp = self._client.put(
            f"{_WATERMARKS_PATH}/{source_slug}",
            json={"watermarkAt": watermark_at},
        )
        return resp.status_code in (200, 201)

    def _post_with_retry(self, path: str, payload: dict[str, object]) -> httpx.Response:
        """POST con exponential backoff en status transitorios y errores de red.

        Reintenta en 429/500/502/503/504 y en TimeoutException/NetworkError
        usando backoff_delay (de _shared). Devuelve la ultima response; relanza
        la ultima excepcion de transporte si se agotan los reintentos sin response.
        """
        assert self._client is not None
        last_exc: httpx.HTTPError | None = None
        resp: httpx.Response | None = None
        for attempt in range(1, _MAX_POST_RETRIES + 1):
            try:
                resp = self._client.post(path, json=payload)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < _MAX_POST_RETRIES:
                    delay = backoff_delay(attempt)
                    log.warning(
                        "%s en POST %s intento %d/%d — reintento en %.1fs",
                        type(exc).__name__, path, attempt, _MAX_POST_RETRIES, delay,
                    )
                    time.sleep(delay)
                continue
            if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_POST_RETRIES:
                delay = backoff_delay(attempt)
                log.warning(
                    "HTTP %s en POST %s intento %d/%d — reintento en %.1fs",
                    resp.status_code, path, attempt, _MAX_POST_RETRIES, delay,
                )
                time.sleep(delay)
                continue
            return resp
        if resp is not None:
            return resp
        assert last_exc is not None
        raise last_exc

    # -- export ---------------------------------------------------------------

    def export_source(
        self,
        records: list[dict[str, object]],
        *,
        source_slug: str,
        source_fetched_ats: list[str],
        source_errors: list[str] | None = None,
    ) -> ExportResult:
        """Exporta los records de ``source_slug``; avanza su watermark si todo OK.

        ``source_errors`` son errores previos de la fuente (parse, PII,
        enriquecimiento y el fail-closed de proteccion de menores) que se
        inyectan despues en run_pipeline. Si no estan vacios, el watermark NO
        avanza: evita perder silenciosamente registros descartados que nunca
        llegaron a staging (p.ej. un menor) saltando su fetched_at.
        """
        result = ExportResult()

        if not self.enabled or self._client is None or self.config is None:
            for rec in records:
                payload = self._build_payload(rec, source_slug)
                log.info(
                    "DRY-RUN staging_exporter: enviaria entityType=%s externalId=%s",
                    payload["entityType"],
                    payload["externalId"],
                )
            return result

        for rec in records:
            payload = self._build_payload(rec, source_slug)
            try:
                resp = self._post_with_retry(_APORTES_PATH, payload)
            except httpx.HTTPError as exc:
                result.errors.append(f"POST {_APORTES_PATH} fallo: {exc}")
                continue
            if resp.status_code in (200, 201):
                result.sent += 1
            elif resp.status_code == 409:
                result.duplicates += 1
            else:
                result.errors.append(
                    f"{_APORTES_PATH} status {resp.status_code} "
                    f"para externalId={payload['externalId']}"
                )

        # El watermark solo avanza si no hubo NINGUN error: ni de POST/PUT ni
        # previo de la fuente (source_errors).
        has_source_errors = bool(source_errors)
        if not result.errors and not has_source_errors and source_fetched_ats:
            new_watermark = _apply_safety_margin(max(source_fetched_ats))
            try:
                if not self._set_watermark(source_slug, new_watermark):
                    result.errors.append("no se pudo actualizar el watermark")
            except httpx.HTTPError as exc:
                result.errors.append(f"PUT {_WATERMARKS_PATH} fallo: {exc}")

        return result

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        """Cierra el httpx.Client solo si lo creo el exporter."""
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> StagingExporter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _opt_str(value: object) -> str | None:
    """Devuelve str(value) o None si value es falsy/None."""
    if value is None or value == "":
        return None
    return str(value)


# Nombre interno del tipo (Event/AcopioCenter/Person) -> slug de la columna
# aportes.entity_type.
_ENTITY_TYPE_SLUGS = {
    "Event": "event",
    "AcopioCenter": "acopio",   # Zod enum: "event" | "acopio" | "person"
    "Person": "person",
}

def _entity_type_slug(entity_type: str) -> str:
    return _ENTITY_TYPE_SLUGS.get(entity_type, entity_type.lower())