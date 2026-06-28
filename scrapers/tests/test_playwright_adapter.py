from __future__ import annotations

import re
from typing import Any

import pytest

from scrapers.adapters.base import AdapterProtocol
from scrapers.adapters.playwright_adapter import (
    PlaywrightAdapter,
    PlaywrightAdapterError,
    PlaywrightNotInstalledError,
    _import_sync_playwright,
)
from scrapers.models.source import SourceConfig


class _FakePage:
    """Doble de prueba para playwright.sync_api.Page — no abre browser real."""

    def __init__(
        self,
        html: str = "<html><body>ok</body></html>",
        final_url: str | None = None,
        fail_times: int = 0,
        error_cls: type[Exception] = TimeoutError,
    ) -> None:
        self._html = html
        self.url = final_url or "unset"
        self._fail_times = fail_times
        self._error_cls = error_cls
        self.goto_calls: list[dict[str, Any]] = []
        self.closed = False

    def goto(self, url: str, *, timeout: float, wait_until: str) -> None:
        self.goto_calls.append({"url": url, "timeout": timeout, "wait_until": wait_until})
        if self.url == "unset":
            self.url = url
        if len(self.goto_calls) <= self._fail_times:
            raise self._error_cls(f"simulated failure for {url}")

    def content(self) -> str:
        return self._html

    def close(self) -> None:
        self.closed = True


def _source_config(
    url: str = "https://example.org/app",
    source_type: str = "webapp_js",
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
) -> SourceConfig:
    return SourceConfig(
        id="webapp_demo",
        name="WebApp demo",
        type=source_type,
        enabled=True,
        trust_tier="C",
        url=url,
        refresh_minutes=60,
        parser_asignado="html",
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(PlaywrightAdapter(page_factory=_FakePage), AdapterProtocol)


def test_fetch_returns_rendered_html_as_raw_content() -> None:
    page = _FakePage(html="<html><body>Hola Caracas</body></html>", final_url="https://example.org/app")
    adapter = PlaywrightAdapter(source_key="webapp_demo", page_factory=lambda: page)

    result = adapter.fetch("https://example.org/app")

    assert result["source_key"] == "webapp_demo"
    assert result["source_url"] == "https://example.org/app"
    assert result["http_status"] == 200
    assert result["content_type"] == "text/html"
    assert result["raw_content"] == "<html><body>Hola Caracas</body></html>"
    assert result["page"] is None
    assert result["total_pages"] is None
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", result["content_hash"])
    assert page.closed is True


def test_fetch_passes_timeout_in_milliseconds_and_wait_until() -> None:
    page = _FakePage()
    adapter = PlaywrightAdapter(timeout=12.5, page_factory=lambda: page)

    adapter.fetch("https://example.org/app", wait_until="networkidle")

    assert page.goto_calls == [
        {"url": "https://example.org/app", "timeout": 12500.0, "wait_until": "networkidle"}
    ]


def test_fetch_all_yields_single_rendered_page() -> None:
    page = _FakePage()
    adapter = PlaywrightAdapter(page_factory=lambda: page)

    results = list(adapter.fetch_all("https://example.org/app"))

    assert len(results) == 1
    assert results[0]["raw_content"] == page.content()


def test_retries_transient_errors_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage(fail_times=2)
    adapter = PlaywrightAdapter(max_retries=5, page_factory=lambda: page)
    monkeypatch.setattr("scrapers.adapters.playwright_adapter.time.sleep", lambda _seconds: None)

    result = adapter.fetch("https://example.org/app")

    assert len(page.goto_calls) == 3
    assert result["raw_content"] == page.content()


def test_exhausts_retries_and_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _FakePage(fail_times=99)
    adapter = PlaywrightAdapter(max_retries=2, page_factory=lambda: page)
    monkeypatch.setattr("scrapers.adapters.playwright_adapter.time.sleep", lambda _seconds: None)

    with pytest.raises(PlaywrightAdapterError, match="No se pudo renderizar"):
        adapter.fetch("https://example.org/app")

    assert len(page.goto_calls) == 2
    assert page.closed is True


def test_from_source_config_uses_timeout_and_retries_from_config() -> None:
    config = _source_config(timeout_seconds=5.0, max_retries=1)

    adapter = PlaywrightAdapter.from_source_config(config, page_factory=_FakePage)

    assert adapter.source_key == "webapp_demo"
    assert adapter.timeout == 5.0
    assert adapter.max_retries == 1


def test_from_source_config_uses_defaults_when_not_set() -> None:
    config = _source_config()

    adapter = PlaywrightAdapter.from_source_config(config, page_factory=_FakePage)

    assert adapter.timeout == 30.0
    assert adapter.max_retries == 5


def test_rejects_max_retries_below_one() -> None:
    with pytest.raises(ValueError, match="max_retries debe ser >= 1"):
        PlaywrightAdapter(max_retries=0, page_factory=_FakePage)


def test_rejects_non_webapp_source_config() -> None:
    config = _source_config(source_type="pdf")

    with pytest.raises(ValueError, match="only supports source types"):
        PlaywrightAdapter.from_source_config(config)


def test_pipeline_registry_returns_playwright_adapter() -> None:
    from scrapers.pipelines.run_pipeline import _get_adapter

    config = _source_config(timeout_seconds=9.0)

    adapter = _get_adapter(config)

    assert isinstance(adapter, PlaywrightAdapter)
    assert adapter.timeout == 9.0


def test_missing_playwright_package_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "playwright.sync_api" or name.startswith("playwright"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    with pytest.raises(PlaywrightNotInstalledError, match="pip install playwright"):
        _import_sync_playwright()


def test_close_is_a_noop_when_browser_was_never_launched() -> None:
    adapter = PlaywrightAdapter(page_factory=_FakePage)
    adapter.close()  # no debe lanzar aunque nunca se haya usado un browser real


def test_context_manager_closes_resources() -> None:
    page = _FakePage()
    with PlaywrightAdapter(page_factory=lambda: page) as adapter:
        adapter.fetch("https://example.org/app")
    # cerrar no debe lanzar y el adapter sigue siendo el mismo objeto
    assert isinstance(adapter, PlaywrightAdapter)
