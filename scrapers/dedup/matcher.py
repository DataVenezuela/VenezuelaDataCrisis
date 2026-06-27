from __future__ import annotations

from datetime import date, datetime
from typing import Any

from scrapers.dedup.domains import domain_for


def _day(value: Any) -> str | None:
    """Normaliza fetched_at a 'YYYY-MM-DD' (acepta datetime o ISO string)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else None


def match_key(claim: dict) -> tuple[str, str | None]:
    """Calcula (dominio, llave_decisiva) para un claim.

    La llave es None cuando no hay señal fuerte suficiente: en ese caso el claim
    NO se fusiona (entidad propia). Postura conservadora: precisión > recall."""
    claim_type = claim.get("claim_type")
    domain, decisive = domain_for(claim_type)

    geo = claim.get("geo_code")
    day = _day(claim.get("fetched_at"))
    token = (claim.get("metadata") or {}).get("cedula_hmac")

    if decisive == "identity_or_name_geo":
        # Persona: solo el token de identidad es decisivo. Sin token, no auto-merge.
        if token:
            return domain, f"person|token|{token}"
        return domain, None

    # type_geo_day: requiere zona canónica y día. Sin geo, no auto-merge.
    if geo and day:
        return domain, f"{claim_type}|{geo}|{day}"
    return domain, None
