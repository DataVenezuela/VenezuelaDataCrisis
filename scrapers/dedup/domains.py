from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "matching_domains.yaml"

_DEFAULT_DOMAIN = "other"
_DEFAULT_DECISIVE = "type_geo_day"


@lru_cache(maxsize=1)
def _domain_map() -> dict[str, tuple[str, str]]:
    """Construye claim_type -> (dominio, decisive). También indexa por prefijo."""
    payload = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    mapping: dict[str, tuple[str, str]] = {}
    for domain, spec in (payload.get("domains") or {}).items():
        decisive = spec.get("decisive", _DEFAULT_DECISIVE)
        for claim_type in spec.get("claim_types", []):
            mapping[claim_type] = (domain, decisive)
            prefix = claim_type.split(".", 1)[0]
            mapping.setdefault(prefix, (domain, decisive))
    return mapping


def domain_for(claim_type: str | None) -> tuple[str, str]:
    """Devuelve (dominio, decisive) para un claim_type, con fallback por prefijo."""
    if not claim_type:
        return _DEFAULT_DOMAIN, _DEFAULT_DECISIVE
    mapping = _domain_map()
    if claim_type in mapping:
        return mapping[claim_type]
    prefix = claim_type.split(".", 1)[0]
    return mapping.get(prefix, (_DEFAULT_DOMAIN, _DEFAULT_DECISIVE))
