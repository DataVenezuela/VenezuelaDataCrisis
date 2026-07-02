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
        left_person: left UUID
        right_person: right UUID
        score: float
        reasons: dict[str, float]
        priority: str ("high"/"medium"/"low")
    """
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for block_key, members in blocks.items():
        if len(members) < 2:
            continue

        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                left = members[i]
                right = members[j]

                left_id = str(left.get("id", ""))
                right_id = str(right.get("id", ""))

                # Canonical ordering to avoid duplicates across blocks
                pair_key = tuple(sorted([left_id, right_id]))  # type: ignore[type-var]
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                score, reasons = similarity_score(left, right)

                if score < threshold:
                    continue

                priority = "high" if score >= 0.95 else "medium"

                candidates.append({
                    "left_person": left_id,
                    "right_person": right_id,
                    "score": score,
                    "reasons": reasons,
                    "priority": priority,
                })

    return candidates
