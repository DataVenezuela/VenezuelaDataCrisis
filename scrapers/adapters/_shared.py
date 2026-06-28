"""
scrapers/adapters/_shared.py
=============================
Helpers internos compartidos por los adapters del pipeline: timestamp UTC,
hashing de contenido para ``RawContent.content_hash`` y backoff exponencial
con jitter para reintentos.

No es parte de ``AdapterProtocol`` (ver ``base.py``) — son utilidades de
implementacion para que cada adapter no reinvente la misma logica.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone


def now_utc() -> str:
    """Timestamp ISO-8601 UTC sin microsegundos, para ``fetched_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(data: bytes) -> str:
    """SHA-256 hexadecimal con el prefijo ``sha256:`` que usa ``content_hash``."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def backoff_delay(attempt: int, *, base: float = 1.0, max_delay: float = 60.0) -> float:
    """
    Exponential backoff con jitter completo.

    ``attempt`` empieza en 1.  Formula:
        delay = min(base * 2^(attempt-1), max_delay) + random(0, 1)
    """
    exp: float = base * (2 ** (attempt - 1))
    capped: float = min(exp, max_delay)
    return capped + random.random()
