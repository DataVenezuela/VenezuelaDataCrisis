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
    max_concurrent_posts: int | None = None
    # Allowlist de hosts exactos para `url` (match exacto, case-insensitive).
    # None/ausente = sin restriccion (retrocompatible). Ver run_pipeline._run_source.
    allowed_domains: list[str] | None = None
    # Tope de requests por ventana de 60s. Solo lo aplica ApiAdapter (paginacion);
    # None/ausente = sin limite. Ver scrapers/adapters/_shared.RateLimiter.
    rate_limit_per_minute: int | None = None

    @property
    def parser(self) -> str:
        """Backward-compatible alias for older code/config wording."""
        return self.parser_asignado
