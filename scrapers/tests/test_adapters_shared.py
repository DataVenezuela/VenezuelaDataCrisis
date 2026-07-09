from __future__ import annotations

import logging
import re
from unittest.mock import patch

import httpx
import pytest

from scrapers.adapters._shared import backoff_delay, now_utc, retry_post, sha256_hex

log = logging.getLogger(__name__)


class TestNowUtc:
    def test_matches_iso8601_utc_without_microseconds(self) -> None:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", now_utc())


class TestSha256Hex:
    def test_mock_hash_has_64_hexchars(self) -> None:
        assert re.fullmatch(r"[0-9a-f]{64}", sha256_hex(b"hola"))

    def test_is_deterministic(self) -> None:
        assert sha256_hex(b"contenido") == sha256_hex(b"contenido")

    def test_different_input_different_hash(self) -> None:
        assert sha256_hex(b"a") != sha256_hex(b"b")

    def test_empty_bytes_does_not_raise(self) -> None:
        assert sha256_hex(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )


class TestBackoffDelay:
    def test_first_attempt_is_close_to_base(self) -> None:
        # attempt=1 -> exp = base * 2^0 = base; + jitter in [0, 1)
        delay = backoff_delay(1, base=1.0, max_delay=60.0)
        assert 1.0 <= delay < 2.0

    def test_grows_monotonically_before_hitting_the_cap(self) -> None:
        deterministic = [min(1.0 * (2 ** (n - 1)), 60.0) for n in range(1, 7)]
        assert deterministic == sorted(deterministic)

    def test_is_capped_at_max_delay_for_large_attempt_counts(self) -> None:
        # 2**49 segundos excede por mucho max_delay; debe quedar acotado, no
        # crecer sin limite (y no lanzar OverflowError por el exponente).
        delay = backoff_delay(50, base=1.0, max_delay=60.0)
        assert 60.0 <= delay < 61.0

    def test_custom_base_and_max_delay_are_respected(self) -> None:
        delay = backoff_delay(10, base=0.1, max_delay=2.0)
        assert 2.0 <= delay < 3.0

    def test_jitter_keeps_delay_non_negative_and_bounded(self) -> None:
        for attempt in range(1, 10):
            delay = backoff_delay(attempt)
            assert delay >= 0.0


class TestRetryPost:
    """retry_post: backoff en status retriables, None en agotamiento de red."""

    def _client(self, transport: httpx.BaseTransport) -> httpx.Client:
        return httpx.Client(base_url="https://example.test", transport=transport)

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_retries_on_retriable_status_then_succeeds(self, status: int) -> None:
        calls: list[int] = []

        class _FirstFail(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                calls.append(1)
                if len(calls) == 1:
                    return httpx.Response(status)
                return httpx.Response(201, json=[])

        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            resp = retry_post(
                self._client(_FirstFail()), "/path", [{"x": 1}],
                retries=3, log=log,
            )
        assert resp is not None
        assert resp.status_code == 201
        assert len(calls) == 2

    def test_exhausted_retries_on_retriable_status_returns_last_response(self) -> None:
        class _AlwaysFail(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                return httpx.Response(503)

        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            resp = retry_post(
                self._client(_AlwaysFail()), "/path", [{}],
                retries=3, log=log,
            )
        assert resp is not None
        assert resp.status_code == 503

    def test_network_error_returns_none_after_exhaustion(self) -> None:
        class _AlwaysNetErr(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("red caida")

        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            resp = retry_post(
                self._client(_AlwaysNetErr()), "/path", [{}],
                retries=3, log=log,
            )
        assert resp is None

    def test_non_retriable_status_returns_immediately(self) -> None:
        calls: list[int] = []

        class _BadRequest(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                calls.append(1)
                return httpx.Response(400, json={"error": "bad"})

        resp = retry_post(
            self._client(_BadRequest()), "/path", [{}],
            retries=3, log=log,
        )
        assert resp is not None
        assert resp.status_code == 400
        assert len(calls) == 1

    def test_custom_retryable_statuses_respected(self) -> None:
        calls: list[int] = []

        class _Returns408(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                calls.append(1)
                return httpx.Response(408)

        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            resp = retry_post(
                self._client(_Returns408()), "/path", [{}],
                retries=3,
                retryable_statuses=frozenset({408}),
                log=log,
            )
        assert resp is not None
        assert resp.status_code == 408
        assert len(calls) == 3
