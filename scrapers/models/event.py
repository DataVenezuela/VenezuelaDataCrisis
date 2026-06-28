from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from scrapers.models._validators import validate_score_range

_TRUST_TIERS = {"A", "B", "C", "D"}
_EVENT_TYPES = {"earthquake", "flood", "landslide", "other"}


class Event(BaseModel):
    """Evento / incidente reportado por una fuente."""

    model_config = ConfigDict(extra="forbid")

    event_type: str
    description: str
    location_text: str | None = None
    date_iso: str | None = None
    trust_tier: str = "D"
    confidence_score: float = 0.0
    fuente: str
    nota: str | None = None

    @field_validator("event_type", "description", "fuente")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("event_type")
    @classmethod
    def _valid_event_type(cls, v: str) -> str:
        if v not in _EVENT_TYPES:
            raise ValueError(f"event_type must be one of {sorted(_EVENT_TYPES)}")
        return v

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _reject_bool_score(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("confidence_score must be a number, not a bool")
        return v

    @field_validator("trust_tier", mode="before")
    @classmethod
    def _valid_trust_tier(cls, v: object) -> str:
        tier = str(v or "").strip().upper()
        if tier not in _TRUST_TIERS:
            raise ValueError(f"trust_tier must be one of {sorted(_TRUST_TIERS)}")
        return tier

    @field_validator("confidence_score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        return validate_score_range(v)

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
