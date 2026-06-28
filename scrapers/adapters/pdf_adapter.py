from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

import httpx
import pdfplumber

from scrapers.adapters._shared import now_utc, sha256_hex
from scrapers.adapters.base import RawContent
from scrapers.adapters.http_client import USER_AGENT
from scrapers.models.source import SourceConfig

_DEFAULT_TIMEOUT = 30.0
_PDF_SOURCE_TYPES = {"pdf", "manual_file"}


class PdfAdapterError(RuntimeError):
    """Base error for PDF adapter failures."""


class PdfTextExtractionError(PdfAdapterError):
    """Raised when a PDF opens but has no extractable text."""


class PdfAdapter:
    """Adapter for local or remote PDF files.

    The adapter extracts text page by page with pdfplumber and returns a single
    RawContent object whose raw_content is list[str], one entry per page.
    """

    def __init__(
        self,
        source_key: str | None = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.source_key = source_key
        self.timeout = timeout
        self.transport = transport
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/pdf,*/*;q=0.8",
            **(headers or {}),
        }

    @classmethod
    def from_source_config(
        cls,
        source_config: SourceConfig,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> "PdfAdapter":
        _validate_pdf_source(source_config)
        return cls(
            source_key=source_config.id,
            timeout=timeout,
            transport=transport,
            headers=headers,
        )

    def fetch(
        self,
        url: str,
        *,
        source_config: SourceConfig | None = None,
        timeout: float | None = None,
        **_: Any,
    ) -> RawContent:
        if source_config is not None:
            _validate_pdf_source(source_config)
            source_key = source_config.id
            url = source_config.url
        else:
            source_key = self.source_key or _source_key_from_ref(url)

        timeout_value = timeout or self.timeout
        if _is_http_url(url):
            pdf_bytes, content_type, http_status, source_url = self._download_pdf(url, timeout_value)
        else:
            pdf_bytes, content_type, http_status, source_url = _read_local_pdf(url)

        pages = _extract_pdf_pages(pdf_bytes, source_url)

        return RawContent(
            source_key=source_key,
            source_url=source_url,
            fetched_at=now_utc(),
            http_status=http_status,
            content_type=content_type,
            content_hash=sha256_hex(pdf_bytes),
            raw_content=pages,
            page=None,
            total_pages=len(pages),
            offset=None,
            limit=None,
            records_in_page=None,
            pages=len(pages),
            extraction_method="pdfplumber",
        )

    def fetch_source(self, source_config: SourceConfig, **kwargs: Any) -> RawContent:
        return self.fetch(source_config.url, source_config=source_config, **kwargs)

    def fetch_all(self, url: str, **kwargs: Any) -> Iterator[RawContent]:
        yield self.fetch(url, **kwargs)

    def _download_pdf(self, url: str, timeout: float) -> tuple[bytes, str, int, str]:
        try:
            with httpx.Client(
                headers=self.headers,
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
                transport=self.transport,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise PdfAdapterError(f"Error downloading PDF from {url}: {exc}") from exc

        return (
            response.content,
            response.headers.get("content-type", "application/pdf"),
            response.status_code,
            str(response.url),
        )


def _validate_pdf_source(source_config: SourceConfig) -> None:
    if source_config.type not in _PDF_SOURCE_TYPES:
        raise ValueError(
            f"PdfAdapter only supports source types {sorted(_PDF_SOURCE_TYPES)!r}; "
            f"got {source_config.type!r}"
        )


def _read_local_pdf(path_value: str) -> tuple[bytes, str, int, str]:
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {path}")
    if not path.is_file():
        raise PdfAdapterError(f"PDF path is not a file: {path}")
    return path.read_bytes(), "application/pdf", 200, str(path)


def _extract_pdf_pages(pdf_bytes: bytes, source_url: str) -> list[str]:
    try:
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "input.pdf"
            tmp_path.write_bytes(pdf_bytes)
            with pdfplumber.open(tmp_path) as pdf:
                pages = [_clean_page_text(page.extract_text() or "") for page in pdf.pages]
    except Exception as exc:
        raise PdfAdapterError(f"Could not extract PDF text from {source_url}: {exc}") from exc

    if not pages or not any(page.strip() for page in pages):
        raise PdfTextExtractionError(
            f"PDF has no extractable text; OCR is required: {source_url}"
        )
    return pages


def _clean_page_text(text: str) -> str:
    return " ".join(text.replace("\ufeff", "").replace("\xef\xbb\xbf", "").split())


def _is_http_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def _source_key_from_ref(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return parsed.netloc
    path = Path(value)
    return path.stem or value


