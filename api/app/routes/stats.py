from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ..repositories import PublicRepository, get_repository
from ..schemas import StatsResponse

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
def get_stats(
    repository: Annotated[PublicRepository, Depends(get_repository)],
) -> StatsResponse:
    return repository.get_stats()
