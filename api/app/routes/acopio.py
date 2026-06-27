from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ..repositories import PublicRepository, get_repository
from ..schemas import PaginatedAcopio

router = APIRouter(prefix="/acopio", tags=["acopio"])


@router.get("", response_model=PaginatedAcopio)
def list_acopio(
    repository: Annotated[PublicRepository, Depends(get_repository)],
    status: str | None = None,
    event_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaginatedAcopio:
    return repository.list_acopio(
        status=status,
        event_id=event_id,
        limit=limit,
        offset=offset,
    )
