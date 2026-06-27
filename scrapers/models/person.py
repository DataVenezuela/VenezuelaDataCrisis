from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

_PERSON_STATUS = {"desaparecido", "encontrado", "fallecido"}


class Person(BaseModel):
    """Persona reportada (desaparecida/encontrada/fallecida)."""

    model_config = ConfigDict(extra="forbid")

    full_name: str
    cedula_hmac: str | None = None
    cedula_masked: str | None = None
    age_range: dict | None = None
    last_known_location: str | None = None
    status: str = "desaparecido"
    verification_status: str = "unverified"
    confidence_score: float = 0.0
    nota: str | None = None
    foto: str | None = None
    fuente: str

    @field_validator("full_name", "fuente")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in _PERSON_STATUS:
            raise ValueError(f"status must be one of {sorted(_PERSON_STATUS)}")
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

    @field_validator("cedula_masked")
    @classmethod
    def _masked_shape(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 4:
            raise ValueError("cedula_masked holds at most the last 4 digits")
        if not v.isdigit():
            raise ValueError("cedula_masked must contain only digits")
        return v

    @field_validator("age_range")
    @classmethod
    def _age_range_shape(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if set(v) - {"min", "max"}:
            raise ValueError("age_range only accepts keys 'min' and 'max'")
        lo, hi = v.get("min"), v.get("max")
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("age_range['min'] must be <= age_range['max']")
        return v
