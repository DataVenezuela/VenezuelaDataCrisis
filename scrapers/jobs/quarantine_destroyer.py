"""Destruccion auditable de registros en quarantined_records (Issue #273).

El ``QuarantineExporter`` (scrapers/exporters/quarantine_exporter.py) preserva
registros no procesables en ``quarantined_records`` via PostgREST, incluyendo un
``payload_hash`` para poder demostrar que ese payload exacto fue visto sin
retener su contenido. Este job implementa la otra mitad: destruir un registro
cuando ya no hace falta retenerlo.

La destruccion:
  1. Pone ``payload_preview_redacted = NULL`` y ``pii_findings_summary = NULL``
     (los unicos campos con contenido potencialmente sensible).
  2. Estampa ``destroyed_at = now()``.
  3. PRESERVA la fila con ``payload_hash`` y el resto de metadatos
     (``source_slug``, ``reason_code``, ``quarantined_at``, etc.) para auditoria.

Solo se puede destruir un registro si ``review_status = 'rejected'`` O
``retention_until < now()``. La elegibilidad se hace cumplir del lado del
servidor con un PATCH filtrado (atomico, sin TOCTOU): el filtro PostgREST ES la
guarda, asi que una carrera no puede destruir una fila inelegible. El filtro
incluye ``destroyed_at=is.null`` para que re-destruir sea un no-op idempotente.

Espeja SilverMaterializer (scrapers/jobs/materializer.py):
  - Mismas credenciales via ``StagingConfig`` (SUPABASE_URL /
    SUPABASE_PUBLISHABLE_KEY / SUPABASE_INGEST_JWT).
  - ``httpx.Client`` inyectable via el parametro ``client`` (tests sin red real).
  - Dry-run silencioso si ``StagingConfig.from_env()`` es None: no toca la red,
    loguea a INFO y devuelve un DestroyResult vacio.
  - Retry con backoff via ``retry_post`` (reusa ``method="PATCH"``).

NUNCA loguea payloads ni ``resp.text``: solo id, payload_hash, review_status y
timestamps (que no son PII).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from scrapers.adapters._shared import now_utc, retry_post
from scrapers.adapters.http_client import USER_AGENT
from scrapers.exporters.staging_exporter import StagingConfig

log = logging.getLogger(__name__)

_QUARANTINE_PATH = "/rest/v1/quarantined_records"


def _eligibility_filter(now_iso: str) -> str:
    """Grupo OR de PostgREST: review_status='rejected' OR retention_until<now.

    Se combina en AND con los demas params (id, destroyed_at). PostgREST parsea
    ``or=(a,b)`` como (a OR b).
    """
    return f"(review_status.eq.rejected,retention_until.lt.{now_iso})"


@dataclass
class DestroyResult:
    """Resultado agregado de una operacion de destruccion.

    ``destroyed_ids`` lleva (id, payload_hash) de cada fila destruida para el log
    de auditoria; nunca contiene contenido sensible.
    """

    destroyed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    destroyed_ids: list[tuple[str, str | None]] = field(default_factory=list)


class QuarantineDestroyer:
    """Destruye registros de quarantined_records via PATCH filtrado a PostgREST."""

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

    # -- PATCH con retry ------------------------------------------------------

    def _patch(
        self, params: dict[str, str], now_iso: str
    ) -> httpx.Response | None:
        """PATCH filtrado que borra los campos sensibles y estampa destroyed_at.

        ``return=representation`` devuelve las filas afectadas: el server aplica
        la guarda de elegibilidad (los params), asi que 1+ filas = destruidas,
        0 filas = no elegible / ya destruida / inexistente.
        """
        assert self._client is not None
        body: dict[str, object] = {
            # NULL explicito (no omision): borrado deliberado de los campos
            # sensibles, no un default. No cae en el problema de missing=default.
            "payload_preview_redacted": None,
            "pii_findings_summary": None,
            "destroyed_at": now_iso,
        }
        headers = {"Prefer": "return=representation"}
        # retry_post arma la URL con ``path`` ya con querystring; construimos el
        # path con los params filtrados para que PostgREST aplique la guarda.
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = f"{_QUARANTINE_PATH}?{query}"
        return retry_post(
            self._client, path, body, headers=headers, method="PATCH", log=log
        )

    @staticmethod
    def _affected_rows(resp: httpx.Response | None) -> list[dict[str, object]]:
        """Filas devueltas por return=representation; [] si no hubo o no parsea."""
        if resp is None:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def _record_destroyed(
        self, rows: list[dict[str, object]], result: DestroyResult
    ) -> None:
        """Cuenta filas destruidas y las traza a INFO (auditoria). Sin PII."""
        for row in rows:
            rid = str(row.get("id"))
            phash = row.get("payload_hash")
            result.destroyed += 1
            result.destroyed_ids.append((rid, phash if isinstance(phash, str) else None))
            log.info(
                "quarantine_destroyer: destruido id=%s payload_hash=%s "
                "review_status=%s destroyed_at=%s",
                rid, phash, row.get("review_status"), row.get("destroyed_at"),
            )

    # -- destruccion por id ---------------------------------------------------

    def destroy(self, record_id: str) -> DestroyResult:
        """Destruye un unico registro por id, si es elegible.

        La guarda (rejected O retention_until<now) la aplica el PATCH filtrado.
        Si no afecta filas, hace un GET de clasificacion solo para el reporte
        (inexistente / ya destruido / no elegible); la decision ya la tomo el
        server.
        """
        result = DestroyResult()
        now_iso = now_utc()

        if not self.enabled or self._client is None:
            log.info(
                "DRY-RUN quarantine_destroyer: destruiria id=%s si fuera elegible "
                "(rejected o retention vencida)", record_id,
            )
            return result

        params = {
            "id": f"eq.{record_id}",
            "destroyed_at": "is.null",
            "or": _eligibility_filter(now_iso),
        }
        resp = self._patch(params, now_iso)
        if resp is None:
            result.errors.append(f"PATCH {record_id} fallo: reintentos agotados")
            return result
        if resp.status_code not in (200, 204):
            result.errors.append(
                f"PATCH {record_id} status {resp.status_code}"
            )
            return result

        rows = self._affected_rows(resp)
        if rows:
            self._record_destroyed(rows, result)
            return result

        # 0 filas: clasificar el motivo solo para el log/reporte.
        result.skipped += 1
        result.errors.append(self._classify_skip(record_id, now_iso))
        return result

    def _classify_skip(self, record_id: str, now_iso: str) -> str:
        """GET de solo-reporte para explicar por que no se destruyo la fila."""
        assert self._client is not None
        try:
            resp = self._client.get(
                _QUARANTINE_PATH,
                params={
                    "id": f"eq.{record_id}",
                    "select": "review_status,destroyed_at,retention_until",
                    "limit": 1,
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            return f"{record_id}: no elegible o inexistente (clasificacion no disponible)"
        if resp.status_code != 200:
            return f"{record_id}: no elegible o inexistente (GET status {resp.status_code})"
        rows = self._affected_rows(resp)
        if not rows:
            return f"{record_id}: inexistente"
        row = rows[0]
        if row.get("destroyed_at") is not None:
            return f"{record_id}: ya destruido"
        return (
            f"{record_id}: no elegible "
            f"(review_status={row.get('review_status')}, "
            f"retention_until={row.get('retention_until')})"
        )

    # -- barrido de retencion -------------------------------------------------

    def destroy_expired(self, limit: int | None = None) -> DestroyResult:
        """Destruye TODAS las filas elegibles (rejected o retention vencida).

        Un solo PATCH filtrado sin ``id=eq``. Con ``return=representation`` cada
        fila afectada vuelve y se cuenta/loguea. ``limit`` acota el batch (usa el
        ``limit`` de PostgREST) para no destruir todo de una en corridas grandes.
        """
        result = DestroyResult()
        now_iso = now_utc()

        if not self.enabled or self._client is None:
            log.info(
                "DRY-RUN quarantine_destroyer: barrido de retencion (destruiria "
                "filas rejected o con retention vencida)"
            )
            return result

        params = {
            "destroyed_at": "is.null",
            "or": _eligibility_filter(now_iso),
        }
        if limit is not None:
            params["limit"] = str(limit)
        resp = self._patch(params, now_iso)
        if resp is None:
            result.errors.append("PATCH barrido fallo: reintentos agotados")
            return result
        if resp.status_code not in (200, 204):
            result.errors.append(f"PATCH barrido status {resp.status_code}")
            return result

        self._record_destroyed(self._affected_rows(resp), result)
        return result

    # -- ciclo de vida --------------------------------------------------------

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def __enter__(self) -> QuarantineDestroyer:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
