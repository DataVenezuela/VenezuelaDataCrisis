from __future__ import annotations

import hashlib

from scrapers.models import AcopioCenter, Event
from scrapers.normalizers.text import normalize_for_match


def build_fingerprint(
    event_id: str,
    claim_type: str,
    location_text: str | None,
    description: str,
) -> str:
    return _hash_normalized_parts(
        normalize_for_match(event_id),
        normalize_for_match(claim_type),
        normalize_for_match(location_text or ""),
        normalize_for_match(description)[:300],
    )


def build_event_fingerprint(event: Event) -> str:
    """Build a stable content fingerprint for Event deduplication."""
    return _hash_normalized_parts(
        normalize_for_match(event.event_type),
        normalize_for_match(event.location_text or ""),
        normalize_for_match(event.date_iso or ""),
        normalize_for_match(event.description)[:300],
    )


def build_acopio_fingerprint(center: AcopioCenter) -> str:
    """Build a stable content fingerprint for AcopioCenter deduplication."""
    return _hash_parts(
        center.name,
        center.location_text,
    )


def build_entity_fingerprint(entity: Event | AcopioCenter) -> str:
    """Dispatch fingerprint generation for supported typed entities."""
    if isinstance(entity, Event):
        return build_event_fingerprint(entity)
    if isinstance(entity, AcopioCenter):
        return build_acopio_fingerprint(entity)
    raise TypeError("build_entity_fingerprint only accepts Event or AcopioCenter")


def _hash_parts(*parts: str) -> str:
    return _hash_normalized_parts(*(normalize_for_match(part) for part in parts))


def _hash_normalized_parts(*parts: str) -> str:
    normalized = "|".join(parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
