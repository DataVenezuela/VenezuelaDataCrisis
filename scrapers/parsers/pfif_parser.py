"""
Parser PFIF 1.5 offline.

Esta primera capa solo convierte XML PFIF ya descargado en entidades Person.
No hace fetch de red, no activa fuentes reales y no persiste campos de contacto
directo ni fotos.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date
from typing import Any, Protocol

import defusedxml.ElementTree as ET
from defusedxml.ElementTree import ParseError

from scrapers.adapters.base import RawContent
from scrapers.models import Person
from scrapers.normalizers import derive_is_minor, normalize_location, normalize_proper_name, normalize_text

log = logging.getLogger(__name__)

SOURCE_KEY = "pfif"
DEFAULT_FUENTE_LABEL = "pfif"
DEFAULT_TRUST_TIER = "C"

_STATUS_MAP: dict[str, str] = {
    "missing": "missing",
    "alive": "found",
    "injured": "injured",
    "deceased": "deceased",
    "unknown": "unknown",
    "inaccessible": "unknown",
}

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_VENEZUELAN_ID_RE = re.compile(r"\b[VEJG]-?\d{6,9}\b", re.IGNORECASE)


class _ElementLike(Protocol):
    tag: str
    text: str | None

    def iter(self) -> Iterator["_ElementLike"]: ...

    def __iter__(self) -> Iterator["_ElementLike"]: ...


class PfifParser:
    """Parser PFIF 1.5 para payloads XML sinteticos u obtenidos por adapter."""

    source_key: str = SOURCE_KEY

    def __init__(self, event_id: str, reference_date: date | None = None) -> None:
        self._event_id = event_id
        self._reference_date = reference_date or date.today()

    def parse(self, raw: RawContent, **_: Any) -> list[Person]:
        payload = raw.get("raw_content")
        if isinstance(payload, bytes):
            xml_text = payload.decode("utf-8", errors="replace")
        elif isinstance(payload, str):
            xml_text = payload
        else:
            log.warning("%s: raw_content inesperado (tipo %s)", SOURCE_KEY, type(payload).__name__)
            return []

        try:
            root = ET.fromstring(xml_text)
        except (ParseError, ValueError) as exc:
            log.warning("%s: XML PFIF invalido: %s", SOURCE_KEY, exc)
            return []

        people: list[Person] = []
        for node in _person_nodes(root):
            person = self._parse_person(node, raw)
            if person is not None:
                people.append(person)
        return people

    def _parse_person(self, node: _ElementLike, raw: RawContent) -> Person | None:
        full_name = normalize_proper_name(_child_text(node, "full_name"))
        if not full_name:
            log.warning("%s: registro PFIF sin full_name - omitido", SOURCE_KEY)
            return None

        person_record = _first_child(node, "person_record")
        status = _map_status(
            _child_text(person_record, "status") if person_record is not None else None
        )
        age_range = _age_range_from_birth_date(
            _child_text(person_record, "date_of_birth") if person_record is not None else None,
            self._reference_date,
        )
        source_name = normalize_text(_child_text(node, "source_name")) or DEFAULT_FUENTE_LABEL
        source_url = normalize_text(_child_text(node, "source_url")) or normalize_text(_child_text(node, "profile_url"))
        if not source_url:
            source_url = normalize_text(raw.get("source_url"))

        return Person(
            full_name=full_name,
            event_id=self._event_id,
            age_range=age_range,
            is_minor=derive_is_minor(age_range),
            last_known_location=_location_text(node),
            status=status,
            trust_tier=DEFAULT_TRUST_TIER,
            confidence_score=0.0,
            nota=_build_nota(node, person_record, source_url),
            foto=None,
            fuente=source_name,
        )


def _person_nodes(root: _ElementLike) -> list[_ElementLike]:
    return [
        node
        for node in root.iter()
        if _local_name(node.tag) == "person" and _child_text(node, "full_name")
    ]


def _first_child(node: _ElementLike | None, name: str) -> _ElementLike | None:
    if node is None:
        return None
    for child in list(node):
        if _local_name(child.tag) == name:
            return child
    return None


def _child_text(node: _ElementLike | None, name: str) -> str | None:
    child = _first_child(node, name)
    if child is None or child.text is None:
        return None
    text = normalize_text(child.text)
    return text or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _map_status(raw_status: str | None) -> str:
    if not raw_status:
        return "unknown"
    return _STATUS_MAP.get(raw_status.strip().lower(), "unknown")


def _location_text(node: _ElementLike) -> str | None:
    city = normalize_text(_child_text(node, "home_city"))
    state = normalize_text(_child_text(node, "home_state"))
    raw = ", ".join(part for part in (city, state) if part)
    if not raw:
        return None

    loc = normalize_location(raw)
    estado = loc.get("estado")
    municipio = loc.get("municipio")
    if municipio and estado:
        return f"{municipio}, {estado}"
    if estado:
        return str(estado)
    return raw


def _age_range_from_birth_date(raw_birth_date: str | None, reference_date: date) -> dict[str, int] | None:
    if not raw_birth_date:
        return None
    try:
        birth_date = date.fromisoformat(raw_birth_date[:10])
    except ValueError:
        return None
    age = reference_date.year - birth_date.year
    if (reference_date.month, reference_date.day) < (birth_date.month, birth_date.day):
        age -= 1
    if age < 0 or age > 130:
        return None
    return {"min": age, "max": age}


def _build_nota(node: _ElementLike, person_record: _ElementLike | None, source_url: str | None) -> str | None:
    parts: list[str] = []
    for label, value in (
        ("notes", _child_text(node, "notes")),
        ("description", _child_text(person_record, "description")),
        ("source_url", source_url),
    ):
        cleaned = _scrub_contact_text(value)
        if cleaned:
            parts.append(f"[{label}:{cleaned}]")
    return " ".join(parts) if parts else None


def _scrub_contact_text(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _EMAIL_RE.sub("[email_redacted]", text)
    cleaned = _VENEZUELAN_ID_RE.sub("[id_redacted]", cleaned)
    cleaned = _PHONE_RE.sub("[phone_redacted]", cleaned)
    cleaned = normalize_text(cleaned)
    return cleaned or None
