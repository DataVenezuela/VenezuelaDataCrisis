"""Provenance exporter: escribe la capa Bronze (scrape_runs + raw_artifacts).

Issue #256. El pipeline (no el exporter de aportes) es quien registra la
procedencia de cada corrida:

  - una fila en ``scrape_runs`` por fuente por invocacion (``start_run``), y
  - una fila APPEND-ONLY en ``raw_artifacts`` por pagina fetcheada
    (``record_artifact``): dos corridas => dos artifacts de la misma pagina.

Cada aporte referencia su ``artifact_id`` (FK NOT NULL -> raw_artifacts), asi
que ``StagingExporter`` deja de emitir ``run_id``/``scraper_id``/``source_url``/
``parser_version`` y emite ``artifact_id`` (ver ``staging_exporter._build_payload``).

Seguridad (ADR 0008): ``raw_artifacts.raw_text`` es el UNICO PII en claro en
reposo del sistema. Un ``pg_cron`` de backend lo anula a las 12h (deja la fila +
``body_hash``). Este exporter NUNCA loguea ``raw_text``: solo ``body_hash`` /
``page`` / ``http_status``. El transporte exige HTTPS (via ``StagingConfig``) y
no sigue redirects para no filtrar la credencial ni PII.

Espeja ``StagingExporter``: reusa ``StagingConfig`` (mismas SUPABASE_*), el
``httpx.Client`` es inyectable para tests sin red, entra en dry-run silencioso si
falta la config (devuelve placeholders para que el pipeline siga), y reintenta con
backoff en status transitorios (429/5xx) y errores de red. El rol
``scraper_ingest`` necesita ademas INSERT/SELECT sobre scrape_runs y raw_artifacts.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import httpx

from scrapers.adapters._shared import backoff_delay
from scrapers.adapters.base import RawContent
from scrapers.adapters.http_client import USER_AGENT
from scrapers.exporters.staging_exporter import StagingConfig

log = logging.getLogger(__name__)

_SCRAPE_RUNS_PATH = "/rest/v1/scrape_runs"
_RAW_ARTIFACTS_PATH = "/rest/v1/raw_artifacts"

# UUID placeholder para dry-run: nunca viaja a la red (en dry-run no se abre
# cliente). Deja que el pipeline stampee un _artifact_id no vacio y siga.
_DRYRUN_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_POST_RETRIES = 4


def _now_iso() -> str:
    """Timestamp ISO-8601 UTC sin microsegundos, para ``scrape_runs.finished_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coerce_text(value: object) -> str:
    """Coacciona el contenido crudo (str | dict | list) a texto para raw_text.

    Los adapters JSON entregan ``raw_content`` ya deserializado (dict/list); los
    de texto/HTML entregan str. ``raw_artifacts.raw_text`` es ``text``, asi que se
    serializa determinista lo no-string.
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


class ProvenanceExporter:
    """Escribe scrape_runs + raw_artifacts a Supabase via PostgREST."""

    def __init__(
        self,
        config: StagingConfig | None,
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

    def _post_with_retry(
        self,
        path: str,
        payload: dict[str, object],
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response | None:
        """POST con backoff en status transitorios / errores de red.

        Devuelve la ultima response, o None si se agotan los reintentos por un
        error de transporte (nunca propaga: la procedencia no debe tumbar el run).
        NUNCA incluye ``payload`` en los logs (puede contener raw_text = PII).
        """
        assert self._client is not None
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
    def _extract_id(resp: httpx.Response, id_key: str) -> str | None:
        """Lee ``id_key`` de la respuesta return=representation (lista u objeto)."""
        try:
            data = resp.json()
        except ValueError:
            return None
        row = data[0] if isinstance(data, list) and data else data
        if isinstance(row, dict):
            value = row.get(id_key)
            if value:
                return str(value)
        return None

    # -- scrape_runs ----------------------------------------------------------

    def start_run(self, source_id: str) -> str | None:
        """Crea la fila ``scrape_runs`` de esta fuente y devuelve su ``run_id``.

        ``source_id`` ES el UUID de la tabla sources (source.id): el config lo trae
        resuelto, asi que ya no hay un GET slug -> id previo. Dry-run: devuelve un
        placeholder (no abre red) para que el pipeline siga. Enabled: POSTea con
        ``return=representation`` y lee el run_id. Devuelve None si no se pudo crear
        la corrida (el pipeline degrada fail-closed: sin run_id no hay artifact_id
        ni aporte).
        """
        if not self.enabled or self._client is None:
            return _DRYRUN_PLACEHOLDER
        resp = self._post_with_retry(
            _SCRAPE_RUNS_PATH,
            {"source_id": source_id},
            headers={"Prefer": "return=representation"},
        )
        if resp is not None and resp.status_code in (401, 403):
            # Grant faltante, no error transitorio: hacerlo ruidoso para no
            # confundirlo con una caida temporal. El rol scraper_ingest necesita
            # INSERT sobre scrape_runs y raw_artifacts.
            log.error(
                "start_run %s: sin permiso (status %s); verificar SUPABASE_INGEST_JWT "
                "y grants INSERT del rol scraper_ingest sobre scrape_runs",
                source_id, resp.status_code,
            )
            return None
        if resp is None or resp.status_code not in (200, 201):
            log.warning(
                "start_run %s: no se pudo crear scrape_run (status=%s)",
                source_id, getattr(resp, "status_code", "n/a"),
            )
            return None
        run_id = self._extract_id(resp, "run_id")
        if run_id is None:
            log.warning("start_run %s: respuesta sin run_id", source_id)
        return run_id

    def finish_run(self, run_id: str | None, stats: dict[str, object]) -> None:
        """Cierra la corrida: ``finished_at`` + ``stats``. Best-effort, nunca lanza.

        Un fallo aca no pierde datos (los aportes/artifacts ya se escribieron); se
        loguea y se sigue.
        """
        if not self.enabled or self._client is None or not run_id:
            return
        try:
            resp = self._client.patch(
                f"{_SCRAPE_RUNS_PATH}?run_id=eq.{run_id}",
                json={"finished_at": _now_iso(), "stats": stats},
                headers={"Prefer": "return=minimal"},
            )
        except httpx.HTTPError as exc:
            log.warning("finish_run %s: error de red: %s", run_id, exc)
            return
        if resp.status_code not in (200, 204):
            log.warning("finish_run %s: status inesperado %s", run_id, resp.status_code)

    # -- raw_artifacts --------------------------------------------------------

    def record_artifact(self, run_id: str | None, page: RawContent) -> str | None:
        """Registra la pagina cruda en ``raw_artifacts`` (append-only) y devuelve
        su ``artifact_id``.

        ``body_hash`` = ``page['content_hash']`` (hex puro del adapter);
        ``raw_text`` = ``page['raw_content']`` coaccionado a texto (el UNICO PII en
        claro en reposo). Dry-run: placeholder. Enabled sin ``run_id`` (la corrida
        no se pudo crear): None, sin POST (no puede haber artifact sin run FK).
        Devuelve None si el INSERT falla (el pipeline no exporta un aporte sin
        artifact_id: fail-closed via ``_build_payload``).
        """
        if not self.enabled or self._client is None:
            return _DRYRUN_PLACEHOLDER if run_id else None
        if not run_id:
            return None
        body: dict[str, object] = {
            "run_id": run_id,
            "source_url": page.get("source_url"),
            "http_status": page.get("http_status"),
            "fetched_at": page.get("fetched_at"),
            "body_hash": page.get("content_hash"),
            "page": page.get("page"),
            "raw_text": _coerce_text(page.get("raw_content")),
        }
        resp = self._post_with_retry(
            _RAW_ARTIFACTS_PATH, body, headers={"Prefer": "return=representation"}
        )
        if resp is not None and resp.status_code in (401, 403):
            log.error(
                "record_artifact: sin permiso (status %s); verificar grants INSERT "
                "del rol scraper_ingest sobre raw_artifacts (run_id=%s page=%s)",
                resp.status_code, run_id, page.get("page"),
            )
            return None
        if resp is None or resp.status_code not in (200, 201):
            # NUNCA loguear el body ni resp.text: contienen raw_text = PII. Solo
            # metadatos (status / page / body_hash).
            log.warning(
                "record_artifact: INSERT fallo (run_id=%s page=%s body_hash=%s status=%s)",
                run_id, page.get("page"), page.get("content_hash"),
                getattr(resp, "status_code", "n/a"),
            )
            return None
        artifact_id = self._extract_id(resp, "artifact_id")
        if artifact_id is None:
            log.warning(
                "record_artifact: respuesta sin artifact_id (run_id=%s page=%s)",
                run_id, page.get("page"),
            )
        return artifact_id

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> "ProvenanceExporter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["ProvenanceExporter", "_DRYRUN_PLACEHOLDER"]
