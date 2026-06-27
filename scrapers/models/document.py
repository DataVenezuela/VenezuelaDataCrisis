from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Document:
    source_id: str
    source_name: str
    source_url: str
    title: str | None
    text: str
    raw_hash: str
    trust_tier: str
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
