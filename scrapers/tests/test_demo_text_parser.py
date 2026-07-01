from __future__ import annotations

from pathlib import Path

from scrapers.adapters.base import RawContent
from scrapers.parsers.demo_text_parser import DemoTextParser

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def _raw_content(payload: object) -> RawContent:
    return RawContent(
        source_key="demo_manual_synthetic",
        source_url="scrapers/sample_data/synthetic_dump.txt",
        fetched_at="2026-06-24T15:30:00Z",
        http_status=200,
        content_type="text/plain",
        content_hash="sha256:test",
        raw_content=payload,
        page=None,
        total_pages=None,
    )


def test_demo_text_parser_extracts_synthetic_person() -> None:
    text = Path("scrapers/sample_data/synthetic_dump.txt").read_text(encoding="utf-8")
    parser = DemoTextParser(event_id=_EVENT_ID)

    people = parser.parse(_raw_content(text))

    assert len(people) == 1
    person = people[0]
    assert person.full_name == "Juan Demo"
    assert person.event_id == _EVENT_ID
    assert person.age_range == {"min": 35, "max": 35}
    assert person.is_minor is False
    assert person.last_known_location == "Lara"
    assert person.status == "missing"
    assert person.cedula_hmac is None


def test_demo_text_parser_returns_empty_for_unexpected_payload() -> None:
    parser = DemoTextParser(event_id=_EVENT_ID)

    assert parser.parse(_raw_content({"data": []})) == []


def test_demo_text_parser_returns_empty_without_demo_name() -> None:
    parser = DemoTextParser(event_id=_EVENT_ID)

    assert parser.parse(_raw_content("Datos sintéticos sin registro de persona.")) == []
