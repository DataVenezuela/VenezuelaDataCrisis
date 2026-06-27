from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class Event(BaseModel):
    """Evento / incidente reportado por una fuente."""

    model_config = ConfigDict(extra="forbid")

    event_type: str
    description: str
    location_text: str | None = None
    date_iso: str | None = None
    confidence_score: float = 0.0
    fuente: str
    nota: str | None = None

    @field_validator("event_type", "description", "fuente")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _reject_bool_score(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("confidence_score must be a number, not a bool")
        return v

    @field_validator("confidence_score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence_score must be in [0.0, 1.0]")
        return v

    @field_validator("date_iso")
    @classmethod
    def _valid_iso(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("date_iso must be a valid ISO-8601 string") from exc
        return v
