from __future__ import annotations

import logging
import time

import httpx

from scrapers.adapters._shared import backoff_delay

log = logging.getLogger(__name__)

USER_AGENT = "VZLA_DEDUP_Scraper/0.3 (+public-interest emergency-data-cleanup)"
_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}

_MAX_RETRIES = 5
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _build_client() -> httpx.Client:
    return httpx.Client(
        headers=_DEFAULT_HEADERS,
        follow_redirects=True,
    )


_CLIENT = _build_client()


def fetch_url(url: str, timeout: int = 25) -> tuple[str, str]:
    """Fetch *url* with retry/backoff.  Returns ``(body, content_type)``."""
    last_exc: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            response = _CLIENT.get(url, timeout=timeout)

            if response.status_code in _RETRYABLE_STATUS:
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
                if attempt < _MAX_RETRIES:
                    delay = backoff_delay(attempt)
                    log.warning(
                        "HTTP %s en intento %d/%d — reintento en %.1fs",
                        response.status_code, attempt, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                continue

            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            return response.text, content_type

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                delay = backoff_delay(attempt)
                log.warning(
                    "%s en intento %d/%d — reintento en %.1fs",
                    type(exc).__name__, attempt, _MAX_RETRIES, delay,
                )
                time.sleep(delay)

    raise RuntimeError(
        f"Máximo de reintentos ({_MAX_RETRIES}) alcanzado para {url}"
    ) from last_exc
