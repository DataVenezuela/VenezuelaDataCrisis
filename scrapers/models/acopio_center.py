from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AcopioCenter(BaseModel):
    """Centro de acopio / punto de recursos."""

    model_config = ConfigDict(extra="forbid")

    name: str
    location_text: str
    coordinates: dict | None = None
    needs: list[str] = Field(default_factory=list)
    active: bool = True
    fuente: str
    nota: str | None = None

    @field_validator("name", "location_text", "fuente")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("coordinates")
    @classmethod
    def _coords_shape(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        if set(v) != {"lat", "lon"}:
            raise ValueError("coordinates must have exactly keys 'lat' and 'lon'")
        lat, lon = float(v["lat"]), float(v["lon"])
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise ValueError("coordinates out of valid lat/lon range")
        return {"lat": lat, "lon": lon}
