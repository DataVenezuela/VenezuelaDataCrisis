from __future__ import annotations

import hashlib
import logging

from scrapers.dedup.deduplicator import deduplicate_by_fingerprint, deduplicate_typed_entities
from scrapers.dedup.fingerprint import (
    build_acopio_fingerprint,
    build_event_fingerprint,
    build_fingerprint,
)
from scrapers.models import AcopioCenter, Event
from scrapers.normalizers.text import normalize_for_match

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def test_event_fingerprint_keeps_highest_trust_tier(caplog) -> None:
    low_trust = Event(
        event_type="earthquake",
        description="Sismo demo reportado",
        location_text="Ciudad Demo, Estado Demo",
        date_iso="2026-06-24T14:32:00Z",
        trust_tier="D",
        fuente="source-social-demo",
    )
    high_trust = Event(
        event_type="earthquake",
        description="Sismo demo reportado",
        location_text="Ciudad Demo, Estado Demo",
        date_iso="2026-06-24T14:32:00Z",
        trust_tier="A",
        fuente="source-official-demo",
    )

    caplog.set_level(logging.INFO, logger="scrapers.dedup.deduplicator")
    output, duplicates = deduplicate_typed_entities([low_trust, high_trust])

    assert duplicates == 1
    assert output == [high_trust]
    fingerprint = build_event_fingerprint(high_trust)
    assert _log_text(caplog).find(f"fingerprint={fingerprint}") != -1
    assert "kept_source_id=source-official-demo" in _log_text(caplog)
    assert "discarded_source_id=source-social-demo" in _log_text(caplog)
    assert "winning_tier=A" in _log_text(caplog)


def test_acopio_fingerprint_keeps_highest_trust_tier(caplog) -> None:
    low_trust = AcopioCenter(
        name="Centro de Acopio Demo",
        event_id=_EVENT_ID,
        location_text="Ciudad Demo, Estado Demo",
        trust_tier="C",
        fuente="source-social-demo",
    )
    high_trust = AcopioCenter(
        name="Centro de Acopio Demo",
        event_id=_EVENT_ID,
        location_text="Ciudad Demo, Estado Demo",
        trust_tier="B",
        fuente="source-ngo-demo",
    )

    caplog.set_level(logging.INFO, logger="scrapers.dedup.deduplicator")
    output, duplicates = deduplicate_typed_entities([low_trust, high_trust])

    assert duplicates == 1
    assert output == [high_trust]
    fingerprint = build_acopio_fingerprint(high_trust)
    assert f"fingerprint={fingerprint}" in _log_text(caplog)
    assert "kept_source_id=source-ngo-demo" in _log_text(caplog)
    assert "discarded_source_id=source-social-demo" in _log_text(caplog)
    assert "winning_tier=B" in _log_text(caplog)


def test_typed_dedup_keeps_first_entity_when_trust_tier_ties() -> None:
    first = Event(
        event_type="earthquake",
        description="Sismo demo reportado",
        location_text="Estado Demo",
        date_iso="2026-06-24",
        trust_tier="B",
        fuente="fuente-1",
    )
    second = Event(
        event_type="earthquake",
        description="Sismo demo reportado",
        location_text="Estado Demo",
        date_iso="2026-06-24",
        trust_tier="B",
        fuente="fuente-2",
    )

    output, duplicates = deduplicate_typed_entities([first, second])

    assert duplicates == 1
    assert output == [first]


def test_existing_claim_fingerprint_and_dedup_remain_compatible(tmp_path) -> None:
    description = "Se necesita AGUA!!! " * 40
    fingerprint = build_fingerprint("event", "need.water", "Venezuela", description)
    legacy_normalized = "|".join(
        [
            normalize_for_match("event"),
            normalize_for_match("need.water"),
            normalize_for_match("Venezuela"),
            normalize_for_match(description)[:300],
        ]
    )
    items: list[dict[str, object]] = [
        {"fingerprint": fingerprint, "description": "uno"},
        {"fingerprint": fingerprint, "description": "dos"},
        {"description": "sin fingerprint"},
    ]

    output, duplicates = deduplicate_by_fingerprint(items, db_path=tmp_path / "dedup.db")

    assert fingerprint == hashlib.sha256(legacy_normalized.encode("utf-8")).hexdigest()
    assert duplicates == 1
    assert output == [
        {"fingerprint": fingerprint, "description": "uno"},
        {"description": "sin fingerprint"},
    ]


def _log_text(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records)
