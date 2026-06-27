from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Claim:
    claim_id: str
    fingerprint: str
    event_id: str
    source_id: str
    source_name: str
    source_url: str
    claim_type: str
    description: str
    location_text: str | None
    confidence_score: float
    verification_status: str
    evidence_text: str
    fetched_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
