"""Quarantine exporter: POST de registros no procesables a /api/v1/quarantine.

Cuando un registro no puede procesarse automaticamente (parser ausente, schema
invalido, PII no redactable, PDF sin texto, etc.) NO se descarta en silencio:
se preserva en la Quarantine DB del backend (dataVenezuela) via un POST por
registro. El run no falla — el registro queda en cuarentena y el pipeline sigue
con las demas fuentes (Issue #88).

Espeja StagingExporter (scrapers/exporters/staging_exporter.py):
  - httpx.Client inyectable via el parametro ``client`` (tests sin red real).
  - Dry-run silencioso si faltan las env vars QUARANTINE_*: no abre cliente,
    loguea a INFO lo que enviaria y devuelve un QuarantineResult vacio.
  - Retry con backoff en status transitorios (429/5xx) y errores de red.

La tabla ``quarantine_records`` vive en el backend, igual que ``aportes``; este
repo (scraper) no ejecuta SQL — solo construye y envia el payload. El
``run_id`` se comparte con el aporte para correlacionar cuarentena y staging de
una misma corrida (depende del concepto de run_id de Stage 1, #81).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

import httpx

from scrapers.adapters._shared import backoff_delay
from scrapers.adapters.http_client import USER_AGENT

log = logging.getLogger(__name__)

_QUARANTINE_PATH = "/api/v1/quarantine"

# Status HTTP transitorios que ameritan reintento (igual criterio que staging).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_POST_RETRIES = 4

# Fragmento maximo del payload redactado que se envia. NUNCA el payload completo:
# truncamos defensivamente aunque el caller pase algo mas largo.
_PREVIEW_MAX_CHARS = 500

# Valores controlados — DEBEN coincidir con los CHECK de quarantine_records en
# el backend. Hay un fixture de test por cada reason_code (criterio #88).
REASON_CODES = frozenset(
    {
        "pii_untreatable",  # PII no tratable/redactable automaticamente
        "invalid_schema",  # schema invalido o inesperado
        "parser_unavailable",  # parser inexistente o incompatible
        "pdf_no_text",  # PDF sin texto extraible
        "unclassified_sensitive",  # contenido potencialmente sensible sin clasificar
        "contradictory_sources",  # datos contradictorios entre fuentes
        "ambiguous_manual_review",  # ambiguo, requiere criterio humano
    }
)

RISK_LEVELS = frozenset({"low", "medium", "high"})


def quarantine_payload_hash(raw: str | bytes) -> str:
    """SHA-256 hex puro (64 chars, SIN prefijo) del payload original.

    A diferencia de ``adapters._shared.sha256_hex`` (que antepone ``sha256:``
    para ``content_hash``), aqui se devuelve hex pelado porque
    ``quarantine_records.payload_hash`` es ``varchar(64)``. Ese hash sobrevive a
    la destruccion del registro y permite verificar que ese payload exacto fue
    visto y destruido deliberadamente.
    """
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class QuarantineConfig:
    """Configuracion del exporter leida del entorno.

    Apunta al backend que hospeda ``/api/v1/quarantine`` (normalmente el mismo
    dataVenezuela que ``/api/aportes``, pero con sus propias credenciales para
    no acoplar los dos endpoints).
    """

    api_key: str
    base_url: str

    @classmethod
    def from_env(cls) -> QuarantineConfig | None:
        """Construye la config desde QUARANTINE_*; None si falta alguna.

        Distingue el dry-run intencional (NINGUNA QUARANTINE_* seteada, dev
        local) de una config parcial en prod (algunas si, otras no): la primera
        loguea a INFO, la segunda a ERROR listando las faltantes. En ambos casos
        devuelve None (gatilla el dry-run) sin abortar el pipeline.
        """
        values = {
            "QUARANTINE_API_KEY": os.getenv("QUARANTINE_API_KEY"),
            "QUARANTINE_BASE_URL": os.getenv("QUARANTINE_BASE_URL"),
        }
        present = [k for k, v in values.items() if v]
        if not present:
            log.info(
                "quarantine_exporter deshabilitado: ninguna QUARANTINE_* seteada "
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
        return cls(
            api_key=str(values["QUARANTINE_API_KEY"]),
            base_url=str(values["QUARANTINE_BASE_URL"]).rstrip("/"),
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
        """Valida enums controlados; ValueError si reason_code/risk_level es invalido.

        Estos valores deben respetar los CHECK de la tabla en el backend; un
        valor fuera del set seria rechazado por la DB. Validar aca lo convierte
        en un error claro y temprano (cubierto por tests) en vez de un 4xx opaco.
        """
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
    duplicates: int = 0  # 409 — ese payload ya estaba en cuarentena
    errors: list[str] = field(default_factory=list)


def _truncate_preview(preview: str | None) -> str | None:
    """Trunca el fragmento redactado a _PREVIEW_MAX_CHARS (defensa, no redaccion)."""
    if preview is None:
        return None
    if len(preview) <= _PREVIEW_MAX_CHARS:
        return preview
    return preview[:_PREVIEW_MAX_CHARS]


class QuarantineExporter:
    """Envia registros no procesables a /api/v1/quarantine del backend."""

    def __init__(
        self,
        config: QuarantineConfig | None,
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
                    # El backend dataVenezuela autentica al scraper con x-api-key
                    # (authenticatePartner), no con Bearer. Ver POST /api/v1/quarantine.
                    "x-api-key": config.api_key,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )

    # -- payload --------------------------------------------------------------

    def _build_payload(self, rec: QuarantineRecord) -> dict[str, object]:
        """Arma el JSON del POST. Valida enums y trunca el preview.

        Las claves van en camelCase: es el contrato de la API de dataVenezuela
        (el schema Zod de /api/v1/quarantine, igual que /api/aportes). El backend
        mapea camelCase -> columnas snake_case en su capa de servicio.
        """
        rec.validate()
        return {
            "runId": self.run_id,
            "sourceSlug": rec.source_slug,
            "sourceUrl": rec.source_url,
            "reasonCode": rec.reason_code,
            "reasonDetail": rec.reason_detail,
            "riskLevel": rec.risk_level,
            "payloadPreviewRedacted": _truncate_preview(rec.payload_preview_redacted),
            "payloadHash": rec.payload_hash,
            "piiFindingsSummary": rec.pii_findings_summary,
        }

    # -- POST con retry -------------------------------------------------------

    def _post_with_retry(
        self, path: str, payload: dict[str, object]
    ) -> httpx.Response:
        """POST con backoff exponencial en status transitorios y errores de red.

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

    def quarantine(self, record: QuarantineRecord) -> QuarantineResult:
        """Envia un solo registro a cuarentena (azucar sobre quarantine_many)."""
        return self.quarantine_many([record])

    def quarantine_many(self, records: list[QuarantineRecord]) -> QuarantineResult:
        """Envia varios registros a cuarentena. Nunca relanza: resiliencia por registro.

        Un fallo de validacion, de red o un status inesperado de un registro se
        acumula en ``errors`` y NO interrumpe el resto — el run debe seguir.
        """
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
                    payload["sourceSlug"], payload["reasonCode"],
                    payload["riskLevel"], payload["payloadHash"],
                )
            return result

        for rec in records:
            try:
                payload = self._build_payload(rec)
            except ValueError as exc:
                result.errors.append(f"registro invalido: {exc}")
                continue
            try:
                resp = self._post_with_retry(_QUARANTINE_PATH, payload)
            except httpx.HTTPError as exc:
                result.errors.append(f"POST {_QUARANTINE_PATH} fallo: {exc}")
                continue
            if resp.status_code in (200, 201):
                result.sent += 1
            elif resp.status_code == 409:
                result.duplicates += 1
            else:
                result.errors.append(
                    f"{_QUARANTINE_PATH} status {resp.status_code} "
                    f"para source_slug={payload['sourceSlug']} "
                    f"reason_code={payload['reasonCode']}"
                )
        return result

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        """Cierra el httpx.Client solo si lo creo el exporter."""
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> QuarantineExporter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
