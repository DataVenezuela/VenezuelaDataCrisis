"""
scrapers/tests/test_rate_limiter.py
=====================================
Tests del ``RateLimiter`` (scrapers/adapters/_shared.py) y su aplicación en
``ApiAdapter`` durante la paginación (issue #132).

Todos los tests usan un reloj falso (``_FakeClock``) inyectado vía
``monotonic`` / ``sleep``, así que no hay esperas reales ni red real.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scrapers.adapters._shared import RateLimiter
from scrapers.adapters.api_adapter import ApiAdapter


class _FakeClock:
    """Reloj monotónico falso; ``sleep`` solo adelanta el tiempo virtual."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _limiter(clock: _FakeClock, max_per_window: int, window: float = 60.0) -> RateLimiter:
    return RateLimiter(
        max_per_window,
        window_seconds=window,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def _max_in_any_window(timestamps: list[float], window: float = 60.0) -> int:
    """Máximo de timestamps contenidos en cualquier ventana [t, t+window)."""
    worst = 0
    for start in timestamps:
        count = sum(1 for t in timestamps if start <= t < start + window)
        worst = max(worst, count)
    return worst


# ---------------------------------------------------------------------------
# RateLimiter — unidad
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_rejects_non_positive_max(self):
        with pytest.raises(ValueError):
            RateLimiter(0)
        with pytest.raises(ValueError):
            RateLimiter(-1)

    def test_allows_up_to_max_without_sleeping(self):
        clock = _FakeClock()
        limiter = _limiter(clock, max_per_window=3)
        for _ in range(3):
            limiter.wait()
        assert clock.t == 0.0  # las primeras N no esperan

    def test_throttles_when_window_full(self):
        clock = _FakeClock()
        limiter = _limiter(clock, max_per_window=2)
        stamps = []
        for _ in range(6):
            limiter.wait()
            stamps.append(clock.t)
        # Nunca más de 2 llamadas en una misma ventana de 60s.
        assert _max_in_any_window(stamps) <= 2

    def test_old_hits_leave_the_window(self):
        clock = _FakeClock()
        limiter = _limiter(clock, max_per_window=1, window=60.0)
        limiter.wait()            # t=0
        clock.sleep(60.0)         # avanza fuera de la ventana
        limiter.wait()            # no debería esperar: el hit viejo ya salió
        assert clock.t == 60.0


# ---------------------------------------------------------------------------
# ApiAdapter — integración con paginación
# ---------------------------------------------------------------------------

class _TimestampingTransport(httpx.BaseTransport):
    """Endpoint paginado limit/offset que registra el reloj falso por request."""

    def __init__(self, clock: _FakeClock, total: int, page_size: int) -> None:
        self.clock = clock
        self.total = total
        self.page_size = page_size
        self.request_times: list[float] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.request_times.append(self.clock.monotonic())
        params = dict(request.url.params)
        limit = int(params.get("limit", self.page_size))
        offset = int(params.get("offset", 0))
        start = min(offset, self.total)
        end = min(offset + limit, self.total)
        items: list[dict[str, Any]] = [{"id": i} for i in range(start, end)]
        body = json.dumps({"data": items, "total": self.total}).encode()
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)


def test_api_adapter_respects_rate_limit_across_pagination():
    clock = _FakeClock()
    transport = _TimestampingTransport(clock, total=8, page_size=2)  # → 4 requests
    adapter = ApiAdapter(
        base_url="https://mock.test",
        page_size=2,
        source_key="mock",
        rate_limiter=_limiter(clock, max_per_window=2),
    )
    adapter._client = httpx.Client(
        base_url="https://mock.test",
        transport=transport,
        headers=adapter._client.headers,
    )

    pages = list(adapter.fetch_all("/api/personas"))

    assert len(pages) == 4
    assert len(transport.request_times) == 4
    # Criterio de aceptación: no se exceden N=2 requests por ventana de 60s.
    assert _max_in_any_window(transport.request_times) <= 2


def test_api_adapter_without_rate_limiter_does_not_throttle():
    clock = _FakeClock()
    transport = _TimestampingTransport(clock, total=8, page_size=2)
    adapter = ApiAdapter(base_url="https://mock.test", page_size=2, source_key="mock")
    adapter._client = httpx.Client(
        base_url="https://mock.test",
        transport=transport,
        headers=adapter._client.headers,
    )

    list(adapter.fetch_all("/api/personas"))

    # Sin limiter, el reloj falso nunca avanza (no hay sleeps).
    assert clock.t == 0.0
    assert transport.request_times == [0.0, 0.0, 0.0, 0.0]
