from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.storage import init_db
from api.routes.records import router as records_router

app = FastAPI(
    title="VZLA_DEDUP API",
    description="API abierta para consultar reportes y necesidades de la crisis humanitaria en Venezuela de manera saneada y deduplicada.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """Inicializa la base de datos y crea las tablas en el arranque."""
    init_db()


@app.get("/")
def read_root():
    return {
        "status": "online",
        "api_name": "VZLA_DEDUP API",
        "docs_url": "/docs",
        "openapi_url": "/openapi.json"
    }


app.include_router(records_router)
