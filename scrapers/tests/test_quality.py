from __future__ import annotations

import pytest

from scrapers.models import AcopioCenter, Event, Person
from scrapers.validators.quality import confidence_from_tier, confidence_score

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def test_confidence_from_tier_is_backward_compatible() -> None:
    assert confidence_from_tier("A") == 0.90
    assert confidence_from_tier("E") == 0.15
    assert confidence_from_tier("desconocido") == 0.10


def test_person_complete_tier_a_scores_one() -> None:
    person = Person(
        full_name="Ana Perez",
        event_id=_EVENT_ID,
        cedula_hmac="abc123",
        last_known_location="Caracas",
        status="missing",
        trust_tier="A",
        fuente="hospital",
    )

    assert confidence_score(person) == pytest.approx(1.0)


def test_person_minimal_record_scores_low() -> None:
    person = Person(full_name="Ana Perez", event_id=_EVENT_ID, fuente="grupo")

    assert confidence_score(person) < 0.4


def test_person_cedula_hmac_significantly_increases_score() -> None:
    without_cedula = Person(full_name="Ana Perez", event_id=_EVENT_ID, fuente="grupo")
    with_cedula = Person(
        full_name="Ana Perez", event_id=_EVENT_ID, cedula_hmac="abc123", fuente="grupo"
    )

    assert confidence_score(with_cedula) - confidence_score(without_cedula) >= 0.25


def test_acopio_center_uses_analogous_completeness_formula() -> None:
    center = AcopioCenter(
        name="Centro de Acopio",
        event_id=_EVENT_ID,
        location_text="San Felipe, Yaracuy",
        coordinates={"lat": 10.34, "lon": -68.74},
        needs=["agua", "alimentos"],
        trust_tier="A",
        fuente="cruz-roja",
    )

    assert confidence_score(center) == pytest.approx(1.0)


def test_event_uses_analogous_completeness_formula() -> None:
    event = Event(
        event_type="earthquake",
        description="Sismo reportado",
        location_text="Yaracuy",
        date_iso="2026-06-24T14:32:00Z",
        trust_tier="B",
        fuente="funvisis",
    )

    assert confidence_score(event) == pytest.approx(0.95)


def test_confidence_score_rejects_unknown_entity_type() -> None:
    with pytest.raises(TypeError):
        confidence_score(object())  # type: ignore[arg-type]
