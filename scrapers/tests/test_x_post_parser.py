from __future__ import annotations

import logging
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models.source import SourceConfig
from scrapers.parsers.x_post_parser import XPostParser
from scrapers.pipelines.run_pipeline import _get_parser

EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def _raw(posts: list[dict[str, Any]]) -> RawContent:
    return RawContent(
        source_key="x_venezuela_crisis_recent",
        source_url="https://api.x.com/2/tweets/search/recent",
        fetched_at="2026-06-30T12:00:00Z",
        http_status=200,
        content_type="application/json",
        content_hash="sha256:demo",
        raw_content={"data": posts, "meta": {}},
        page=1,
        total_pages=None,
        offset=None,
        limit=10,
        records_in_page=len(posts),
    )


def test_parser_maps_missing_post_to_person() -> None:
    parser = XPostParser(event_id=EVENT_ID)
    people = parser.parse(
        _raw(
            [
                {
                    "id": "100",
                    "text": "Se busca JOSE LUIS PEREZ DEMO tras el terremoto.",
                }
            ]
        )
    )

    assert len(people) == 1
    person = people[0]
    assert person.full_name == "Jose Luis Perez Demo"
    assert person.status == "missing"
    assert person.trust_tier == "D"
    assert person.fuente == "X/Twitter public recent search"
    assert person.nota == "[tweet_id:100]"


def test_parser_maps_found_post_to_person() -> None:
    parser = XPostParser(event_id=EVENT_ID)
    people = parser.parse(
        _raw(
            [
                {
                    "id": "101",
                    "text": "MARIA FERNANDA DEMO encontrada y con su familia.",
                }
            ]
        )
    )

    assert len(people) == 1
    assert people[0].full_name == "Maria Fernanda Demo"
    assert people[0].status == "found"


def test_parser_ignores_posts_without_clear_person_signal() -> None:
    parser = XPostParser(event_id=EVENT_ID)
    people = parser.parse(
        _raw(
            [
                {
                    "id": "102",
                    "text": "Reporte general de terremoto sin datos verificables.",
                }
            ]
        )
    )

    assert people == []


def test_parser_does_not_log_pii_text(caplog: Any) -> None:
    parser = XPostParser(event_id=EVENT_ID)
    pii_like_text = "Se busca ANA DEMO celular 0412-000-0000 tras el terremoto."

    with caplog.at_level(logging.WARNING, logger="scrapers.parsers.x_post_parser"):
        people = parser.parse(_raw([{"id": "103", "text": pii_like_text}]))

    assert len(people) == 1
    assert people[0].full_name == "Ana Demo"
    assert "0412-000-0000" not in caplog.text
    assert pii_like_text not in caplog.text


def test_get_parser_registers_x_posts() -> None:
    source = SourceConfig(
        id="x_venezuela_crisis_recent",
        name="X Recent Search Demo",
        type="x_recent_search",
        enabled=False,
        trust_tier="D",
        url="https://api.x.com/2/tweets/search/recent",
        refresh_minutes=10,
        parser_asignado="x_posts",
    )

    parser = _get_parser(source, EVENT_ID)

    assert isinstance(parser, XPostParser)
