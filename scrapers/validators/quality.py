from __future__ import annotations

from scrapers.sanitizers.pii_detector import detect_pii


def assert_sanitized(text: str) -> bool:
    return len(detect_pii(text)) == 0


def confidence_from_tier(tier: str) -> float:
    return {
        "A": 0.90,
        "B": 0.75,
        "C": 0.60,
        "D": 0.35,
        "E": 0.15,
    }.get((tier or "").upper(), 0.10)
