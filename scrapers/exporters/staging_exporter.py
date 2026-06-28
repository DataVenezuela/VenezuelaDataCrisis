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
import uuid
from dataclasses import dataclass, field

import httpx

from scrapers.adapters.http_client import USER_AGENT
from scrapers.dedup import specs

log = logging.getLogger(__name__)

_DEFAULT_WATERMARK = "1970-01-01T00:00:00Z"
_APORTES_PATH = "/api/aportes"
_WATERMARKS_PATH = "/api/source_watermarks"


@dataclass(frozen=True)
class StagingConfig:
    """Configuracion del exporter leida del entorno."""

    api_key: str
    base_url: str
    source_slug: str

    @classmethod
    def from_env(cls) -> StagingConfig | None:
        """Construye la config desde STAGING_*; None si falta cualquiera.

        El None gatilla el dry-run silencioso en StagingExporter.
        """
        api_key = os.getenv("STAGING_API_KEY")
        base_url = os.getenv("STAGING_BASE_URL")
        source_slug = os.getenv("STAGING_SOURCE_SLUG")
        if not (api_key and base_url and source_slug):
            log.info("staging_exporter deshabilitado: faltan STAGING_* (dry-run)")
            return None
        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            source_slug=source_slug,
        )


@dataclass
class ExportResult:
    """Resultado agregado de exportar los records de una fuente."""

    sent: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _content_hash(body: dict[str, object]) -> str:
    """sha256 hex de json canonico del payload de negocio limpio."""
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
                    "Authorization": f"Bearer {config.api_key}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )

    # -- payload --------------------------------------------------------------

    def _build_payload(self, rec: dict[str, object]) -> dict[str, object]:
        entity_type = str(rec.get("_entity_type") or "Person")
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        spec = specs.spec_for_entity_type(entity_type)
        source_slug = self.config.source_slug if self.config is not None else ""
        return {
            "run_id": self.run_id,
            "entity_type": _entity_type_slug(entity_type),
            "external_id": compute_external_id(rec, entity_type),
            "dedup_hash": specs.dedup_key(rec, entity_type),
            "dedup_version": spec.version,
            "block_keys": specs.block_keys(rec, entity_type),
            "content_hash": _content_hash(clean),
            "source_slug": source_slug,
            "source_record_id": _opt_str(rec.get("_source_record_id")),
            "source_url": _opt_str(rec.get("_source_url")),
            "parser_version": _opt_str(rec.get("_parser_version")),
            "normalizer_version": _opt_str(rec.get("_normalizer_version")),
            "data": clean,
        }

    # -- watermark ------------------------------------------------------------

    def _get_watermark(self) -> str:
        assert self._client is not None and self.config is not None
        resp = self._client.get(f"{_WATERMARKS_PATH}/{self.config.source_slug}")
        if resp.status_code == 404:
            return _DEFAULT_WATERMARK
        resp.raise_for_status()
        payload = resp.json()
        return str(payload.get("watermark_at", _DEFAULT_WATERMARK))

    def _set_watermark(self, watermark_at: str) -> bool:
        assert self._client is not None and self.config is not None
        resp = self._client.put(
            _WATERMARKS_PATH,
            json={
                "source_slug": self.config.source_slug,
                "watermark_at": watermark_at,
            },
        )
        return resp.status_code in (200, 201)

    # -- export ---------------------------------------------------------------

    def export_source(
        self,
        records: list[dict[str, object]],
        *,
        source_fetched_ats: list[str],
    ) -> ExportResult:
        """Exporta los records de una fuente; avanza el watermark si todo OK."""
        result = ExportResult()

        if not self.enabled or self._client is None or self.config is None:
            for rec in records:
                payload = self._build_payload(rec)
                log.info(
                    "DRY-RUN staging_exporter: enviaria entity_type=%s external_id=%s",
                    payload["entity_type"],
                    payload["external_id"],
                )
            return result

        # Lectura informativa del watermark actual (no filtra en Stage 1).
        try:
            self._get_watermark()
        except httpx.HTTPError as exc:
            log.warning("no se pudo leer watermark: %s", exc)

        for rec in records:
            payload = self._build_payload(rec)
            try:
                resp = self._client.post(_APORTES_PATH, json=payload)
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
                    f"para external_id={payload['external_id']}"
                )

        if not result.errors and source_fetched_ats:
            new_watermark = max(source_fetched_ats)
            try:
                if not self._set_watermark(new_watermark):
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
    "AcopioCenter": "acopio_center",
    "Person": "person",
}


def _entity_type_slug(entity_type: str) -> str:
    return _ENTITY_TYPE_SLUGS.get(entity_type, entity_type.lower())
