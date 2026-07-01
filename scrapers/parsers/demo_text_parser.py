"""
Parser concreto para la fuente demo sintética local.

Este parser existe solo para que el quickstart procese una fuente offline con
datos ficticios. No es un fallback genérico para texto libre: depende del
formato controlado de ``scrapers/sample_data/synthetic_dump.txt``.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models import Person
from scrapers.normalizers import derive_is_minor, normalize_location, normalize_proper_name

log = logging.getLogger(__name__)

SOURCE_KEY = "demo_manual_synthetic"
FUENTE_LABEL = "demo.synthetic.local"
DEFAULT_TRUST_TIER = "C"

_NAME_RE = re.compile(r"\bFamilia\s+Demo\s+busca\s+a\s+(?P<name>[^,\n]+)", re.IGNORECASE)
_AGE_RE = re.compile(r"\b(?P<age>\d{1,3})\s+a(?:ñ|n)os\b", re.IGNORECASE)


class DemoTextParser:
    """Parser de demostración para el fixture sintético local."""

    source_key: str = SOURCE_KEY

    def __init__(self, event_id: str) -> None:
        self._event_id = event_id

    def parse(self, raw: RawContent, **_: Any) -> list[Person]:
        payload = raw.get("raw_content")
        if not isinstance(payload, str):
            log.warning("%s: raw_content inesperado (tipo %s)", SOURCE_KEY, type(payload).__name__)
            return []

        person = self._parse_person(payload)
        return [person] if person is not None else []

    def _parse_person(self, text: str) -> Person | None:
        name_match = _NAME_RE.search(text)
        if name_match is None:
            log.warning("%s: fixture sin nombre demo reconocible", SOURCE_KEY)
            return None

        full_name = normalize_proper_name(name_match.group("name"))
        if not full_name:
            return None

        age_range = _extract_age_range(text)
        last_known_location = _extract_location(text)

        return Person(
            full_name=full_name,
            event_id=self._event_id,
            age_range=age_range,
            is_minor=derive_is_minor(age_range),
            last_known_location=last_known_location,
            status="missing",
            trust_tier=DEFAULT_TRUST_TIER,
            confidence_score=0.0,
            nota="Registro sintético del quickstart local.",
            fuente=FUENTE_LABEL,
        )


def _extract_age_range(text: str) -> dict[str, int] | None:
    match = _AGE_RE.search(text)
    if match is None:
        return None
    age = int(match.group("age"))
    if age < 0 or age > 130:
        return None
    return {"min": age, "max": age}


def _extract_location(text: str) -> str | None:
    # El fixture demo termina con ", Lara."; si cambia, fallamos suave.
    if "Lara" not in text:
        return None
    loc = normalize_location("Lara")
    estado = loc.get("estado")
    return str(estado) if estado else "Lara"
