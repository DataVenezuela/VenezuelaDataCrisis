from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PipelineResult:
    source_id: str
    documents: int = 0
    claims: int = 0
    errors: list[str] = field(default_factory=list)
