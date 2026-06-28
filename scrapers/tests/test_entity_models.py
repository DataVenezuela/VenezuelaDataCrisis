from __future__ import annotations

import pytest
from pydantic import ValidationError

from scrapers.models import AcopioCenter, Event, Person

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def test_models_importable_from_package():
    assert Person.__name__ == "Person"
    assert AcopioCenter.__name__ == "AcopioCenter"
    assert Event.__name__ == "Event"


def test_person_valid_and_defaults():
    p = Person(full_name="Juan Perez", event_id=_EVENT_ID, fuente="encuentralos")
    assert p.status == "missing"
    assert p.verification_status == "unverified"
    assert p.trust_tier == "D"
    assert p.confidence_score == 0.0
    assert p.cedula_hmac is None
    assert p.foto is None
    assert p.is_minor is None


def test_person_is_minor_accepts_true_false_and_none():
    assert Person(full_name="A", event_id=_EVENT_ID, fuente="s", is_minor=True).is_minor is True
    assert Person(full_name="A", event_id=_EVENT_ID, fuente="s", is_minor=False).is_minor is False
    assert Person(full_name="A", event_id=_EVENT_ID, fuente="s", is_minor=None).is_minor is None


def test_person_type_error_is_clear():
    with pytest.raises(ValidationError):
        Person(full_name=123, event_id=_EVENT_ID, fuente="x")  # type: ignore[arg-type]


def test_person_rejects_bad_score_status_and_extra():
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", confidence_score=1.5)
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", status="zombie")
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", unknown_field="x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", trust_tier="Z")


def test_person_rejects_bool_confidence_score():
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", confidence_score=True)  # type: ignore[arg-type]


def test_person_confidence_score_tolerates_float_imprecision_at_bounds():
    p = Person(full_name="A", event_id=_EVENT_ID, fuente="s", confidence_score=1.0000000000000002)
    assert p.confidence_score == 1.0
    p = Person(full_name="A", event_id=_EVENT_ID, fuente="s", confidence_score=-1e-16)
    assert p.confidence_score == 0.0


def test_person_rejects_empty_required_strings():
    with pytest.raises(ValidationError):
        Person(full_name="   ", event_id=_EVENT_ID, fuente="s")
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="")


def test_person_requires_event_id():
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s")  # type: ignore[call-arg]


def test_person_rejects_non_uuid_event_id():
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id="no-es-uuid", fuente="s")


def test_person_age_range_and_masked():
    p = Person(
        full_name="A",
        event_id=_EVENT_ID,
        fuente="s",
        age_range={"min": 30, "max": 40},
        cedula_masked="V-****5821",
    )
    assert p.age_range == {"min": 30, "max": 40}
    assert p.cedula_masked == "V-****5821"
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", age_range={"min": 50, "max": 40})
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", age_range={"edad": 30})
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", cedula_masked="1234567890123456")
    with pytest.raises(ValidationError):
        Person(full_name="A", event_id=_EVENT_ID, fuente="s", cedula_masked="   ")


def test_acopio_center_valid_and_defaults():
    c = AcopioCenter(name="Refugio", event_id=_EVENT_ID, location_text="Caracas", fuente="veneconnect")
    assert c.status == "unverified"
    assert c.needs == []
    assert c.coordinates is None
    assert c.trust_tier == "D"
    assert c.confidence_score == 0.0


def test_acopio_center_requires_event_id():
    with pytest.raises(ValidationError):
        AcopioCenter(name="R", location_text="L", fuente="s")  # type: ignore[call-arg]


def test_acopio_center_rejects_non_uuid_event_id():
    with pytest.raises(ValidationError):
        AcopioCenter(name="R", event_id="no-es-uuid", location_text="L", fuente="s")


def test_acopio_center_rejects_bad_status_and_legacy_active():
    with pytest.raises(ValidationError):
        AcopioCenter(name="R", event_id=_EVENT_ID, location_text="L", fuente="s", status="zombie")
    with pytest.raises(ValidationError):
        AcopioCenter(name="R", event_id=_EVENT_ID, location_text="L", fuente="s", active=True)  # type: ignore[call-arg]


def test_acopio_center_accepts_all_valid_statuses():
    for status in ("active", "full", "closed", "unverified"):
        c = AcopioCenter(name="R", event_id=_EVENT_ID, location_text="L", fuente="s", status=status)
        assert c.status == status


def test_acopio_center_rejects_bool_confidence_score():
    with pytest.raises(ValidationError):
        AcopioCenter(
            name="R", event_id=_EVENT_ID, location_text="L", fuente="s", confidence_score=True
        )  # type: ignore[arg-type]


def test_acopio_center_coordinates():
    c = AcopioCenter(
        name="Refugio",
        event_id=_EVENT_ID,
        location_text="Caracas",
        fuente="veneconnect",
        coordinates={"lat": 10.5, "lon": -66.9},
    )
    assert c.coordinates == {"lat": 10.5, "lon": -66.9}
    with pytest.raises(ValidationError):
        AcopioCenter(
            name="R",
            event_id=_EVENT_ID,
            location_text="L",
            fuente="s",
            coordinates={"lat": 200, "lon": 0},
        )
    with pytest.raises(ValidationError):
        AcopioCenter(
            name="R",
            event_id=_EVENT_ID,
            location_text="L",
            fuente="s",
            coordinates={"lat": 10.0},
        )


def test_acopio_center_coordinates_with_none_lat_raises_validation_error():
    """Regression test for #73: float(None) used to escape as raw TypeError."""
    with pytest.raises(ValidationError):
        AcopioCenter(
            name="R",
            event_id=_EVENT_ID,
            location_text="L",
            fuente="s",
            coordinates={"lat": None, "lon": 5},
        )


def test_event_valid_and_date_iso_validation():
    e = Event(
        event_type="earthquake",
        description="sismo",
        fuente="usgs",
        date_iso="2026-06-27T15:00:00Z",
    )
    assert e.location_text is None
    assert e.trust_tier == "D"
    assert e.confidence_score == 0.0
    with pytest.raises(ValidationError):
        Event(event_type="earthquake", description="y", fuente="s", date_iso="not-a-date")
    with pytest.raises(ValidationError):
        Event(event_type="earthquake", description="y", fuente="s", confidence_score=True)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Event(event_type="demo", description="y", fuente="s")


def test_serialization_round_trip():
    p = Person(full_name="Ana", event_id=_EVENT_ID, fuente="src", confidence_score=0.75)
    data = p.model_dump()
    assert data["full_name"] == "Ana"
    assert data["confidence_score"] == 0.75
    assert set(data) == {
        "full_name",
        "event_id",
        "cedula_hmac",
        "cedula_masked",
        "age_range",
        "is_minor",
        "last_known_location",
        "status",
        "verification_status",
        "trust_tier",
        "confidence_score",
        "nota",
        "foto",
        "deterministic_id",
        "fuente",
    }
    assert Person.model_validate(p.model_dump()) == p
    assert "Ana" in p.model_dump_json()


def test_existing_sourceconfig_intact():
    from dataclasses import is_dataclass

    from scrapers.models import SourceConfig

    assert SourceConfig.__name__ == "SourceConfig"
    assert is_dataclass(SourceConfig)
