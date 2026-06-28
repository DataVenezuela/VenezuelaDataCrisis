"""
scrapers/adapters/api_adapter.py
=================================
Adapter para fuentes de tipo ``api_json``.

Usa ``httpx`` (sync) con:
- Paginación automática vía ``limit`` / ``offset`` (estilo encuentralos)
- Exponential backoff con jitter en errores 429 / 5xx
- Timeout configurable
- User-Agent de interés público

Contrato de salida
------------------
Cada página produce un ``RawContent`` con los campos estándar del pipeline
(ver ``base.py``) más:
  offset      : int   — offset enviado en esa petición
  limit       : int   — tamaño de página solicitado
  records_in_page : int — registros realmente devueltos en esa página

Uso básico
----------
::

    from scrapers.adapters.api_adapter import ApiAdapter

    adapter = ApiAdapter(base_url="https://encuentralos.tecnosoft.dev")
    for page in adapter.fetch_all("/api/personas"):
        registros = page["raw_content"]["data"]   # lista de dicts
        print(f"Página {page['page']} — {page['records_in_page']} registros")
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Iterator

import httpx

from .base import AdapterProtocol, RawContent
from scrapers.adapters.http_client import USER_AGENT

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes por defecto
# ---------------------------------------------------------------------------

_DEFAULT_PAGE_SIZE = 20
_DEFAULT_TIMEOUT = 30.0          # segundos
_MAX_RETRIES = 5
_BACKOFF_BASE = 1.0              # segundos base para backoff
_BACKOFF_MAX = 60.0              # techo del backoff
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _now_utc() -> str:
    """ISO-8601 UTC sin microsegundos."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(obj: Any) -> str:
    """Hash SHA-256 del contenido serializado como JSON compacto."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _backoff_delay(attempt: int) -> float:
    """
    Exponential backoff con jitter completo.

    ``attempt`` empieza en 1.  Fórmula:
        delay = min(base * 2^(attempt-1), max) + random(0, 1)
    """
    exp = _BACKOFF_BASE * (2 ** (attempt - 1))
    capped = min(exp, _BACKOFF_MAX)
    return capped + random.random()


# ---------------------------------------------------------------------------
# Adapter principal
# ---------------------------------------------------------------------------

class ApiAdapter:
    """
    Adapter para endpoints JSON paginados (limit/offset).

    Parameters
    ----------
    base_url:
        Raíz del servidor, p. ej. ``"https://encuentralos.tecnosoft.dev"``.
        El path concreto se pasa en ``fetch`` / ``fetch_all``.
    page_size:
        Registros por página (parámetro ``limit`` de la query).
    timeout:
        Timeout en segundos para cada petición individual.
    extra_headers:
        Headers adicionales que se mezclan con los defaults.
    max_retries:
        Número máximo de reintentos ante errores retryables.
    source_key:
        Identificador de la fuente para el campo ``source_key`` de RawContent.
        Si no se pasa, se usa el dominio del ``base_url``.
    """

    def __init__(
        self,
        base_url: str,
        page_size: int = _DEFAULT_PAGE_SIZE,
        timeout: float = _DEFAULT_TIMEOUT,
        extra_headers: dict[str, str] | None = None,
        max_retries: int = _MAX_RETRIES,
        source_key: str | None = None,
        default_path: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.max_retries = max_retries
        # Fix: default_path as constructor param instead of monkey-patching
        # a private attr after construction. Allows _run_source to know the
        # API path without coupling to internal attribute names.
        self.default_path = default_path

        merged_headers = {**_DEFAULT_HEADERS, **(extra_headers or {})}
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=merged_headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )

        # source_key: usa el dominio si no se provee
        self.source_key = source_key or (
            httpx.URL(base_url).host or base_url
        )

    # ------------------------------------------------------------------
    # Método privado: petición con retry
    # ------------------------------------------------------------------

    def _get_with_retry(
        self,
        path: str,
        params: dict[str, Any],
    ) -> httpx.Response:
        """
        Hace GET con exponential backoff.  Lanza ``httpx.HTTPStatusError``
        si se agotan los reintentos.
        """
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.get(path, params=params)

                if resp.status_code in _RETRYABLE_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                    if attempt < self.max_retries:
                        delay = _backoff_delay(attempt)
                        log.warning(
                            "HTTP %s en intento %d/%d — reintento en %.1fs",
                            resp.status_code, attempt, self.max_retries, delay,
                        )
                        time.sleep(delay)
                    else:
                        log.warning(
                            "HTTP %s en intento %d/%d — sin más reintentos",
                            resp.status_code, attempt, self.max_retries,
                        )
                    continue

                resp.raise_for_status()
                return resp

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = _backoff_delay(attempt)
                    log.warning(
                        "%s en intento %d/%d — reintento en %.1fs",
                        type(exc).__name__, attempt, self.max_retries, delay,
                    )
                    time.sleep(delay)
                else:
                    log.warning(
                        "%s en intento %d/%d — sin más reintentos",
                        type(exc).__name__, attempt, self.max_retries,
                    )

        raise RuntimeError(
            f"Máximo de reintentos ({self.max_retries}) alcanzado para {path}"
        ) from last_exc

    # ------------------------------------------------------------------
    # AdapterProtocol: fetch (una sola petición)
    # ------------------------------------------------------------------

    def fetch(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RawContent:
        """
        Descarga una sola URL (path relativo a base_url) y devuelve RawContent.

        Parameters
        ----------
        url:
            Path relativo, p. ej. ``"/api/personas"``.
        params:
            Query params adicionales que se envían tal cual (sin limit/offset).
        """
        query = dict(params or {})
        resp = self._get_with_retry(url, query)

        try:
            data: Any = resp.json()
        except Exception:
            data = resp.text

        return RawContent(
            source_key=self.source_key,
            source_url=str(resp.url),
            fetched_at=_now_utc(),
            http_status=resp.status_code,
            content_type=resp.headers.get("content-type", ""),
            content_hash=_sha256(data),
            raw_content=data,
            page=None,
            total_pages=None,
            offset=None,
            limit=None,
            records_in_page=None,
        )

    # ------------------------------------------------------------------
    # AdapterProtocol: fetch_all (paginación automática)
    # ------------------------------------------------------------------

    def fetch_all(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[RawContent]:
        """
        Itera todas las páginas de un endpoint paginado (limit/offset).

        Algoritmo de parada
        -------------------
        1. Si la respuesta tiene ``"total"`` (int), se detiene cuando
           ``offset >= total``.
        2. Si la lista de datos devuelta está vacía, se detiene.
        3. Si la lista tiene menos registros que ``page_size``, es la última página.

        Yields
        ------
        RawContent
            Un dict por página con ``page`` (base 1) y ``total_pages``.
        """
        extra_params = dict(params or {})
        offset = 0
        page_num = 0
        total: int | None = None
        total_pages: int | None = None

        while True:
            query = {
                "limit": self.page_size,
                "offset": offset,
                **extra_params,
            }

            resp = self._get_with_retry(url, query)

            try:
                data: Any = resp.json()
            except Exception:
                data = resp.text

            page_num += 1

            # ── Intentar extraer la lista de registros y el total ──────────
            records: list[Any] = []
            if isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                # Patrones comunes: {"data": [...], "total": N}
                #                  {"results": [...], "count": N}
                #                  {"items": [...]}
                for key in ("data", "results", "items", "personas", "records"):
                    if key in data and isinstance(data[key], list):
                        records = data[key]
                        break
                else:
                    # Ninguna clave conocida matcheó — distinguir "vacío real"
                    # de "esquema no reconocido" para evitar pérdida silenciosa.
                    if data:
                        log.warning(
                            "%s: dict no vacío en página %d no matchea ninguna "
                            "clave conocida (claves presentes: %s) — "
                            "records quedará vacío; verifica el esquema de la API.",
                            self.source_key, page_num, list(data.keys())[:10],
                        )
                # Intentar extraer total
                for tkey in ("total", "count", "total_count", "totalCount"):
                    if tkey in data and isinstance(data[tkey], int):
                        total = data[tkey]
                        break

            # Calcular total_pages si conocemos el total
            if total is not None and total_pages is None:
                total_pages = (total + self.page_size - 1) // self.page_size

            records_in_page = len(records)

            yield RawContent(
                source_key=self.source_key,
                source_url=str(resp.url),
                fetched_at=_now_utc(),
                http_status=resp.status_code,
                content_type=resp.headers.get("content-type", ""),
                content_hash=_sha256(data),
                raw_content=data,
                page=page_num,
                total_pages=total_pages,
                offset=offset,
                limit=self.page_size,
                records_in_page=records_in_page,
            )

            log.debug(
                "Página %d — offset=%d, registros=%d, total=%s",
                page_num, offset, records_in_page, total,
            )

            # ── Condiciones de parada ──────────────────────────────────────
            if records_in_page == 0:
                log.info("Paginación completa: página vacía en offset=%d", offset)
                break

            offset += records_in_page

            if total is not None and offset >= total:
                log.info(
                    "Paginación completa: %d registros obtenidos de %d",
                    offset, total,
                )
                break

            if records_in_page < self.page_size:
                log.info(
                    "Paginación completa: última página parcial "
                    "(%d < %d) en offset=%d",
                    records_in_page, self.page_size, offset,
                )
                break

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "ApiAdapter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Cierra el cliente httpx subyacente."""
        self._client.close()