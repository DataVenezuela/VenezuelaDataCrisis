from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from scrapers.adapters.base import AdapterProtocol
from scrapers.adapters.pdf_adapter import (
    PdfAdapter,
    PdfAdapterError,
    PdfTextExtractionError,
)
from scrapers.models.source import SourceConfig


def _pdf_bytes(pages: list[str]) -> bytes:
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    )

    for i, text in enumerate(pages):
        page_obj = 3 + i * 2
        contents_obj = page_obj + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
                f"/BaseFont /Helvetica >> >> >> /Contents {contents_obj} 0 R >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({_escape_pdf_text(text)}) Tj ET".encode()
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode())
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(out)


def _blank_pdf_bytes() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << >> /Contents 4 0 R >>"
        ),
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode())
        out.extend(obj)
        out.extend(b"\nendobj\n")

    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(out)


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _write_pdf(path: Path, pages: list[str]) -> Path:
    path.write_bytes(_pdf_bytes(pages))
    return path


def _source_config(url: str, source_type: str = "pdf") -> SourceConfig:
    return SourceConfig(
        id="pdf_demo",
        name="PDF demo",
        type=source_type,
        enabled=True,
        trust_tier="C",
        url=url,
        refresh_minutes=60,
        parser_asignado="text",
    )


def test_pdf_adapter_satisfies_protocol() -> None:
    assert isinstance(PdfAdapter(), AdapterProtocol)


def test_fetch_local_pdf_returns_text_by_page(tmp_path: Path) -> None:
    pdf_path = _write_pdf(
        tmp_path / "sample.pdf",
        ["Pagina uno demo", "Pagina dos Caracas"],
    )

    result = PdfAdapter(source_key="local_pdf").fetch(str(pdf_path))

    assert result["source_key"] == "local_pdf"
    assert result["source_url"] == str(pdf_path)
    assert result["http_status"] == 200
    assert result["content_type"] == "application/pdf"
    assert result["raw_content"] == ["Pagina uno demo", "Pagina dos Caracas"]
    assert result["page"] is None
    assert result["total_pages"] == 2
    assert result["offset"] is None
    assert result["limit"] is None
    assert result["records_in_page"] is None
    assert result["pages"] == 2
    assert result["extraction_method"] == "pdfplumber"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", result["content_hash"])


def test_fetch_all_yields_one_document(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path / "single.pdf", ["Reporte demo"])

    results = list(PdfAdapter(source_key="local_pdf").fetch_all(str(pdf_path)))

    assert len(results) == 1
    assert results[0]["raw_content"] == ["Reporte demo"]


def test_fetch_source_uses_source_config(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path / "source.pdf", ["Texto de fuente"])
    config = _source_config(str(pdf_path))

    result = PdfAdapter.from_source_config(config).fetch_source(config)

    assert result["source_key"] == "pdf_demo"
    assert result["raw_content"] == ["Texto de fuente"]


def test_pipeline_registry_returns_pdf_adapter(tmp_path: Path) -> None:
    from scrapers.pipelines.run_pipeline import _fetch_pages, _get_adapter

    pdf_path = _write_pdf(tmp_path / "registry.pdf", ["Texto via registry"])
    config = _source_config(str(pdf_path))

    adapter = _get_adapter(config)
    assert isinstance(adapter, PdfAdapter)

    pages = _fetch_pages(adapter, config, "1970-01-01T00:00:00Z")
    assert len(pages) == 1
    assert pages[0]["source_key"] == "pdf_demo"
    assert pages[0]["raw_content"] == ["Texto via registry"]


def test_manual_file_source_config_is_allowed_for_local_pdf(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path / "manual.pdf", ["Archivo manual"])
    config = _source_config(str(pdf_path), source_type="manual_file")

    result = PdfAdapter.from_source_config(config).fetch_source(config)

    assert result["source_key"] == "pdf_demo"
    assert result["raw_content"] == ["Archivo manual"]


def test_rejects_non_pdf_source_config(tmp_path: Path) -> None:
    pdf_path = _write_pdf(tmp_path / "bad_type.pdf", ["Texto"])
    config = _source_config(str(pdf_path), source_type="api_json")

    with pytest.raises(ValueError, match="only supports source types"):
        PdfAdapter.from_source_config(config)


def test_fetch_url_downloads_pdf() -> None:
    pdf_data = _pdf_bytes(["PDF remoto"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=pdf_data,
            request=request,
        )

    result = PdfAdapter(
        source_key="remote_pdf",
        transport=httpx.MockTransport(handler),
    ).fetch("https://example.test/report.pdf")

    assert result["source_key"] == "remote_pdf"
    assert result["source_url"] == "https://example.test/report.pdf"
    assert result["http_status"] == 200
    assert result["raw_content"] == ["PDF remoto"]


def test_url_http_error_has_clear_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    adapter = PdfAdapter(transport=httpx.MockTransport(handler))

    with pytest.raises(PdfAdapterError, match="Error downloading PDF"):
        adapter.fetch("https://example.test/missing.pdf")


def test_missing_local_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="PDF file not found"):
        PdfAdapter().fetch(str(tmp_path / "missing.pdf"))


def test_pdf_without_text_raises_clear_error(tmp_path: Path) -> None:
    pdf_path = tmp_path / "blank.pdf"
    pdf_path.write_bytes(_blank_pdf_bytes())

    with pytest.raises(PdfTextExtractionError, match="OCR is required"):
        PdfAdapter().fetch(str(pdf_path))


def test_invalid_pdf_raises_clear_error(tmp_path: Path) -> None:
    pdf_path = tmp_path / "invalid.pdf"
    pdf_path.write_bytes(b"not a pdf")

    with pytest.raises(PdfAdapterError, match="Could not extract PDF text"):
        PdfAdapter().fetch(str(pdf_path))
