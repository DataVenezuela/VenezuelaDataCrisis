from __future__ import annotations

from fastapi import APIRouter

from ..settings import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, bool | str]:
    return {"ok": True, "service": settings.service_name, "version": settings.version}
