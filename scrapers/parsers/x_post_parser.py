"""
Parser MVP para posts publicos de X/Twitter.

Convierte solo posts con senales claras en entidades Person. No loguea texto
de posts ni intenta inferir PII, edad o ubicacion.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models import Person
from scrapers.normalizers import normalize_proper_name

log = logging.getLogger(__name__)

SOURCE_KEY = "x_posts"
FUENTE_LABEL = "X/Twitter public recent search"
DEFAULT_TRUST_TIER = "D"

_STATUS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("missing", re.compile(r"\b(desaparecid[oa]|se busca|no aparece)\b", re.IGNORECASE)),
    ("found", re.compile(r"\b(encontrad[oa]|apareci[oó]|localizad[oa])\b", re.IGNORECASE)),
    ("injured", re.compile(r"\b(herid[oa]|lesionad[oa])\b", re.IGNORECASE)),
    ("deceased", re.compile(r"\b(fallecid[oa]|muri[oó])\b", re.IGNORECASE)),
)
_NAME_AFTER_SIGNAL = re.compile(
    r"\b(?:se busca|desaparecid[oa]|encontrad[oa]|herid[oa]|lesionad[oa]|"
    r"fallecid[oa])\s+(?:a\s+)?"
    r"(?P<name>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ'-]+"
    r"(?:\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ'-]+){1,4})",
    re.IGNORECASE,
)
_NAME_BEFORE_STATUS = re.compile(
    r"(?P<name>[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ'-]+"
    r"(?:\s+[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ'-]+){1,4})"
    r"\s+(?:esta\s+|est[aá]\s+)?"
    r"(?:desaparecid[oa]|encontrad[oa]|herid[oa]|lesionad[oa]|fallecid[oa])\b",
    re.IGNORECASE,
)
_TRAILING_CONTEXT_WORDS = {
    "tras",
    "por",
    "en",
    "con",
    "durante",
    "luego",
    "y",
    "tlf",
    "celular",
    "contacto",
}


class XPostParser:
    """Parser conservador para posts sinteticos de X."""

    source_key: str = SOURCE_KEY

    def __init__(
        self,
        *,
        event_id: str,
        source_key: str = SOURCE_KEY,
        trust_tier: str = DEFAULT_TRUST_TIER,
    ) -> None:
        self._event_id = event_id
        self.source_key = source_key
        self._trust_tier = trust_tier or DEFAULT_TRUST_TIER

    def parse(self, raw: RawContent, **kwargs: Any) -> list[Person]:
        payload = raw.get("raw_content", {})
        records = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            log.warning("%s: payload sin lista data; pagina omitida", self.source_key)
            return []

        people: list[Person] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            person = self._parse_post(record)
            if person is not None:
                people.append(person)

        log.debug("%s: %d/%d posts parseados", self.source_key, len(people), len(records))
        return people

    def _parse_post(self, post: dict[str, Any]) -> Person | None:
        text = post.get("text")
        if not isinstance(text, str) or not text.strip():
            return None

        status = _status_from_text(text)
        if status is None:
            return None

        full_name = _name_from_text(text)
        if full_name is None:
            return None

        tweet_id = post.get("id")
        nota = f"[tweet_id:{tweet_id}]" if tweet_id is not None else None

        try:
            return Person(
                full_name=full_name,
                event_id=self._event_id,
                status=status,
                trust_tier=self._trust_tier,
                confidence_score=0.0,
                nota=nota,
                fuente=FUENTE_LABEL,
            )
        except Exception as exc:
            log.warning(
                "%s: post omitido por error_type=%s",
                self.source_key,
                type(exc).__name__,
            )
            return None


def _status_from_text(text: str) -> str | None:
    for status, pattern in _STATUS_PATTERNS:
        if pattern.search(text):
            return status
    return None


def _name_from_text(text: str) -> str | None:
    for pattern in (_NAME_AFTER_SIGNAL, _NAME_BEFORE_STATUS):
        match = pattern.search(text)
        if match:
            name = normalize_proper_name(_trim_context_words(match.group("name")))
            if _has_minimum_name_shape(name):
                return name
    return None


def _trim_context_words(raw_name: str) -> str:
    parts = raw_name.split()
    while parts and parts[-1].lower() in _TRAILING_CONTEXT_WORDS:
        parts.pop()
    return " ".join(parts)


def _has_minimum_name_shape(name: str) -> bool:
    parts = [part for part in name.split() if len(part) > 1]
    return len(parts) >= 2
