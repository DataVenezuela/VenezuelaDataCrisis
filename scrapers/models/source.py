from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceConfig:
    id: str
    name: str
    type: str
    enabled: bool
    trust_tier: str
    url: str
    refresh_minutes: int
    parser: str = "auto"
    required_keywords: list[str] = field(default_factory=list)
    notes: str | None = None
