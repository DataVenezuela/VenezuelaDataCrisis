from __future__ import annotations


def normalize_location(location: str | None, default_country: str | None = None) -> str | None:
    if location and location.strip():
        return " ".join(location.split()).title()
    return default_country
