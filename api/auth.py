import secrets
import os
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from shared.config import API_SECRET_KEY

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    """Verifica la API key inyectada en las cabeceras del request."""
    # Leído dinámicamente de os.environ para posibilitar swapping de llaves en suites de pruebas concurrentes.
    secret_key = os.getenv("API_SECRET_KEY", API_SECRET_KEY)
    
    # compare_digest mitiga ataques de canal lateral por análisis de tiempo de respuesta (Timing Attacks).
    if not api_key or not secrets.compare_digest(api_key, secret_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acceso no autorizado: API Key invalida o ausente en la cabecera X-API-Key.",
        )
    return api_key
