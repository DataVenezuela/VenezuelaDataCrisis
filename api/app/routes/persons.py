from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ..repositories import PublicRepository, get_repository
from ..schemas import PaginatedPersons, PersonDetail

router = APIRouter(prefix="/persons", tags=["persons"])


@router.get("", response_model=PaginatedPersons)
def list_persons(
    repository: Annotated[PublicRepository, Depends(get_repository)],
    q: str | None = None,
    status: str | None = None,
    event_id: str | None = None,
    verification_status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedPersons:
    return repository.list_persons(
        q=q,
        status=status,
        event_id=event_id,
        verification_status=verification_status,
        limit=limit,
        offset=offset,
    )


@router.get("/{person_record_id}", response_model=PersonDetail)
def get_person(
    person_record_id: str,
    repository: Annotated[PublicRepository, Depends(get_repository)],
) -> PersonDetail:
    person = repository.get_person(person_record_id)
    if person is None:
        raise HTTPException(status_code=404, detail="person not found")
    return person
