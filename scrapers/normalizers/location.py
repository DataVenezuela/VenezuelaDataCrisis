from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

import requests

from scrapers.normalizers.text import expand_abbreviations, normalize_for_match, normalize_text

LocationObject: TypeAlias = dict[str, str | float | None]
Geocoder: TypeAlias = Callable[[str, float, str], tuple[float, float] | None]

DEFAULT_OSM_USER_AGENT = "VZLA_DEDUP/0.1 location-normalizer"
DEFAULT_COUNTRY = "Venezuela"
OSM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"

_STATES = {
    "Amazonas",
    "Anzoategui",
    "Apure",
    "Aragua",
    "Barinas",
    "Bolivar",
    "Carabobo",
    "Cojedes",
    "Delta Amacuro",
    "Dependencias Federales",
    "Distrito Capital",
    "Falcon",
    "Guarico",
    "La Guaira",
    "Lara",
    "Merida",
    "Miranda",
    "Monagas",
    "Nueva Esparta",
    "Portuguesa",
    "Sucre",
    "Tachira",
    "Trujillo",
    "Yaracuy",
    "Zulia",
}

_STATE_ALIASES = {
    "caracas": "Distrito Capital",
    "dtto capital": "Distrito Capital",
    "distrito capital": "Distrito Capital",
    "capital": "Distrito Capital",
    "vargas": "La Guaira",
    "la guaira": "La Guaira",
}

for _state in _STATES:
    _state_key = normalize_for_match(_state)
    _STATE_ALIASES[_state_key] = _state
    _STATE_ALIASES[f"estado {_state_key}"] = _state
    _STATE_ALIASES[f"edo {_state_key}"] = _state

_MUNICIPALITY_ENTRIES = [
    ("Libertador", "Distrito Capital", ("libertador", "municipio libertador")),
    ("Chacao", "Miranda", ("chacao", "municipio chacao")),
    ("Baruta", "Miranda", ("baruta", "municipio baruta")),
    ("Sucre", "Miranda", ("sucre", "municipio sucre", "petare")),
    ("Maracaibo", "Zulia", ("maracaibo", "municipio maracaibo")),
    ("San Francisco", "Zulia", ("san francisco", "municipio san francisco")),
    ("San Felipe", "Yaracuy", ("san felipe", "municipio san felipe")),
    ("Independencia", "Yaracuy", ("independencia", "municipio independencia")),
    ("Iribarren", "Lara", ("iribarren", "municipio iribarren", "barquisimeto")),
    ("Moran", "Lara", ("moran", "municipio moran", "el tocuyo")),
    ("Valencia", "Carabobo", ("valencia", "municipio valencia")),
]


def normalize_location(
    location: str | None,
    default_country: str | None = DEFAULT_COUNTRY,
    *,
    geocode: bool = False,
    timeout: float = 5.0,
    user_agent: str = DEFAULT_OSM_USER_AGENT,
    geocoder: Geocoder | None = None,
) -> LocationObject:
    """Normalize Venezuelan locations into the schema location_object shape.

    The function is offline by default. Geocoding is opt-in and failures leave
    lat/lng as None so the caller never discards a record just because
    coordinates are unavailable.
    """
    raw = normalize_text(location)
    expanded = expand_abbreviations(raw)
    match_text = normalize_for_match(expanded)

    estado = _find_state(match_text)
    municipio = _find_municipality(match_text, estado)
    if estado is None and municipio is not None:
        estado = _state_for_municipality(municipio)
    if estado == "Distrito Capital" and municipio is None:
        municipio = "Libertador"

    lat: float | None = None
    lng: float | None = None
    if geocode and raw:
        coords = _safe_geocode(
            _build_geocode_query(expanded, default_country),
            timeout=timeout,
            user_agent=user_agent,
            geocoder=geocoder or geocode_osm,
        )
        if coords is not None:
            lat, lng = coords

    return {
        "raw": raw or None,
        "estado": estado,
        "municipio": municipio,
        "parroquia": None,
        "lat": lat,
        "lng": lng,
    }


def geocode_osm(
    query: str,
    timeout: float = 5.0,
    user_agent: str = DEFAULT_OSM_USER_AGENT,
) -> tuple[float, float] | None:
    """Return lat/lng from OpenStreetMap Nominatim, or None on failure."""
    try:
        response = requests.get(
            OSM_SEARCH_URL,
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        first = data[0]
        return float(first["lat"]), float(first["lon"])
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
        return None


def _find_state(match_text: str) -> str | None:
    for alias, state in sorted(_STATE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if _contains_token_sequence(match_text, alias):
            return state
    return None


def _find_municipality(match_text: str, estado: str | None) -> str | None:
    matches: list[tuple[str, str]] = []
    for municipality, state, aliases in _MUNICIPALITY_ENTRIES:
        if estado is not None and state != estado:
            continue
        if any(_contains_token_sequence(match_text, normalize_for_match(alias)) for alias in aliases):
            matches.append((municipality, state))

    if not matches and estado is None:
        for municipality, state, aliases in _MUNICIPALITY_ENTRIES:
            if any(_contains_token_sequence(match_text, normalize_for_match(alias)) for alias in aliases):
                matches.append((municipality, state))

    unique = {(municipality, state) for municipality, state in matches}
    if len(unique) == 1:
        return next(iter(unique))[0]
    return None


def _state_for_municipality(municipality: str) -> str | None:
    states = {state for name, state, _aliases in _MUNICIPALITY_ENTRIES if name == municipality}
    if len(states) == 1:
        return next(iter(states))
    return None


def _safe_geocode(
    query: str,
    *,
    timeout: float,
    user_agent: str,
    geocoder: Geocoder,
) -> tuple[float, float] | None:
    try:
        return geocoder(query, timeout, user_agent)
    except (requests.RequestException, ValueError, TypeError):
        return None


def _build_geocode_query(location: str, default_country: str | None) -> str:
    if not default_country:
        return location
    country_match = normalize_for_match(default_country)
    if country_match and _contains_token_sequence(normalize_for_match(location), country_match):
        return location
    return f"{location}, {default_country}"


def _contains_token_sequence(text: str, needle: str) -> bool:
    if not text or not needle:
        return False
    return f" {needle} " in f" {text} "
