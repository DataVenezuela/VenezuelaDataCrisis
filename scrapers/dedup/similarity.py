"""Person similarity scoring for dedup candidate evaluation.

Multi-field weighted scoring with:
- Jaro-Winkler on full_name (via jellyfish)
- Cedula binary match with veto
- Partial location scoring (same city=1.0, same state=0.5, different=0.0)
- Age range overlap
- Status binary match
"""

from __future__ import annotations

from typing import Any

from jellyfish import jaro_winkler_similarity as jaro_winkler

from scrapers.normalizers.text import normalize_for_match

# ---------------------------------------------------------------------------
# Weights (sum must be 1.0)
# ---------------------------------------------------------------------------
_NAME_WEIGHT = 0.40
_CEDULA_WEIGHT = 0.30
_LOCATION_WEIGHT = 0.15
_AGE_WEIGHT = 0.10
_STATUS_WEIGHT = 0.05


# ---------------------------------------------------------------------------
# Individual field scorers
# ---------------------------------------------------------------------------


def _name_score(left_name: str, right_name: str) -> float:
    """Jaro-Winkler similarity on normalized names."""
    a = normalize_for_match(left_name)
    b = normalize_for_match(right_name)
    if not a or not b:
        return 0.0
    return jaro_winkler(a, b)


def _parse_location(loc: str | None) -> tuple[str, str]:
    """Parse a 'City, State' string into (city, state).

    Returns empty strings for missing or unparseable input.
    """
    if not loc or not isinstance(loc, str):
        return ("", "")
    parts = [p.strip() for p in loc.split(",", 1)]
    if len(parts) == 2:
        city, state = parts
        return (normalize_for_match(city), normalize_for_match(state))
    return (normalize_for_match(parts[0]), "")


def _location_score(left_loc: str | None, right_loc: str | None) -> float:
    """Partial location scoring.

    Same city + same state → 1.0
    Same state only → 0.5
    Different or missing → 0.0
    """
    if not left_loc or not right_loc:
        return 0.0
    left_city, left_state = _parse_location(left_loc)
    right_city, right_state = _parse_location(right_loc)

    if not left_state or not right_state:
        return 0.0

    if left_city == right_city and left_state == right_state:
        return 1.0
    if left_state == right_state:
        return 0.5
    return 0.0


def _age_overlap_score(age_a: dict[str, int] | None, age_b: dict[str, int] | None) -> float:
    """Score age range overlap as proportion of the union."""
    if not age_a or not age_b:
        return 0.0

    a_min = age_a.get("min")
    a_max = age_a.get("max")
    b_min = age_b.get("min")
    b_max = age_b.get("max")

    # At least one range must be fully defined
    if a_min is None or a_max is None or b_min is None or b_max is None:
        return 0.0

    # Overlap
    overlap_min = max(a_min, b_min)
    overlap_max = min(a_max, b_max)
    overlap = max(0, overlap_max - overlap_min)

    # Union
    union_min = min(a_min, b_min)
    union_max = max(a_max, b_max)
    union = union_max - union_min

    if union <= 0:
        return 0.0
    return overlap / union


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------


def similarity_score(
    left: dict[str, Any],
    right: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """Compute weighted similarity score for two person records.

    Returns (total_score, reasons_dict) where:
    - total_score is a float in [0.0, 1.0]
    - reasons_dict has per-field scores like {"nombre": 0.35, "cedula": 0.0, ...}

    Cedula veto: if both have cedula_hmac and they differ, total_score = 0.0
    with all reasons zeroed.
    """
    left_cedula: str | None = left.get("cedula_hmac") or None
    right_cedula: str | None = right.get("cedula_hmac") or None

    # --- Cedula veto: both present and different → hard zero ---
    if left_cedula and right_cedula and left_cedula != right_cedula:
        return (0.0, {"nombre": 0.0, "cedula": 0.0, "ubicacion": 0.0, "edad": 0.0, "status": 0.0})

    # --- Per-field scores ---
    name_sc = _name_score(
        str(left.get("full_name", "")), str(right.get("full_name", ""))
    )
    cedula_sc = 1.0 if left_cedula and right_cedula and left_cedula == right_cedula else 0.0
    location_sc = _location_score(
        left.get("last_known_location"),
        right.get("last_known_location"),
    )
    age_sc = _age_overlap_score(
        left.get("age_range"),
        right.get("age_range"),
    )
    status_sc = 1.0 if str(left.get("status", "unknown")) == str(right.get("status", "unknown")) else 0.0

    # --- Weighted total ---
    weighted = [
        ("nombre", _NAME_WEIGHT, name_sc),
        ("cedula", _CEDULA_WEIGHT, cedula_sc),
        ("ubicacion", _LOCATION_WEIGHT, location_sc),
        ("edad", _AGE_WEIGHT, age_sc),
        ("status", _STATUS_WEIGHT, status_sc),
    ]
    total = round(sum(w * s for _, w, s in weighted), 4)
    reasons = {field: round(w * s, 4) for field, w, s in weighted}
    return total, reasons
