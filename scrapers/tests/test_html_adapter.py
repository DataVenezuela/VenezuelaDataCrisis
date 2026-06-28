from __future__ import annotations

import re

import pytest

from scrapers.adapters import AdapterProtocol
from scrapers.adapters.html_adapter import HtmlAdapter
from scrapers.models.source import SourceConfig


HTML_SAMPLE = """
<!doctype html>
<html>
  <head>
    <title>Reporte Demo</title>
    <style>.hidden { display: none; }</style>
    <script>window.hiddenValue = "not returned";</script>
  </head>
  <body>
    <header>Menu institucional</header>
    <nav>Links</nav>
    <main>
      <h1>Centro Demo</h1>
      <p>Se reciben insumos en Caracas.</p>
    </main>
    <footer>Pie de pagina</footer>
  </body>
</html>
"""


def _source_config(source_type: str = "html_static") -> SourceConfig:
    return SourceConfig(
        id="html_demo",
        name="HTML Demo",
        type=source_type,
        enabled=True,
        trust_tier="B",
        url="https://example.test/demo",
        refresh_minutes=30,
        parser_asignado="html",
    )


def _fake_fetcher(html: str = HTML_SAMPLE, content_type: str = "text/html; charset=utf-8"):
    calls: list[tuple[str, int]] = []

    def fetcher(url: str, timeout: int) -> tuple[str, str]:
        calls.append((url, timeout))
        return html, content_type

    return fetcher, calls


def test_html_adapter_satisfies_protocol() -> None:
    adapter = HtmlAdapter(source_key="demo")

    assert isinstance(adapter, AdapterProtocol)


def test_fetch_returns_standard_raw_content_with_extracted_text() -> None:
    fetcher, calls = _fake_fetcher()
    adapter = HtmlAdapter(source_key="demo_source", fetcher=fetcher, timeout=7)

    result = adapter.fetch("https://example.test/demo")

    assert calls == [("https://example.test/demo", 7)]
    assert result["source_key"] == "demo_source"
    assert result["source_url"] == "https://example.test/demo"
    assert result["http_status"] == 200
    assert result["content_type"] == "text/html; charset=utf-8"
    assert result["html_title"] == "Reporte Demo"
    assert result["raw_content"] == "Centro Demo Se reciben insumos en Caracas."
    assert result["page"] is None
    assert result["total_pages"] is None


def test_fetch_drops_non_content_html_sections() -> None:
    fetcher, _calls = _fake_fetcher()
    adapter = HtmlAdapter(source_key="demo_source", fetcher=fetcher)

    result = adapter.fetch("https://example.test/demo")

    assert "Menu institucional" not in result["raw_content"]
    assert "Links" not in result["raw_content"]
    assert "Pie de pagina" not in result["raw_content"]
    assert "window" not in result["raw_content"]


def test_fetch_sets_hash_and_timestamp_formats() -> None:
    fetcher, _calls = _fake_fetcher()
    adapter = HtmlAdapter(source_key="demo_source", fetcher=fetcher)

    result = adapter.fetch("https://example.test/demo")

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", result["content_hash"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result["fetched_at"])


def test_fetch_infers_source_key_from_url_when_not_configured() -> None:
    fetcher, _calls = _fake_fetcher()
    adapter = HtmlAdapter(fetcher=fetcher)

    result = adapter.fetch("https://example.test/demo")

    assert result["source_key"] == "example.test"


def test_fetch_source_uses_html_static_source_config() -> None:
    fetcher, calls = _fake_fetcher()
    config = _source_config()
    adapter = HtmlAdapter.from_source_config(config, fetcher=fetcher)

    result = adapter.fetch_source(config)

    assert calls == [(config.url, 25)]
    assert result["source_key"] == "html_demo"
    assert result["source_url"] == config.url
    assert result["html_title"] == "Reporte Demo"


def test_pipeline_registry_uses_html_adapter_for_html_static() -> None:
    from scrapers.pipelines.run_pipeline import _get_adapter

    adapter = _get_adapter(_source_config())

    assert isinstance(adapter, HtmlAdapter)


def test_pipeline_registry_keeps_rss_static_adapter() -> None:
    from scrapers.pipelines.run_pipeline import _get_adapter

    adapter = _get_adapter(_source_config(source_type="rss"))

    assert not isinstance(adapter, HtmlAdapter)


def test_fetch_accepts_source_config_keyword() -> None:
    fetcher, calls = _fake_fetcher()
    config = _source_config()
    adapter = HtmlAdapter(fetcher=fetcher)

    result = adapter.fetch("https://ignored.test", source_config=config, timeout=3)

    assert calls == [(config.url, 3)]
    assert result["source_key"] == config.id
    assert result["source_url"] == config.url


def test_rejects_non_html_static_source_config() -> None:
    fetcher, _calls = _fake_fetcher()
    config = _source_config(source_type="api_json")

    with pytest.raises(ValueError, match="html_static"):
        HtmlAdapter.from_source_config(config, fetcher=fetcher)


def test_fetch_all_yields_single_raw_content_for_static_html() -> None:
    fetcher, _calls = _fake_fetcher()
    adapter = HtmlAdapter(source_key="demo_source", fetcher=fetcher)

    results = list(adapter.fetch_all("https://example.test/demo"))

    assert len(results) == 1
    assert results[0]["raw_content"] == "Centro Demo Se reciben insumos en Caracas."


def test_empty_html_returns_empty_text_and_null_title() -> None:
    fetcher, _calls = _fake_fetcher(html="")
    adapter = HtmlAdapter(source_key="demo_source", fetcher=fetcher)

    result = adapter.fetch("https://example.test/demo")

    assert result["raw_content"] == ""
    assert result["html_title"] is None


@pytest.mark.parametrize("bom", ["\ufeff", "\xef\xbb\xbf"])
def test_html_with_utf8_bom_does_not_keep_bom_or_duplicate_title(bom: str) -> None:
    fetcher, _calls = _fake_fetcher(html=f"{bom}{HTML_SAMPLE}")
    adapter = HtmlAdapter(source_key="demo_source", fetcher=fetcher)

    result = adapter.fetch("https://example.test/demo")

    assert result["html_title"] == "Reporte Demo"
    assert result["raw_content"] == "Centro Demo Se reciben insumos en Caracas."
    assert "\ufeff" not in result["raw_content"]
