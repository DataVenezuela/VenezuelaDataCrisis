from scrapers.normalizers.text import (
    expand_abbreviations,
    normalize_for_match,
    normalize_proper_name,
    normalize_text,
    normalize_unicode,
)
from scrapers.normalizers.date import normalize_date
from scrapers.normalizers.location import geocode_osm, normalize_location
from scrapers.normalizers.nlp_extractor import extract_entities
from scrapers.normalizers.person import derive_is_minor, name_key, normalize_person_name
from scrapers.normalizers.phonetic import phonetic_hash, build_deterministic_id

__all__ = [
    "normalize_text",
    "normalize_for_match",
    "expand_abbreviations",
    "normalize_proper_name",
    "normalize_unicode",
    "normalize_date",
    "normalize_location",
    "geocode_osm",
    "extract_entities",
    "phonetic_hash",
    "build_deterministic_id",
    "derive_is_minor",
    "name_key",
    "normalize_person_name",
]
