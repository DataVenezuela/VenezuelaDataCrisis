"""
Phonetic hash for Venezuelan Spanish names.

Uses Double Metaphone (librería `phonetics`) as primary algorithm,
with a custom Spanish phonetic key as fallback.
"""

from __future__ import annotations

import hashlib
import unicodedata


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def _spanish_phonetic_key(name: str) -> str:
    """Custom phonetic key for Venezuelan Spanish names.

    Rules (applied in order):
    - ñ → ni (BEFORE accent stripping, because NFD分解 turns ñ into n + combining tilde)
    - ll → y
    - ch → x
    - rr → r
    - h → (removed)
    - b ↔ v (b → v)
    - z → s
    - c → s
    - g → j
    - Remove consecutive duplicate characters
    """
    s = name.strip().lower()
    s = s.replace("ñ", "ni")
    s = _strip_accents(s)
    s = s.replace("ll", "y")
    s = s.replace("ch", "x")
    s = s.replace("rr", "r")
    s = s.replace("h", "")
    s = s.replace("b", "v")
    s = s.replace("z", "s")
    s = s.replace("c", "s")
    s = s.replace("g", "j")
    result: list[str] = []
    prev: str | None = None
    for c in s:
        if c != prev:
            result.append(c)
        prev = c
    return "".join(result)


def phonetic_hash(name: str) -> str:
    """Return phonetic hash for a name string.

    Strategy (same as localizalo):
    1. Double Metaphone (primary) — via librería `phonetics`
    2. Spanish phonetic key + SHA256 (fallback)
    3. Plain SHA256 (last resort)
    """
    from phonetics import dmetaphone

    cleaned = _strip_accents(name.strip().lower())
    primary, alternate = dmetaphone(cleaned)
    if primary or alternate:
        return primary or alternate

    spa_key = _spanish_phonetic_key(name)
    if spa_key:
        return hashlib.sha256(("spa:" + spa_key).encode()).hexdigest()[:16]

    return hashlib.sha256(name.encode()).hexdigest()[:16]


def build_deterministic_id(
    phonetic_hash_value: str | None,
    location_normalized: str | None,
) -> str | None:
    """Compute deterministic person ID from phonetic hash and location.

    Returns None if either input is None/empty.
    """
    if not phonetic_hash_value or not location_normalized:
        return None
    key = f"{phonetic_hash_value}|{location_normalized}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]