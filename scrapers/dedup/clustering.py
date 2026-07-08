"""Pair generation from blocks and similarity scoring.

Given blocks of persons (from blocking.py), generate all unique pairs within
each block, score them, and return candidate pairs that meet the threshold.
"""

from __future__ import annotations

from typing import Any

from scrapers.dedup.similarity import similarity_score


def find_candidates(
    blocks: dict[str, list[dict[str, Any]]],
    threshold: float = 0.85,
) -> list[dict[str, Any]]:
    """Generate and score pairs within each block, returning candidates.

    Returns list of candidate dicts with keys:
        event_id: event UUID
        left_aporte_id: left aporte.id
        right_aporte_id: right aporte.id
        blocking_key: block key that produced the candidate
        score: float
        reasons: dict[str, float]
        priority: int (1 = high, 2 = medium)
    """
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for block_key, members in blocks.items():
        if len(members) < 2:
            continue

        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                left = members[i]
                right = members[j]

                left_id = _aporte_id(left)
                right_id = _aporte_id(right)
                if not left_id or not right_id:
                    continue

                # Canonical ordering to avoid duplicates across blocks
                first_id, second_id = sorted([left_id, right_id])
                pair_key = (first_id, second_id, block_key)
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                score, reasons = similarity_score(left, right)

                if score < threshold:
                    continue

                priority = 1 if score >= 0.95 else 2

                left_aporte_id, right_aporte_id = sorted([left_id, right_id])

                candidates.append({
                    "event_id": str(left.get("event_id") or right.get("event_id") or ""),
                    "left_aporte_id": left_aporte_id,
                    "right_aporte_id": right_aporte_id,
                    "blocking_key": block_key,
                    "source_record_ids": [
                        str(value)
                        for value in (left.get("id"), right.get("id"))
                        if value
                    ],
                    "score": score,
                    "reasons": reasons,
                    "priority": priority,
                })

    return candidates


def _aporte_id(person: dict[str, Any]) -> str:
    """Return ``aportes.id`` (staging row PK) expected by dedup_candidates FK."""
    value = person.get("id")
    return value.strip() if isinstance(value, str) and value.strip() else ""
