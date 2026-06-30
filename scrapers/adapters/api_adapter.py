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

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Iterator

import httpx

from scrapers.adapters._shared import backoff_delay, now_utc, sha256_hex
from scrapers.adapters.http_client import USER_AGENT

from .base import RawContent

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes por defecto
# ---------------------------------------------------------------------------

_DEFAULT_PAGE_SIZE = 100
_DEFAULT_TIMEOUT = 30.0          # segundos
_MAX_RETRIES = 5
_DEFAULT_MAX_CONCURRENT_PAGES = 4
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}

_KNOWN_RECORD_KEYS = ("data", "results", "items", "personas", "records")

# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _sha256(obj: Any) -> str:
    """Hash SHA-256 del contenido serializado como JSON compacto."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256_hex(raw.encode("utf-8"))


def _coerce_max_concurrent_pages(value: Any) -> int:
    """Normaliza el limite de concurrencia a un entero seguro."""
    if value is None:
        return _DEFAULT_MAX_CONCURRENT_PAGES
    if isinstance(value, bool):
        return 1
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return 1
    return normalized if normalized > 0 else 1


def _extract_records_and_total(data: Any) -> tuple[list[Any], int | None]:
    """Extrae la lista de registros y el total reportado por APIs comunes."""
    records: list[Any] = []
    total: int | None = None

    if isinstance(data, list):
        return data, None

    if isinstance(data, dict):
        for key in _KNOWN_RECORD_KEYS:
            if key in data and isinstance(data[key], list):
                records = data[key]
                break

        for tkey in ("total", "count", "total_count", "totalCount"):
            value = data.get(tkey)
            if isinstance(value, int) and not isinstance(value, bool):
                total = value
                break

    return records, total


@dataclass(frozen=True)
class _FetchedPage:
    offset: int
    response: httpx.Response
    data: Any
    records: list[Any]
    total: int | None


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
    max_concurrent_pages:
        Número máximo de páginas paginadas solicitadas en paralelo cuando la
        primera respuesta reporta un total confiable.
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
        max_concurrent_pages: int | None = _DEFAULT_MAX_CONCURRENT_PAGES,
        source_key: str | None = None,
        default_path: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.page_size = page_size
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_concurrent_pages = _coerce_max_concurrent_pages(max_concurrent_pages)
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
                        delay = backoff_delay(attempt)
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
                    delay = backoff_delay(attempt)
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
            fetched_at=now_utc(),
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

        Comportamiento de fallos (path paralelo)
        -----------------------------------------
        Si una página falla tras agotar los reintentos, se omite del resultado
        y se emite ``log.error`` con los offsets perdidos al terminar el batch.
        Las páginas vacías que el servidor devuelve cuando ``total`` está
        sobreestimado se descartan silenciosamente (``log.info``).

        Yields
        ------
        RawContent
            Un dict por página con ``page`` (base 1) y ``total_pages``.
        """
        extra_params = dict(params or {})
        first_page = self._fetch_page(url, extra_params, 0)

        if isinstance(first_page.data, dict) and first_page.data and not any(k in first_page.data for k in _KNOWN_RECORD_KEYS):
            self._log_unrecognized_schema(first_page.data, page_num=1)

        total = first_page.total
        total_pages = self._total_pages(total)
        first_records = len(first_page.records)

        yield self._raw_content_from_page(
            first_page,
            page_num=1,
            total_pages=total_pages,
        )

        log.debug(
            "Página %d — offset=%d, registros=%d, total=%s",
            1, first_page.offset, first_records, total,
        )

        if self._is_last_page(
            offset=first_page.offset,
            records_in_page=first_records,
            total=total,
        ):
            return

        if total is None:
            yield from self._fetch_remaining_sequential(url, extra_params, first_records)
            return

        yield from self._fetch_remaining_parallel(url, extra_params, total, total_pages)

    def _fetch_page(
        self,
        url: str,
        extra_params: dict[str, Any],
        offset: int,
    ) -> _FetchedPage:
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
        records, total = _extract_records_and_total(data)
        return _FetchedPage(
            offset=offset,
            response=resp,
            data=data,
            records=records,
            total=total,
        )

    def _raw_content_from_page(
        self,
        page: _FetchedPage,
        *,
        page_num: int,
        total_pages: int | None,
    ) -> RawContent:
        return RawContent(
            source_key=self.source_key,
            source_url=str(page.response.url),
            fetched_at=now_utc(),
            http_status=page.response.status_code,
            content_type=page.response.headers.get("content-type", ""),
            content_hash=_sha256(page.data),
            raw_content=page.data,
            page=page_num,
            total_pages=total_pages,
            offset=page.offset,
            limit=self.page_size,
            records_in_page=len(page.records),
        )

    def _total_pages(self, total: int | None) -> int | None:
        if total is None:
            return None
        return (total + self.page_size - 1) // self.page_size

    def _is_last_page(
        self,
        *,
        offset: int,
        records_in_page: int,
        total: int | None,
    ) -> bool:
        if records_in_page == 0:
            log.info("Paginación completa: página vacía en offset=%d", offset)
            return True

        next_offset = offset + records_in_page
        if total is not None and next_offset >= total:
            log.info(
                "Paginación completa: %d registros obtenidos de %d",
                next_offset, total,
            )
            return True

        if records_in_page < self.page_size:
            log.info(
                "Paginación completa: última página parcial "
                "(%d < %d) en offset=%d",
                records_in_page, self.page_size, offset,
            )
            return True

        return False

    def _fetch_remaining_sequential(
        self,
        url: str,
        extra_params: dict[str, Any],
        first_records: int,
    ) -> Iterator[RawContent]:
        offset = first_records
        page_num = 1
        total: int | None = None
        total_pages: int | None = None

        while True:
            page = self._fetch_page(url, extra_params, offset)
            page_num += 1
            if isinstance(page.data, dict) and page.data and not any(k in page.data for k in _KNOWN_RECORD_KEYS):
                self._log_unrecognized_schema(page.data, page_num=page_num)

            if page.total is not None:
                total = page.total
                total_pages = self._total_pages(total)

            records_in_page = len(page.records)
            yield self._raw_content_from_page(
                page,
                page_num=page_num,
                total_pages=total_pages,
            )

            log.debug(
                "Página %d — offset=%d, registros=%d, total=%s",
                page_num, page.offset, records_in_page, total,
            )

            if self._is_last_page(
                offset=page.offset,
                records_in_page=records_in_page,
                total=total,
            ):
                break

            offset += records_in_page

    def _fetch_remaining_parallel(
        self,
        url: str,
        extra_params: dict[str, Any],
        total: int,
        total_pages: int | None,
    ) -> Iterator[RawContent]:
        offsets = list(range(self.page_size, total, self.page_size))
        results: list[_FetchedPage] = []

        failed_offsets: list[int] = []
        with ThreadPoolExecutor(max_workers=self.max_concurrent_pages) as executor:
            futures = {
                executor.submit(self._fetch_page, url, extra_params, offset): offset
                for offset in offsets
            }
            for future in as_completed(futures):
                offset = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    log.warning(
                        "%s: página offset=%d omitida error_type=%s",
                        self.source_key, offset, type(exc).__name__,
                    )
                    failed_offsets.append(offset)

        if failed_offsets:
            log.error(
                "%s: %d página(s) perdidas tras agotar reintentos — "
                "offsets=%s — el dataset está incompleto.",
                self.source_key, len(failed_offsets), failed_offsets,
            )

        # Acumula todas las páginas en memoria para ordenarlas por offset.
        # Aceptable porque el pipeline actual ya acumula antes de exportar.
        # Si fetch_all se consume en streaming real, reemplazar por un heap de prioridad.
        for page in sorted(results, key=lambda item: item.offset):
            if not page.records:
                log.info(
                    "%s: página offset=%d vacía ignorada (total sobreestimado?)",
                    self.source_key, page.offset,
                )
                continue
            page_num = (page.offset // self.page_size) + 1
            if isinstance(page.data, dict) and page.data and not any(k in page.data for k in _KNOWN_RECORD_KEYS):
                self._log_unrecognized_schema(page.data, page_num=page_num)
            records_in_page = len(page.records)
            yield self._raw_content_from_page(
                page,
                page_num=page_num,
                total_pages=total_pages,
            )
            log.debug(
                "Página %d — offset=%d, registros=%d, total=%s",
                page_num, page.offset, records_in_page, total,
            )

    def _log_unrecognized_schema(self, data: dict[str, Any], *, page_num: int) -> None:
        log.warning(
            "%s: dict no vacío en página %d no matchea ninguna "
            "clave conocida (claves presentes: %s) — "
            "records quedará vacío; verifica el esquema de la API.",
            self.source_key, page_num, list(data.keys())[:10],
        )

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
