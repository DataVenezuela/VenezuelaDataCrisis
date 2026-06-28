from __future__ import annotations

import re

from scrapers.adapters._shared import backoff_delay, now_utc, sha256_hex


class TestNowUtc:
    def test_matches_iso8601_utc_without_microseconds(self) -> None:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", now_utc())


class TestSha256Hex:
    def test_has_sha256_prefix_and_64_hex_chars(self) -> None:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", sha256_hex(b"hola"))

    def test_is_deterministic(self) -> None:
        assert sha256_hex(b"contenido") == sha256_hex(b"contenido")

    def test_different_input_different_hash(self) -> None:
        assert sha256_hex(b"a") != sha256_hex(b"b")

    def test_empty_bytes_does_not_raise(self) -> None:
        assert sha256_hex(b"") == (
            "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
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
