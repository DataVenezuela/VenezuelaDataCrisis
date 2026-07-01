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
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone


def now_utc() -> str:
    """Timestamp ISO-8601 UTC sin microsegundos, para ``fetched_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(data: bytes) -> str:
    """SHA-256 hexadecimal con el prefijo ``sha256:`` que usa ``content_hash``."""
    return f"{hashlib.sha256(data).hexdigest()}"


def backoff_delay(attempt: int, *, base: float = 1.0, max_delay: float = 60.0) -> float:
    """
    Exponential backoff con jitter completo.

    ``attempt`` empieza en 1.  Formula:
        delay = min(base * 2^(attempt-1), max_delay) + random(0, 1)
    """
    exp: float = base * (2 ** (attempt - 1))
    capped: float = min(exp, max_delay)
    return capped + random.random()


class RateLimiter:
    """Limitador de tasa por ventana deslizante de ``window_seconds``.

    Permite a lo sumo ``max_per_window`` llamadas dentro de cualquier ventana
    de ``window_seconds``. ``wait()`` bloquea (duerme) lo justo para no exceder
    ese tope antes de registrar la llamada actual.

    ``monotonic`` y ``sleep`` se inyectan para poder testear el throttling con
    un reloj falso, sin esperas reales. Por defecto usan ``time.monotonic`` /
    ``time.sleep``.
    """

    def __init__(
        self,
        max_per_window: int,
        *,
        window_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_window <= 0:
            raise ValueError("max_per_window debe ser un entero positivo")
        self._max = max_per_window
        self._window = window_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._hits: deque[float] = deque()

    def _purge(self, now: float) -> None:
        boundary = now - self._window
        while self._hits and self._hits[0] <= boundary:
            self._hits.popleft()

    def wait(self) -> None:
        """Bloquea hasta que registrar una llamada no exceda el tope."""
        now = self._monotonic()
        self._purge(now)
        if len(self._hits) >= self._max:
            # La ventana esta llena: esperar a que la llamada mas antigua salga.
            delay = self._hits[0] + self._window - now
            if delay > 0:
                self._sleep(delay)
                now = self._monotonic()
                self._purge(now)
        self._hits.append(now)
