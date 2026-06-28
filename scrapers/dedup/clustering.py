"""Clustering for Person deduplication.

Within each block, compare all pairs and produce DedupCandidate records.
Uses single-linkage: if A~B and B~C, all 3 are in the same cluster.
"""

from __future__ import annotations

from scrapers.dedup.similarity import similarity_score


def find_candidates(
    blocks: dict[str, list[dict]],
    threshold: float = 0.75,
) -> list[dict]:
    """Compare pairs within blocks, return candidate dicts above threshold.
    
    Returns list of candidate dicts with:
    - left_id: deterministic_id or full_name of first person
    - right_id: deterministic_id or full_name of second person
    - score: similarity score
    - reasons: list of scoring reasons
    - blocking_key: which block they came from
    - decision: "pending" (always — no auto-merge)
    """
    candidates: list[dict] = []
    
    for block_key, persons in blocks.items():
        if len(persons) < 2:
            continue
        
        seen_pairs: set[tuple[str, str]] = set()
        
        for i in range(len(persons)):
            for j in range(i + 1, len(persons)):
                left = persons[i]
                right = persons[j]
                
                pair_key = _pair_key(left, right)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                
                score, reasons = similarity_score(left, right)
                
                if score >= threshold:
                    candidates.append({
                        "left_id": left.get("deterministic_id") or left.get("full_name", "?"),
                        "right_id": right.get("deterministic_id") or right.get("full_name", "?"),
                        "score": score,
                        "reasons": reasons,
                        "blocking_key": block_key,
                        "decision": "pending",
                    })
    
    return candidates


def _pair_key(left: dict, right: dict) -> tuple[str, str]:
    """Deterministic pair key regardless of order."""
    lid = left.get("deterministic_id") or left.get("full_name", "")
    rid = right.get("deterministic_id") or right.get("full_name", "")
    return tuple(sorted([lid, rid]))
