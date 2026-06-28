from __future__ import annotations

import uuid

from scrapers.dedup.fingerprint import (
    FINGERPRINT_VERSION,
    _truncate_to_hour,
    build_acopio_fingerprint,
    build_event_fingerprint,
)
from scrapers.models import AcopioCenter, Event


def test_version_is_v1() -> None:
    assert FINGERPRINT_VERSION == "v1"


def test_truncate_to_hour_basic() -> None:
    assert _truncate_to_hour("2026-06-28T15:05:00Z") == "2026-06-28T15"
    assert _truncate_to_hour("2026-06-28T15:55:00Z") == "2026-06-28T15"


def test_truncate_to_hour_graceful_on_invalid() -> None:
    assert _truncate_to_hour(None) == ""
    assert _truncate_to_hour("") == ""
    assert _truncate_to_hour("no es una fecha") == ""


def test_event_ignores_description() -> None:
    a = Event(
        event_type="flood",
        description="rio crecido",
        location_text="Vargas",
        date_iso="2026-06-28T15:05:00Z",
        fuente="s1",
    )
    b = Event(
        event_type="flood",
        description="inundacion severa",
        location_text="Vargas",
        date_iso="2026-06-28T15:55:00Z",
        fuente="s2",
    )
    # Misma hora truncada + mismo tipo/location, description distinta -> mismo fp
    assert build_event_fingerprint(a) == build_event_fingerprint(b)


def test_event_hour_boundary_splits() -> None:
    a = Event(
        event_type="flood",
        description="x",
        location_text="Vargas",
        date_iso="2026-06-28T15:59:00Z",
        fuente="s",
    )
    b = Event(
        event_type="flood",
        description="x",
        location_text="Vargas",
        date_iso="2026-06-28T16:00:00Z",
        fuente="s",
    )
    assert build_event_fingerprint(a) != build_event_fingerprint(b)


def test_event_cross_source_same_dedup_hash() -> None:
    """Dos fuentes que reportan el mismo evento -> mismo dedup_hash."""
    a = Event(
        event_type="earthquake",
        description="reportado por la fuente A",
        location_text="Sucre",
        date_iso="2026-06-28T09:12:00Z",
        trust_tier="C",
        fuente="fuente-a",
    )
    b = Event(
        event_type="earthquake",
        description="otra redaccion totalmente distinta",
        location_text="Sucre",
        date_iso="2026-06-28T09:48:00Z",
        trust_tier="A",
        fuente="fuente-b",
    )
    assert build_event_fingerprint(a) == build_event_fingerprint(b)


def test_acopio_includes_event_id() -> None:
    e1, e2 = str(uuid.uuid4()), str(uuid.uuid4())
    a = AcopioCenter(name="Refugio", event_id=e1, location_text="Vargas", fuente="s")
    b = AcopioCenter(name="Refugio", event_id=e2, location_text="Vargas", fuente="s")
    assert build_acopio_fingerprint(a) != build_acopio_fingerprint(b)


def test_acopio_cross_source_same_dedup_hash() -> None:
    eid = str(uuid.uuid4())
    a = AcopioCenter(
        name="Refugio Norte",
        event_id=eid,
        location_text="Vargas",
        status="active",
        trust_tier="C",
        fuente="fuente-a",
    )
    b = AcopioCenter(
        name="Refugio Norte",
        event_id=eid,
        location_text="Vargas",
        status="full",
        trust_tier="A",
        fuente="fuente-b",
    )
    assert build_acopio_fingerprint(a) == build_acopio_fingerprint(b)
