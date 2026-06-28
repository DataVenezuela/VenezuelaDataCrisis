"""Pairwise similarity scoring for Person records.

Uses pure-Python Jaro-Winkler (no external dependencies).
Multi-field scoring: name (0.4), cedula_hmac (0.3), location (0.15), age (0.1), status (0.05).
"""

from __future__ import annotations


def jaro_winkler(s1: str, s2: str, p: float = 0.1) -> float:
    """Compute Jaro-Winkler similarity between two strings.
    
    Pure-Python implementation. Returns 0.0 to 1.0.
    """
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    
    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0
    
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    
    matches = 0
    transpositions = 0
    
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    
    if matches == 0:
        return 0.0
    
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    
    jaro = (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3
    
    # Common prefix (up to 4 chars)
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    
    return jaro + prefix * p * (1 - jaro)


# Scoring weights
_NAME_WEIGHT = 0.40
_CEDULA_WEIGHT = 0.30
_LOCATION_WEIGHT = 0.15
_AGE_WEIGHT = 0.10
_STATUS_WEIGHT = 0.05


def _name_similarity(left: str, right: str) -> float:
    """Jaro-Winkler on normalized full_name."""
    return jaro_winkler(left.lower().strip(), right.lower().strip())


def _cedula_match(left: str | None, right: str | None) -> float:
    """Binary: 1.0 if both cedula_hmac match and are not None, 0.0 otherwise."""
    if left and right and left == right:
        return 1.0
    return 0.0


def _location_match(left: str | None, right: str | None) -> float:
    """Exact match on normalized location string."""
    if left and right and left.lower().strip() == right.lower().strip():
        return 1.0
    return 0.0


def _age_compatible(left: dict | None, right: dict | None) -> float:
    """Check if age ranges overlap."""
    if not left or not right:
        return 0.5  # Unknown — neutral score
    l_min, l_max = left.get("min", 0), left.get("max", 150)
    r_min, r_max = right.get("min", 0), right.get("max", 150)
    if l_min <= r_max and r_min <= l_max:
        return 1.0
    return 0.0


def _status_match(left: str, right: str) -> float:
    """1.0 if same status, 0.0 otherwise."""
    return 1.0 if left == right else 0.0


def similarity_score(left: dict, right: dict) -> tuple[float, list[str]]:
    """Compare two Person records.
    
    Returns (score, list_of_reasons) where score is 0.0 to 1.0.
    """
    reasons: list[str] = []
    
    name_sim = _name_similarity(left.get("full_name", ""), right.get("full_name", ""))
    score = name_sim * _NAME_WEIGHT
    reasons.append(f"name={name_sim:.2f}")
    
    ced = _cedula_match(left.get("cedula_hmac"), right.get("cedula_hmac"))
    score += ced * _CEDULA_WEIGHT
    if ced == 1.0:
        reasons.append("cedula=match")
    
    loc = _location_match(left.get("last_known_location"), right.get("last_known_location"))
    score += loc * _LOCATION_WEIGHT
    if loc == 1.0:
        reasons.append("location=match")
    
    age = _age_compatible(left.get("age_range"), right.get("age_range"))
    score += age * _AGE_WEIGHT
    reasons.append(f"age={age:.2f}")
    
    st = _status_match(left.get("status", ""), right.get("status", ""))
    score += st * _STATUS_WEIGHT
    if st == 1.0:
        reasons.append("status=match")
    
    return round(score, 4), reasons
