from __future__ import annotations

import math
import uuid

_SCORE_TOL = 1e-9


def validate_uuid_str(v: str) -> str:
    """Raise ValueError if ``v`` is not a valid UUID string."""
    try:
        uuid.UUID(v)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("must be a valid UUID string") from exc
    return v


def validate_score_range(v: float) -> float:
    """Raise ValueError unless ``v`` is in [0.0, 1.0].

    Clamps values within ``_SCORE_TOL`` of either bound to tolerate
    float imprecision (e.g. 1.0000000000000002) instead of rejecting
    them outright.
    """
    if math.isclose(v, 0.0, abs_tol=_SCORE_TOL):
        return 0.0
    if math.isclose(v, 1.0, abs_tol=_SCORE_TOL):
        return 1.0
    if not 0.0 <= v <= 1.0:
        raise ValueError("confidence_score must be in [0.0, 1.0]")
    return v
