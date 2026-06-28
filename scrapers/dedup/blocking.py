"""Blocking for Person deduplication.

Groups Person records by blocking keys to reduce O(n²) comparisons.
Primary key: deterministic_id (same phonetic hash + same location).
Fallback: name_key + last_known_location for records without deterministic_id.
"""

from __future__ import annotations

from scrapers.normalizers.person import name_key


def build_blocks(persons: list[dict]) -> dict[str, list[dict]]:
    """Group persons by blocking key.
    
    Returns dict of block_key -> list of person dicts in that block.
    Only blocks with 2+ persons are useful for comparison.
    """
    blocks: dict[str, list[dict]] = {}
    
    for person in persons:
        key = _blocking_key(person)
        blocks.setdefault(key, []).append(person)
    
    return blocks


def _blocking_key(person: dict) -> str:
    """Compute blocking key for a person record.
    
    Priority:
    1. deterministic_id (if present) — same phonetic hash + same location
    2. name_key + last_known_location — fallback
    """
    det_id = person.get("deterministic_id")
    if det_id:
        return f"det:{det_id}"
    
    nk = name_key(person.get("full_name", ""))
    loc = (person.get("last_known_location") or "").lower().strip()
    return f"name:{nk}|loc:{loc}"
