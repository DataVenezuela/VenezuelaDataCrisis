from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from scrapers.models._validators import validate_score_range, validate_uuid_str

_TRUST_TIERS = {"A", "B", "C", "D"}
_ACOPIO_STATUSES = {"active", "full", "closed", "unverified"}


class AcopioCenter(BaseModel):
    """Centro de acopio / punto de recursos."""

    model_config = ConfigDict(extra="forbid")

    name: str
    event_id: str
    location_text: str
    coordinates: dict[str, float] | None = None
    needs: list[str] = Field(default_factory=list)
    status: str = "unverified"
    trust_tier: str = "D"
    confidence_score: float = 0.0
    fuente: str
    nota: str | None = None

    @field_validator("name", "location_text", "fuente")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("event_id")
    @classmethod
    def _valid_event_id(cls, v: str) -> str:
        return validate_uuid_str(v)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in _ACOPIO_STATUSES:
            raise ValueError(f"status must be one of {sorted(_ACOPIO_STATUSES)}")
        return v

    @field_validator("trust_tier", mode="before")
    @classmethod
    def _valid_trust_tier(cls, v: object) -> str:
        tier = str(v or "").strip().upper()
        if tier not in _TRUST_TIERS:
            raise ValueError(f"trust_tier must be one of {sorted(_TRUST_TIERS)}")
        return tier

    @field_validator("confidence_score", mode="before")
    @classmethod
    def _reject_bool_score(cls, v: object) -> object:
        if isinstance(v, bool):
            raise ValueError("confidence_score must be a number, not a bool")
        return v

    @field_validator("confidence_score")
    @classmethod
    def _score_range(cls, v: float) -> float:
        return validate_score_range(v)

    @field_validator("coordinates")
    @classmethod
    def _coords_shape(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        if set(v) != {"lat", "lon"}:
            raise ValueError("coordinates must have exactly keys 'lat' and 'lon'")
        # lat/lon ya llegan coercionados a float por la anotación de tipo
        # dict[str, float] — pydantic-core rechaza valores no numéricos
        # (incluido None) con ValidationError antes de llegar aquí (#73).
        lat, lon = v["lat"], v["lon"]
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise ValueError("coordinates out of valid lat/lon range")
        return {"lat": lat, "lon": lon}
