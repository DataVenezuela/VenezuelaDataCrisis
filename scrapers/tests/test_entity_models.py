from __future__ import annotations

import pytest
from pydantic import ValidationError

from scrapers.models import AcopioCenter, Event, Person


def test_models_importable_from_package():
    assert Person.__name__ == "Person"
    assert AcopioCenter.__name__ == "AcopioCenter"
    assert Event.__name__ == "Event"


def test_person_valid_and_defaults():
    p = Person(full_name="Juan Perez", fuente="encuentralos")
    assert p.status == "desaparecido"
    assert p.verification_status == "unverified"
    assert p.confidence_score == 0.0
    assert p.cedula_hmac is None
    assert p.foto is None


def test_person_type_error_is_clear():
    with pytest.raises(ValidationError):
        Person(full_name=123, fuente="x")  # type: ignore[arg-type]


def test_person_rejects_bad_score_status_and_extra():
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", confidence_score=1.5)
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", status="zombie")
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", unknown_field="x")  # type: ignore[call-arg]


def test_person_rejects_bool_confidence_score():
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", confidence_score=True)  # type: ignore[arg-type]


def test_person_rejects_empty_required_strings():
    with pytest.raises(ValidationError):
        Person(full_name="   ", fuente="s")
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="")


def test_person_age_range_and_masked():
    p = Person(
        full_name="A",
        fuente="s",
        age_range={"min": 30, "max": 40},
        cedula_masked="5678",
    )
    assert p.age_range == {"min": 30, "max": 40}
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", age_range={"min": 50, "max": 40})
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", age_range={"edad": 30})
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", cedula_masked="123456789")
    with pytest.raises(ValidationError):
        Person(full_name="A", fuente="s", cedula_masked="abcd")


def test_acopio_center_valid_and_defaults():
    c = AcopioCenter(name="Refugio", location_text="Caracas", fuente="veneconnect")
    assert c.active is True
    assert c.needs == []
    assert c.coordinates is None


def test_acopio_center_coordinates():
    c = AcopioCenter(
        name="Refugio",
        location_text="Caracas",
        fuente="veneconnect",
        coordinates={"lat": 10.5, "lon": -66.9},
    )
    assert c.coordinates == {"lat": 10.5, "lon": -66.9}
    with pytest.raises(ValidationError):
        AcopioCenter(
            name="R",
            location_text="L",
            fuente="s",
            coordinates={"lat": 200, "lon": 0},
        )
    with pytest.raises(ValidationError):
        AcopioCenter(
            name="R",
            location_text="L",
            fuente="s",
            coordinates={"lat": 10.0},
        )


def test_event_valid_and_date_iso_validation():
    e = Event(
        event_type="earthquake",
        description="sismo",
        fuente="usgs",
        date_iso="2026-06-27T15:00:00Z",
    )
    assert e.location_text is None
    assert e.confidence_score == 0.0
    with pytest.raises(ValidationError):
        Event(event_type="x", description="y", fuente="s", date_iso="not-a-date")
    with pytest.raises(ValidationError):
        Event(event_type="x", description="y", fuente="s", confidence_score=True)  # type: ignore[arg-type]


def test_serialization_round_trip():
    p = Person(full_name="Ana", fuente="src", confidence_score=0.75)
    data = p.model_dump()
    assert data["full_name"] == "Ana"
    assert data["confidence_score"] == 0.75
    assert set(data) == {
        "full_name",
        "cedula_hmac",
        "cedula_masked",
        "age_range",
        "last_known_location",
        "status",
        "verification_status",
        "confidence_score",
        "nota",
        "foto",
        "fuente",
    }
    assert Person.model_validate(p.model_dump()) == p
    assert "Ana" in p.model_dump_json()


def test_existing_sourceconfig_intact():
    from dataclasses import is_dataclass

    from scrapers.models import SourceConfig

    assert SourceConfig.__name__ == "SourceConfig"
    assert is_dataclass(SourceConfig)
