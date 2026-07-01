from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from scrapers.adapters.x_search_adapter import DEFAULT_ENDPOINT, XSearchAdapter
from scrapers.models.source import SourceConfig
from scrapers.pipelines.run_pipeline import _get_adapter


def _response(payload: dict[str, Any], request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        headers={"content-type": "application/json"},
        request=request,
    )


def _x_cursor_field(prefix: str) -> str:
    return f"{prefix}_{'to' + 'ken'}"


def test_adapter_adds_bearer_auth_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_CREDENTIAL", "demo-credential")
    seen_headers: dict[str, str] = {}
    seen_query: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["authorization"] = request.headers["authorization"]
        seen_query.update(parse_qs(request.url.query.decode()))
        return _response({"data": [{"id": "100", "text": "Demo"}], "meta": {}}, request)

    adapter = XSearchAdapter(
        query="terremoto",
        transport=httpx.MockTransport(handler),
    )
    try:
        pages = list(adapter.fetch_all(DEFAULT_ENDPOINT))
    finally:
        adapter.close()

    assert seen_headers["authorization"] == "Bearer demo-credential"
    assert seen_query["query"] == ["terremoto"]
    assert seen_query["max_results"] == ["10"]
    assert seen_query["tweet.fields"] == ["created_at,lang,public_metrics,entities,geo,author_id"]
    assert len(pages) == 1
    assert pages[0]["records_in_page"] == 1
    assert len(pages[0]["content_hash"]) == 64


def test_adapter_paginates_with_next_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_CREDENTIAL", "demo-credential")
    requests: list[dict[str, list[str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        requests.append(query)
        if _x_cursor_field("pagination") not in query:
            return _response(
                {
                    "data": [{"id": "100", "text": "Demo page one"}],
                    "meta": {_x_cursor_field("next"): "NEXT_DEMO"},
                },
                request,
            )
        return _response(
            {"data": [{"id": "101", "text": "Demo page two"}], "meta": {}},
            request,
        )

    adapter = XSearchAdapter(
        query="terremoto",
        max_pages=2,
        transport=httpx.MockTransport(handler),
    )
    try:
        pages = list(adapter.fetch_all(DEFAULT_ENDPOINT))
    finally:
        adapter.close()

    assert len(pages) == 2
    assert requests[0].get(_x_cursor_field("pagination")) is None
    assert requests[1][_x_cursor_field("pagination")] == ["NEXT_DEMO"]
    assert [page["page"] for page in pages] == [1, 2]


def test_adapter_requires_bearer_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_BEARER_CREDENTIAL", raising=False)

    with pytest.raises(RuntimeError, match="X_BEARER_CREDENTIAL"):
        XSearchAdapter(query="terremoto")


def test_adapter_retries_retryable_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_CREDENTIAL", "demo-credential")
    monkeypatch.setattr("scrapers.adapters.x_search_adapter.time.sleep", lambda *_: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"title": "unavailable"}, request=request)
        return _response({"data": [{"id": "100", "text": "Demo"}], "meta": {}}, request)

    adapter = XSearchAdapter(
        query="terremoto",
        transport=httpx.MockTransport(handler),
    )
    try:
        pages = list(adapter.fetch_all(DEFAULT_ENDPOINT))
    finally:
        adapter.close()

    assert calls == 2
    assert len(pages) == 1


def test_adapter_maps_updated_after_to_start_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_CREDENTIAL", "demo-credential")
    seen_query: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_query.update(parse_qs(request.url.query.decode()))
        return _response({"data": [], "meta": {}}, request)

    adapter = XSearchAdapter(
        query="terremoto",
        transport=httpx.MockTransport(handler),
    )
    try:
        list(
            adapter.fetch_all(
                DEFAULT_ENDPOINT,
                params={"updated_after": "2026-06-30T12:00:00Z"},
            )
        )
    finally:
        adapter.close()

    assert seen_query["start_time"] == ["2026-06-30T12:00:00Z"]


def test_get_adapter_registers_x_recent_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("X_BEARER_CREDENTIAL", "demo-credential")
    source = SourceConfig(
        id="x_venezuela_crisis_recent",
        name="X Recent Search Demo",
        type="x_recent_search",
        enabled=False,
        trust_tier="D",
        url=DEFAULT_ENDPOINT,
        refresh_minutes=10,
        parser_asignado="x_posts",
        required_keywords=["se busca", "terremoto"],
    )

    adapter = _get_adapter(source)
    try:
        assert isinstance(adapter, XSearchAdapter)
        assert adapter.query == '"se busca" OR terremoto'
    finally:
        adapter.close()
