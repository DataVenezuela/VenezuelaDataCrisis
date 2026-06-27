from __future__ import annotations

from fastapi import FastAPI

from .routes import acopio, events, health, persons, stats
from .settings import settings

app = FastAPI(
    title="VZLA_DEDUP Public API",
    version=settings.version,
    description="Public read API for normalized crisis data in Venezuela.",
)

app.include_router(health.router)
app.include_router(persons.router, prefix="/v1")
app.include_router(events.router, prefix="/v1")
app.include_router(acopio.router, prefix="/v1")
app.include_router(stats.router, prefix="/v1")
