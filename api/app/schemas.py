from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocationObject(ApiModel):
    raw: str | None = None
    estado: str | None = None
    municipio: str | None = None
    parroquia: str | None = None
    lat: float | None = None
    lng: float | None = None


class AgeRange(ApiModel):
    min: int | None = None
    max: int | None = None


class PersonPublic(ApiModel):
    person_record_id: str
    event_id: str
    full_name: str | None = None
    alternate_names: list[str] | None = None
    cedula_masked: str | None = None
    age_range: AgeRange | None = None
    sex: Literal["M", "F", "unknown"] | None = None
    is_minor: bool | None = None
    last_known_location: LocationObject | None = None
    status: Literal["missing", "found", "injured", "deceased", "unknown"]
    verification_status: Literal["unverified", "pending", "verified", "conflicting"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    source_url: str | None = None


class PersonNotePublic(ApiModel):
    note_record_id: str
    person_record_id: str
    note_type: Literal["missing", "injured", "found", "deceased"]
    status: Literal["active", "superseded", "retracted"]
    source_date: str | None = None
    entry_date: str
    found: bool | None = None
    last_known_location: LocationObject | None = None


class PersonSourcePublic(ApiModel):
    source_id: str
    person_record_id: str
    source_url: str
    ext_id: str | None = None
    trust_tier: int
    fetched_at: str


class PersonPhotoPublic(ApiModel):
    photo_id: str
    person_record_id: str
    url: str
    caption: str | None = None
    source_id: str | None = None
    uploaded_at: str


class PersonDetail(ApiModel):
    person: PersonPublic
    notes: list[PersonNotePublic] = Field(default_factory=list)
    sources: list[PersonSourcePublic] = Field(default_factory=list)
    photos: list[PersonPhotoPublic] = Field(default_factory=list)


class EventPublic(ApiModel):
    event_id: str
    name: str
    event_type: Literal["earthquake", "flood", "landslide", "other"]
    occurred_at: str
    affected_states: list[str] | None = None
    magnitude: float | None = None
    depth_km: float | None = None
    status: Literal["active", "monitoring", "closed"]
    external_ids: dict[str, str] | None = None


class AcopioCenterPublic(ApiModel):
    acopio_id: str
    event_id: str
    name: str
    location: LocationObject | None = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    status: Literal["active", "full", "closed", "unverified"]
    needs: list[str] | None = None
    last_verified_at: str | None = None
    managing_org: str | None = None
    contact_masked: str | None = None
    capacity: int | None = None
    current_load: int | None = None


class PaginatedPersons(ApiModel):
    items: list[PersonPublic]
    limit: int
    offset: int


class PaginatedEvents(ApiModel):
    items: list[EventPublic]
    limit: int
    offset: int


class PaginatedAcopio(ApiModel):
    items: list[AcopioCenterPublic]
    limit: int
    offset: int


class PersonStats(ApiModel):
    total: int = 0
    missing: int = 0
    found: int = 0
    injured: int = 0
    deceased: int = 0
    unknown: int = 0


class EventStats(ApiModel):
    total: int = 0
    active: int = 0
    monitoring: int = 0
    closed: int = 0


class AcopioStats(ApiModel):
    total: int = 0
    active: int = 0
    full: int = 0
    closed: int = 0
    unverified: int = 0


class StatsResponse(ApiModel):
    persons: PersonStats = Field(default_factory=PersonStats)
    events: EventStats = Field(default_factory=EventStats)
    acopio: AcopioStats = Field(default_factory=AcopioStats)
