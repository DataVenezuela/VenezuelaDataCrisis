from __future__ import annotations

from typing import Protocol

from .schemas import (
    PaginatedAcopio,
    PaginatedEvents,
    PaginatedPersons,
    PersonDetail,
    StatsResponse,
)


class PublicRepository(Protocol):
    def list_persons(
        self,
        *,
        q: str | None,
        status: str | None,
        event_id: str | None,
        verification_status: str | None,
        limit: int,
        offset: int,
    ) -> PaginatedPersons: ...

    def get_person(self, person_record_id: str) -> PersonDetail | None: ...

    def list_events(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> PaginatedEvents: ...

    def list_acopio(
        self,
        *,
        status: str | None,
        event_id: str | None,
        limit: int,
        offset: int,
    ) -> PaginatedAcopio: ...

    def get_stats(self) -> StatsResponse: ...


class InMemoryPublicRepository:
    def list_persons(
        self,
        *,
        q: str | None,
        status: str | None,
        event_id: str | None,
        verification_status: str | None,
        limit: int,
        offset: int,
    ) -> PaginatedPersons:
        return PaginatedPersons(items=[], limit=limit, offset=offset)

    def get_person(self, person_record_id: str) -> PersonDetail | None:
        return None

    def list_events(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> PaginatedEvents:
        return PaginatedEvents(items=[], limit=limit, offset=offset)

    def list_acopio(
        self,
        *,
        status: str | None,
        event_id: str | None,
        limit: int,
        offset: int,
    ) -> PaginatedAcopio:
        return PaginatedAcopio(items=[], limit=limit, offset=offset)

    def get_stats(self) -> StatsResponse:
        return StatsResponse()


def get_repository() -> PublicRepository:
    return InMemoryPublicRepository()
