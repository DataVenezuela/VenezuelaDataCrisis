"""Quarantine exporter: INSERT directo a Supabase via PostgREST.

Cuando un registro no puede procesarse automaticamente (parser ausente, schema
invalido, PII no redactable, PDF sin texto, etc.) NO se descarta en silencio:
se preserva en ``quarantined_records`` via escritura directa a Supabase
(POST /rest/v1/quarantined_records), igual que ``aportes`` (Issue #88).

Espeja StagingExporter (scrapers/exporters/staging_exporter.py):
  - Mismas credenciales: SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY / SUPABASE_INGEST_JWT.
  - httpx.Client inyectable via el parametro ``client`` (tests sin red real).
  - Dry-run silencioso si faltan las env vars SUPABASE_*: no abre cliente,
    loguea a INFO lo que enviaria y devuelve un QuarantineResult vacio.
  - Retry con backoff en status transitorios (429/5xx) y errores de red.

El payload viaja en snake_case (columnas de ``quarantined_records``).
El ``run_id`` del esquema es nullable (FK a scrape_runs); se omite del payload
porque el pipeline usa un UUID de correlacion local que no existe en scrape_runs.
La correlacion fuente/cuarentena se mantiene via ``source_slug``.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field

import httpx

from scrapers.adapters._shared import retry_post
from scrapers.adapters.http_client import USER_AGENT

log = logging.getLogger(__name__)

_QUARANTINE_PATH = "/rest/v1/quarantined_records"

# Fragmento maximo del payload redactado que se envia. NUNCA el payload completo.
_PREVIEW_MAX_CHARS = 500

# Valores controlados — DEBEN coincidir con los enums de quarantined_records en
# el backend. Hay un fixture de test por cada reason_code (criterio #88).
REASON_CODES = frozenset(
    {
        "pii_untreatable",          # PII no tratable/redactable automaticamente
        "invalid_schema",           # schema invalido o inesperado
        "parser_unavailable",       # parser inexistente o incompatible
        "pdf_no_text",              # PDF sin texto extraible
        "unclassified_sensitive",   # contenido potencialmente sensible sin clasificar
        "contradictory_sources",    # datos contradictorios entre fuentes
        "ambiguous_manual_review",  # ambiguo, requiere criterio humano
    }
)

RISK_LEVELS = frozenset({"low", "medium", "high"})


def quarantine_payload_hash(raw: str | bytes) -> str:
    """SHA-256 hex puro (64 chars, SIN prefijo) del payload original.

    Igual que ``adapters._shared.sha256_hex`` (que tambien devuelve hex puro sin
    prefijo para ``content_hash``), aqui se devuelve hex pelado porque
    ``quarantined_records.payload_hash`` es ``varchar(64)``. Ese hash sobrevive a
    la destruccion del registro y permite verificar que ese payload exacto fue
    visto y destruido deliberadamente.
    """
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class QuarantineConfig:
    """Configuracion del exporter leida del entorno.

    Usa las mismas credenciales Supabase que StagingExporter: SUPABASE_URL,
    SUPABASE_PUBLISHABLE_KEY y SUPABASE_INGEST_JWT. Ambos exporters entran y
    salen de dry-run juntos cuando las env vars no estan seteadas.
    """

    supabase_url: str
    publishable_key: str
    ingest_jwt: str

    @classmethod
    def from_env(cls) -> QuarantineConfig | None:
        """Construye la config desde SUPABASE_*; None si falta alguna.

        Distingue el dry-run intencional (NINGUNA SUPABASE_* seteada, dev
        local) de una config parcial en prod (algunas si, otras no): la primera
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
                "quarantine_exporter deshabilitado: ninguna SUPABASE_* seteada "
                "(dry-run intencional)"
            )
            return None
        if len(present) < len(values):
            missing = [k for k, v in values.items() if not v]
            log.error(
                "quarantine_exporter mal configurado: faltan %s; entrando en dry-run",
                missing,
            )
            return None
        supabase_url = str(values["SUPABASE_URL"]).rstrip("/")
        if not supabase_url.lower().startswith("https://"):
            log.error(
                "quarantine_exporter: SUPABASE_URL debe ser https:// (recibido %r); "
                "entrando en dry-run para no enviar credenciales/PII en claro",
                supabase_url,
            )
            return None
        return cls(
            supabase_url=supabase_url,
            publishable_key=str(values["SUPABASE_PUBLISHABLE_KEY"]),
            ingest_jwt=str(values["SUPABASE_INGEST_JWT"]),
        )


