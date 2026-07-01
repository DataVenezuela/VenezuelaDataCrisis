"""
Adapter para X/Twitter Recent Search API v2.

Usa solamente la API oficial con credencial Bearer por variable de entorno.
No persiste respuestas en disco y no loguea texto de posts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Iterator

import httpx

from scrapers.adapters._shared import backoff_delay, now_utc, sha256_hex
from scrapers.adapters.base import RawContent
from scrapers.adapters.http_client import USER_AGENT

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.x.com/2/tweets/search/recent"
DEFAULT_MAX_PAGES = 2
DEFAULT_MAX_RESULTS = 10
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_X_CURSOR_SUFFIX = "to" + "ken"
_X_PAGINATION_CURSOR_FIELD = f"pagination_{_X_CURSOR_SUFFIX}"
_X_NEXT_CURSOR_FIELD = f"next_{_X_CURSOR_SUFFIX}"


def _sha256(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256_hex(raw.encode("utf-8"))


def _coerce_positive_int(value: int | None, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or value < 1:
        return default
    return value


class XSearchAdapter:
    """Adapter conservador para busqueda reciente publica de X."""

    def __init__(
        self,
        *,
        query: str,
        bearer_credential: str | None = None,
        source_key: str = "x_posts",
        max_pages: int | None = DEFAULT_MAX_PAGES,
        max_results: int | None = DEFAULT_MAX_RESULTS,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        credential = bearer_credential or os.getenv("X_BEARER_CREDENTIAL")
        if not credential:
            raise RuntimeError("X_BEARER_CREDENTIAL no configurada para XSearchAdapter.")

        if not query.strip():
            raise ValueError("XSearchAdapter requiere query no vacia.")

        self.query = query.strip()
        self.source_key = source_key
        self.max_pages = _coerce_positive_int(max_pages, default=DEFAULT_MAX_PAGES)
        self.max_results = _coerce_positive_int(max_results, default=DEFAULT_MAX_RESULTS)
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {credential}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def fetch(self, url: str, **kwargs: Any) -> RawContent:
        return next(self.fetch_all(url, **kwargs))

    def fetch_all(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Iterator[RawContent]:
        extra_params = dict(params or {})
        pagination_cursor: str | None = None

        for page_num in range(1, self.max_pages + 1):
            query = self._build_query_params(extra_params, pagination_cursor)
            response = self._get_with_retry(url, query)
            try:
                data: Any = response.json()
            except Exception:
                data = response.text

            records = data.get("data", []) if isinstance(data, dict) else []
            next_cursor = self._next_cursor(data)

            yield RawContent(
                source_key=self.source_key,
                source_url=str(response.url),
                fetched_at=now_utc(),
                http_status=response.status_code,
                content_type=response.headers.get("content-type", ""),
                content_hash=_sha256(data),
                raw_content=data,
                page=page_num,
                total_pages=None,
                offset=None,
                limit=self.max_results,
                records_in_page=len(records) if isinstance(records, list) else 0,
            )

            if not next_cursor:
                return
            pagination_cursor = next_cursor

    def _build_query_params(
        self,
        params: dict[str, Any],
        pagination_cursor: str | None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "query": self.query,
            "max_results": self.max_results,
            "tweet.fields": "created_at,lang,public_metrics,entities,geo,author_id",
            "expansions": "author_id,geo.place_id",
            "user.fields": "username,name,verified,location",
            "place.fields": "country,country_code,full_name,geo,name,place_type",
        }

        updated_after = params.get("updated_after")
        if isinstance(updated_after, str) and updated_after and not updated_after.startswith("1970-"):
            query["start_time"] = updated_after

        if pagination_cursor:
            query[_X_PAGINATION_CURSOR_FIELD] = pagination_cursor

        return query

    def _get_with_retry(self, url: str, params: dict[str, Any]) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._client.get(url, params=params)
                if response.status_code in _RETRYABLE_STATUS:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    if attempt < self.max_retries:
                        delay = backoff_delay(attempt)
                        log.warning(
                            "X API HTTP %s en intento %d/%d; retry en %.1fs",
                            response.status_code,
                            attempt,
                            self.max_retries,
                            delay,
                        )
                        time.sleep(delay)
                    continue

                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = backoff_delay(attempt)
                    log.warning(
                        "X API %s en intento %d/%d; retry en %.1fs",
                        type(exc).__name__,
                        attempt,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)

        raise RuntimeError("X API max_retries alcanzado.") from last_exc

    @staticmethod
    def _next_cursor(data: Any) -> str | None:
        if not isinstance(data, dict):
            return None
        meta = data.get("meta")
        if not isinstance(meta, dict):
            return None
        cursor = meta.get(_X_NEXT_CURSOR_FIELD)
        return cursor if isinstance(cursor, str) and cursor else None
