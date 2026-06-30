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
    parser_asignado: str = "auto"
    required_keywords: list[str] = field(default_factory=list)
    notes: str | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    page_size: int | None = None
    max_concurrent_pages: int | None = None

    @property
    def parser(self) -> str:
        """Backward-compatible alias for older code/config wording."""
        return self.parser_asignado
