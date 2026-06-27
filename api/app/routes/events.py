from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..repositories import PublicRepository, get_repository
from ..schemas import PaginatedEvents

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=PaginatedEvents)
def list_events(
    repository: Annotated[PublicRepository, Depends(get_repository)],
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedEvents:
    return repository.list_events(status=status, limit=limit, offset=offset)
