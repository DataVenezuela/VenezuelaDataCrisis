from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from scrapers.adapters.base import RawContent
from scrapers.adapters.http_client import fetch_url
from scrapers.models.source import SourceConfig
from scrapers.parsers.html_extractor import extract_html_text

HtmlFetcher = Callable[[str, int], tuple[str, str]]

_DEFAULT_TIMEOUT = 25


class HtmlAdapter:
    """Adapter for static HTML sources.

    It fetches one HTML document, extracts readable text with the existing
    BeautifulSoup parser, and returns the standard RawContent shape.
    """

    def __init__(
        self,
        source_key: str | None = None,
        *,
        fetcher: HtmlFetcher = fetch_url,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self.source_key = source_key
        self.fetcher = fetcher
        self.timeout = timeout

    @classmethod
    def from_source_config(
        cls,
        source_config: SourceConfig,
        *,
        fetcher: HtmlFetcher = fetch_url,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> "HtmlAdapter":
        _validate_html_static(source_config)
        return cls(source_key=source_config.id, fetcher=fetcher, timeout=timeout)

    def fetch(
        self,
        url: str,
        *,
        source_config: SourceConfig | None = None,
        timeout: int | None = None,
        **_: Any,
    ) -> RawContent:
        if source_config is not None:
            _validate_html_static(source_config)
            source_key = source_config.id
            url = source_config.url
        else:
            source_key = self.source_key or _source_key_from_url(url)

        html, content_type = self.fetcher(url, timeout or self.timeout)
        title, text = extract_html_text(html)
        title = _clean_extracted_text(title)
        text = _clean_extracted_text(text)
        text = _drop_duplicate_title(text, title)

        return RawContent(
            source_key=source_key,
            source_url=url,
            fetched_at=_now_utc(),
            http_status=200,
            content_type=content_type,
            content_hash=_sha256_text(text),
            raw_content=text,
            page=None,
            total_pages=None,
            html_title=title,
        )

    def fetch_source(self, source_config: SourceConfig, **kwargs: Any) -> RawContent:
        return self.fetch(source_config.url, source_config=source_config, **kwargs)

    def fetch_all(self, url: str, **kwargs: Any) -> Iterator[RawContent]:
        yield self.fetch(url, **kwargs)


def _validate_html_static(source_config: SourceConfig) -> None:
    if source_config.type != "html_static":
        raise ValueError(
            f"HtmlAdapter only supports source type 'html_static'; got {source_config.type!r}"
        )


def _source_key_from_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or url


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_text(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _clean_extracted_text(text: str | None) -> str | None:
    if text is None:
        return None
    return " ".join(text.replace("\ufeff", "").replace("\xef\xbb\xbf", "").split())


def _drop_duplicate_title(text: str, title: str | None) -> str:
    if not title:
        return text
    prefix = f"{title} "
    if text.startswith(prefix):
        return text[len(prefix):]
    if text == title:
        return ""
    return text
