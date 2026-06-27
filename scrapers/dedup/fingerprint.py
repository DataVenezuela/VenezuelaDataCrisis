from __future__ import annotations

import hashlib

from scrapers.normalizers.text import normalize_for_match


def build_fingerprint(
    event_id: str,
    claim_type: str,
    location_text: str | None,
    description: str,
) -> str:
    normalized = "|".join(
        [
            normalize_for_match(event_id),
            normalize_for_match(claim_type),
            normalize_for_match(location_text or ""),
            normalize_for_match(description)[:300],
        ]
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
