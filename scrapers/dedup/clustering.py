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
        left_person_record_id: left persons.person_record_id
        right_person_record_id: right persons.person_record_id
        blocking_key: block key that produced the candidate
        score: float
        reasons: dict[str, float]
        priority: str ("high"/"medium"/"low")
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

                left_id = _person_record_id(left)
                right_id = _person_record_id(right)
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

                priority = "high" if score >= 0.95 else "medium"

                left_person_record_id, right_person_record_id = sorted([left_id, right_id])

                candidates.append({
                    "event_id": str(left.get("event_id") or right.get("event_id") or ""),
                    "left_person_record_id": left_person_record_id,
                    "right_person_record_id": right_person_record_id,
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


def _person_record_id(person: dict[str, Any]) -> str:
    """Return the ID expected by dedup_candidates FK.

    ``aportes.id`` is the staging row id. The dedup candidate schema points to
    ``persons.person_record_id``, so prefer the explicit projected FK. Current
    staging uses Person ``external_id`` as the deterministic person identity;
    accept that as a compatibility fallback when the backend exposes it instead.
    """
    for key in ("person_record_id", "external_id", "externalId"):
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