@dataclass(frozen=True)
class QuarantineRecord:
    """Un registro a preservar en cuarentena.

    El caller (run_pipeline, en el punto donde hoy se descartaria el registro)
    arma este objeto. ``payload_preview_redacted`` debe venir YA redactado (sin
    PII en claro); el exporter solo lo trunca defensivamente. ``payload_hash``
    se calcula con ``quarantine_payload_hash`` sobre el payload original.
    """

    source_slug: str
    reason_code: str
    risk_level: str
    source_url: str | None = None
    reason_detail: str | None = None
    payload_preview_redacted: str | None = None
    payload_hash: str | None = None
    pii_findings_summary: dict[str, object] | None = None

    def validate(self) -> None:
        """Valida enums controlados; ValueError si reason_code/risk_level es invalido."""
        if self.reason_code not in REASON_CODES:
            raise ValueError(
                f"reason_code invalido: {self.reason_code!r} "
                f"(validos: {sorted(REASON_CODES)})"
            )
        if self.risk_level not in RISK_LEVELS:
            raise ValueError(
                f"risk_level invalido: {self.risk_level!r} "
                f"(validos: {sorted(RISK_LEVELS)})"
            )
        if not self.source_slug:
            raise ValueError("source_slug es obligatorio")


@dataclass
class QuarantineResult:
    """Resultado agregado de enviar registros a cuarentena."""

    sent: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _truncate_preview(preview: str | None) -> str | None:
    if preview is None:
        return None
    if len(preview) <= _PREVIEW_MAX_CHARS:
        return preview
    return preview[:_PREVIEW_MAX_CHARS]


class QuarantineExporter:
    """Inserta registros no procesables en quarantined_records via PostgREST."""

    def __init__(
        self,
        config: QuarantineConfig | None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self.enabled = config is not None
        self._owns_client = client is None
        self._client: httpx.Client | None = client
        if self.enabled and config is not None and client is None:
            self._client = httpx.Client(
                base_url=config.supabase_url,
                headers={
                    # Auth PostgREST: misma convencion que StagingExporter.
                    "apikey": config.publishable_key,
                    "Authorization": f"Bearer {config.ingest_jwt}",
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    # Evitar que PostgREST devuelva la fila completa en la respuesta.
                    "Prefer": "return=minimal",
                },
                timeout=httpx.Timeout(30.0),
                follow_redirects=False,
            )

    # -- payload --------------------------------------------------------------

    def _build_payload(self, rec: QuarantineRecord) -> dict[str, object]:
        """Arma el JSON del POST en snake_case (columnas de quarantined_records).

        Omite run_id: el pipeline usa un UUID de correlacion local que no existe
        en scrape_runs, y la columna es nullable. No incluye claves con None
        para no sobreescribir defaults del servidor (review_status, quarantined_at).
        """
        rec.validate()
        payload: dict[str, object] = {
            "source_slug": rec.source_slug,
            "reason_code": rec.reason_code,
            "risk_level": rec.risk_level,
        }
        for key, value in (
            ("source_url", rec.source_url),
            ("reason_detail", rec.reason_detail),
            ("payload_preview_redacted", _truncate_preview(rec.payload_preview_redacted)),
            ("payload_hash", rec.payload_hash),
            ("pii_findings_summary", rec.pii_findings_summary),
        ):
            if value is not None:
                payload[key] = value
        return payload

    # -- POST con retry -------------------------------------------------------

    def _post_with_retry(
        self, path: str, payload: dict[str, object]
    ) -> httpx.Response | None:
        assert self._client is not None
        return retry_post(self._client, path, payload, log=log)

    # -- export ---------------------------------------------------------------

    def quarantine(self, record: QuarantineRecord) -> QuarantineResult:
        """Envia un solo registro a cuarentena (azucar sobre quarantine_many)."""
        return self.quarantine_many([record])

    def quarantine_many(self, records: list[QuarantineRecord]) -> QuarantineResult:
        """Inserta varios registros en quarantined_records. Nunca relanza."""
        result = QuarantineResult()

        if not self.enabled or self._client is None:
            for rec in records:
                try:
                    payload = self._build_payload(rec)
                except ValueError as exc:
                    result.errors.append(f"registro invalido (dry-run): {exc}")
                    continue
                log.info(
                    "DRY-RUN quarantine_exporter: enviaria source_slug=%s reason_code=%s "
                    "risk_level=%s payload_hash=%s",
                    payload["source_slug"], payload["reason_code"],
                    payload["risk_level"], payload.get("payload_hash"),
                )
            return result

        for rec in records:
            try:
                payload = self._build_payload(rec)
            except ValueError as exc:
                result.errors.append(f"registro invalido: {exc}")
                continue
            resp = self._post_with_retry(_QUARANTINE_PATH, payload)
            if resp is None:
                result.errors.append(f"POST {_QUARANTINE_PATH} fallo: reintentos agotados")
                continue
            if resp.status_code in (200, 201):
                result.sent += 1
            elif resp.status_code == 409:
                # PostgREST sin on_conflict no devuelve 409, pero se conserva el
                # contador por si el backend agrega un unique index en el futuro.
                result.duplicates += 1
            else:
                result.errors.append(
                    f"{_QUARANTINE_PATH} status {resp.status_code} "
                    f"para source_slug={payload['source_slug']} "
                    f"reason_code={payload['reason_code']}"
                )
        return result

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> QuarantineExporter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
