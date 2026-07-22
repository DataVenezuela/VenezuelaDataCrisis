from __future__ import annotations

from datetime import date
from pathlib import Path

from scrapers.adapters.base import RawContent
from scrapers.models import Person
from scrapers.parsers.base import ParserProtocol
from scrapers.parsers.pfif_parser import PfifParser, _map_status

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pfif_sample.xml"


def _raw_content(payload: object) -> RawContent:
    return RawContent(
        source_key="pfif_demo",
        source_url="https://example.org/pfif/demo-feed.xml",
        fetched_at="2026-06-24T15:30:00Z",
        http_status=200,
        content_type="application/xml",
        content_hash="sha256:test",
        raw_content=payload,
        page=1,
        total_pages=1,
    )


def _parser() -> PfifParser:
    return PfifParser(event_id=_EVENT_ID, reference_date=date(2026, 7, 1))


def _parse_fixture() -> list[Person]:
    return _parser().parse(_raw_content(_FIXTURE_PATH.read_text(encoding="utf-8")))


def test_pfif_parser_satisfies_protocol() -> None:
    assert isinstance(_parser(), ParserProtocol)


def test_pfif_parser_extracts_people_and_skips_nameless_records() -> None:
    people = _parse_fixture()

    assert len(people) == 2
    assert all(isinstance(person, Person) for person in people)


def test_pfif_parser_maps_required_person_fields() -> None:
    person = _parse_fixture()[0]

    assert person.full_name == "Jose Luis Perez Demo"
    assert person.event_id == _EVENT_ID
    assert person.fuente == "Fuente PFIF Demo"
    assert person.status == "missing"
    assert person.last_known_location == "Maracaibo, Zulia"
    assert person.trust_tier == "C"
    assert person.confidence_score == 0.0


def test_pfif_parser_derives_age_range_without_storing_birth_date() -> None:
    person = _parse_fixture()[0]

    assert person.age_range == {"min": 36, "max": 36}
    assert person.is_minor is False
    assert not hasattr(person, "date_of_birth")


def test_pfif_parser_maps_alive_to_found_and_minor_age() -> None:
    person = _parse_fixture()[1]

    assert person.full_name == "Maria Demo"
    assert person.status == "found"
    assert person.last_known_location == "Lara"
    assert person.age_range == {"min": 15, "max": 15}
    assert person.is_minor is True
    assert person.fuente == "pfif"


def test_pfif_parser_scrubs_contact_like_text_from_notes() -> None:
    person = _parse_fixture()[0]

    assert person.nota is not None
    assert "demo@example.org" not in person.nota
    assert "+58" not in person.nota
    assert "V-00000000" not in person.nota
    assert "[email_redacted]" in person.nota
    assert "[phone_redacted]" in person.nota
    assert "[id_redacted]" in person.nota
    assert "mercado demo" in person.nota
    assert "https://example.org/pfif/demo-001" in person.nota


def test_pfif_parser_does_not_store_photo_or_person_id() -> None:
    person = _parse_fixture()[0]

    assert person.foto is None
    assert person.deterministic_id is None
    assert "pfif-demo-001" not in (person.nota or "")


def test_pfif_parser_returns_empty_for_unexpected_payload() -> None:
    assert _parser().parse(_raw_content({"data": []})) == []


def test_pfif_parser_returns_empty_for_malformed_xml() -> None:
    assert _parser().parse(_raw_content("<pfif><person>")) == []


def test_pfif_parser_tolerates_missing_person_record() -> None:
    xml = """
    <pfif xmlns="http://zesty.ca/pfif/1.5">
      <person>
        <full_name>REGISTRO DEMO SIN STATUS</full_name>
        <home_state>Yaracuy</home_state>
      </person>
    </pfif>
    """

    people = _parser().parse(_raw_content(xml))

    assert len(people) == 1
    assert people[0].full_name == "Registro Demo Sin Status"
    assert people[0].status == "unknown"
    assert people[0].age_range is None


def test_pfif_status_mapping() -> None:
    expected: dict[str | None, str] = {
        "missing": "missing",
        "alive": "found",
        "injured": "injured",
        "deceased": "deceased",
        "unknown": "unknown",
        "inaccessible": "unknown",
        "otro": "unknown",
        None: "unknown",
    }

    for raw, status in expected.items():
        assert _map_status(raw) == status


def test_pfif_parser_accepts_bytes_payload() -> None:
    people = _parser().parse(_raw_content(_FIXTURE_PATH.read_bytes()))

    assert [person.full_name for person in people] == ["Jose Luis Perez Demo", "Maria Demo"]
