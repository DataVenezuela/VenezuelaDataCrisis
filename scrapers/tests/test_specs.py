from __future__ import annotations

import uuid

from scrapers.dedup.fingerprint import FINGERPRINT_VERSION
from scrapers.dedup.specs import (
    ACOPIO_SPEC,
    EVENT_SPEC,
    PERSON_ID_VERSION,
    PERSON_SPEC,
    acopio_block_keys,
    acopio_dedup_key,
    block_keys,
    dedup_key,
    event_dedup_key,
    person_block_keys,
    spec_for_entity_type,
)

_EID = str(uuid.uuid4())


def _person(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "_entity_type": "Person",
        "full_name": "Jose Perez",
        "event_id": _EID,
        "last_known_location": "Vargas",
        "fuente": "src",
    }
    base.update(kw)
    return base


# --- block keys -------------------------------------------------------------

def test_person_with_cedula_has_two_keys_strong_first() -> None:
    keys = person_block_keys(_person(cedula_hmac="abc123"))
    assert len(keys) == 2
    assert keys[0] == f"ced:{_EID}:abc123"
    assert keys[1].startswith(f"phon:{_EID}:")


def test_person_without_cedula_only_phonetic() -> None:
    keys = person_block_keys(_person())
    assert len(keys) == 1
    assert keys[0].startswith(f"phon:{_EID}:")
    assert not any(k.startswith("ced:") for k in keys)


def test_person_blank_cedula_treated_as_absent() -> None:
    keys = person_block_keys(_person(cedula_hmac="   "))
    assert len(keys) == 1
    assert keys[0].startswith("phon:")


def test_acopio_block_key_is_phonetic() -> None:
    rec: dict[str, object] = {"name": "Refugio Norte", "event_id": _EID, "location_text": "Vargas"}
    assert acopio_block_keys(rec)[0].startswith(f"phon:{_EID}:")


def test_block_keys_dispatch() -> None:
    assert block_keys(_person(), "Person")[0].startswith("phon:")
    acopio: dict[str, object] = {"name": "R", "event_id": _EID, "location_text": "Vargas"}
    assert block_keys(acopio, "AcopioCenter")[0].startswith("phon:")
    assert block_keys({}, "Event") == []


# --- specs ------------------------------------------------------------------

def test_person_spec_no_auto_merge() -> None:
    assert PERSON_SPEC.allow_automerge is False
    assert PERSON_SPEC.version == PERSON_ID_VERSION


def test_event_and_acopio_specs_auto_merge() -> None:
    assert EVENT_SPEC.allow_automerge is True
    assert ACOPIO_SPEC.allow_automerge is True
    assert EVENT_SPEC.version == FINGERPRINT_VERSION
    assert ACOPIO_SPEC.version == FINGERPRINT_VERSION


def test_spec_for_entity_type() -> None:
    assert spec_for_entity_type("Event") is EVENT_SPEC
    assert spec_for_entity_type("AcopioCenter") is ACOPIO_SPEC
    assert spec_for_entity_type("Person") is PERSON_SPEC


# --- dedup keys -------------------------------------------------------------

def test_event_dedup_key_ignores_description() -> None:
    a: dict[str, object] = {
        "event_type": "flood",
        "location_text": "Vargas",
        "date_iso": "2026-06-28T15:05:00Z",
        "description": "uno",
    }
    b: dict[str, object] = {
        "event_type": "flood",
        "location_text": "Vargas",
        "date_iso": "2026-06-28T15:50:00Z",
        "description": "dos totalmente distinto",
    }
    assert event_dedup_key(a) == event_dedup_key(b)


def test_acopio_dedup_key_includes_event_id() -> None:
    e1, e2 = str(uuid.uuid4()), str(uuid.uuid4())
    a: dict[str, object] = {"event_id": e1, "name": "Refugio", "location_text": "Vargas"}
    b: dict[str, object] = {"event_id": e2, "name": "Refugio", "location_text": "Vargas"}
    assert acopio_dedup_key(a) != acopio_dedup_key(b)


def test_dedup_key_person_uses_deterministic_id() -> None:
    rec = _person(deterministic_id="detid123")
    assert dedup_key(rec, "Person") == "detid123"
    assert dedup_key(_person(), "Person") is None
