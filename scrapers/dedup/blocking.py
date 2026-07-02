"""Two-level blocking for Person dedup.

Generates up to 2 block keys per person:
- Strong: ced:{event_id}:{cedula_hmac} — only if cedula_hmac exists
- Loose: phon:{event_id}:{phonetic_hash} — only if phonetic_hash exists

Location is intentionally excluded from the loose key: two records
with the same full_name but different location granularity (e.g.
"Caracas" vs "Caracas, Miranda") must land in the same block and
be compared, since similarity_score already accounts for location.
"""

from __future__ import annotations

from typing import Any


def build_blocks(persons: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group persons by their block keys.

    Returns a dict mapping block_key → list of persons that belong to that block.
    A person with both cedula_hmac and phonetic_hash will appear in two blocks.
    """
    blocks: dict[str, list[dict[str, Any]]] = {}

    for person in persons:
        raw_block_keys = person.get("block_keys")
        if isinstance(raw_block_keys, list):
            for key in raw_block_keys:
                if isinstance(key, str) and key.strip():
                    blocks.setdefault(key.strip(), []).append(person)
            continue

        event_id = str(person.get("event_id") or "").strip()
        cedula_hmac = str(person.get("cedula_hmac") or "").strip()
        ph_hash = str(person.get("phonetic_hash") or "").strip()

        for prefix, value in (("ced", cedula_hmac), ("phon", ph_hash)):
            if event_id and value:
                blocks.setdefault(f"{prefix}:{event_id}:{value}", []).append(person)

    return blocks
