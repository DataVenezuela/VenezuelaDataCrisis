from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv es opcional; el entorno puede traer las vars ya cargadas.
    load_dotenv = None


_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Carga el .env de la raíz del repo si existe y python-dotenv está disponible."""
    if load_dotenv is None:
        return
    env_path = _REPO_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def get_database_url() -> str | None:
    """DSN de Postgres. Local apunta al contenedor; en prod lo da Supabase."""
    load_env()
    return os.environ.get("DATABASE_URL")


def get_pii_hmac_secret() -> str | None:
    """Secreto para tokenizar identidad (cedula_hmac). No debe commitearse."""
    load_env()
    return os.environ.get("PII_HMAC_SECRET")
