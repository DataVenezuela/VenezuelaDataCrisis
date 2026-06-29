from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from scrapers.models._validators import validate_score_range, validate_uuid_str

_PERSON_STATUS = {"missing", "found", "injured", "deceased", "unknown"}
_TRUST_TIERS = {"A", "B", "C", "D"}


class Person(BaseModel):
    """Persona reportada con estado compatible con el schema público."""

    model_config = ConfigDict(extra="forbid")

    full_name: str
    event_id: str
    cedula_hmac: str | None = None
    cedula_masked: str | None = None
    age_range: dict[str, int] | None = None
    is_minor: bool | None = None
    last_known_location: str | None = None
    status: str = "missing"
    verification_status: str = "unverified"
    trust_tier: str = "D"
    confidence_score: float = 0.0
    nota: str | None = None
    foto: str | None = None
    deterministic_id: str | None = None
    fuente: str

    @field_validator("full_name", "fuente")
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
        if v not in _PERSON_STATUS:
            raise ValueError(f"status must be one of {sorted(_PERSON_STATUS)}")
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

    @field_validator("cedula_masked")
    @classmethod
    def _masked_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.strip():
            raise ValueError("cedula_masked must be non-empty when provided")
        if len(v) > 15:
            raise ValueError("cedula_masked holds at most 15 characters")
        return v

    @field_validator("age_range")
    @classmethod
    def _age_range_shape(cls, v: dict[str, int] | None) -> dict[str, int] | None:
        if v is None:
            return v
        if set(v) - {"min", "max"}:
            raise ValueError("age_range only accepts keys 'min' and 'max'")
        lo, hi = v.get("min"), v.get("max")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("age_range['min'] must be <= age_range['max']")
        return v

    @model_validator(mode="after")
    def _infer_is_minor(self) -> Person:
        """Derive is_minor from age_range when not set explicitly."""
        if self.is_minor is not None:
            return self
        if self.age_range is not None:
            hi = self.age_range.get("max")
            lo = self.age_range.get("min")
            if hi is not None and hi < 18:
                self.is_minor = True
            elif lo is not None and lo >= 18:
                self.is_minor = False
        return self
