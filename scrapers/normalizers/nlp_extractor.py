from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache
from typing import Protocol, TypeAlias, cast

from scrapers.normalizers.text import normalize_for_match, normalize_text

SPACY_MODEL = "es_core_news_sm"
INSTALL_INSTRUCTION = "python -m spacy download es_core_news_sm"

EntityExtraction: TypeAlias = dict[str, list[str]]


class EntityLike(Protocol):
    text: str
    label_: str


class DocLike(Protocol):
    ents: Iterable[EntityLike]


NlpPipeline: TypeAlias = Callable[[str], DocLike]

VENEZUELAN_REGIONAL_VARIANTS = {
    "name_diminutives": (
        "Juancho",
        "Mariela",
        "Goyo",
        "Cheo",
        "Chuo",
    ),
    "colloquial_toponyms": (
        "el Este",
        "la Costa",
        "los Valles del Tuy",
        "la Gran Caracas",
    ),
}

_LABEL_TO_BUCKET = {
    "PER": "persons",
    "PERSON": "persons",
    "LOC": "locations",
    "GPE": "locations",
    "FAC": "locations",
    "ORG": "organizations",
}

_ORG_KEYWORDS = (
    "alcaldia",
    "bomberos",
    "caritas",
    "cruz roja",
    "fundacion",
    "hospital",
    "iglesia",
    "ministerio",
    "proteccion civil",
    "universidad",
)

_GENERIC_FALSE_ENTITIES = {
    "familiares",
    "pacientes",
    "vecinos",
    "voluntarios",
}


def extract_entities(text: str | None, nlp: NlpPipeline | None = None) -> EntityExtraction:
    """Extract people, locations and organizations from free-form Spanish text.

    This module runs before field mapping and does not replace source-specific
    parsers. A spaCy pipeline can be injected for tests or batch jobs; otherwise
    the Spanish model is loaded lazily when the function is called.
    """
    cleaned = normalize_text(text)
    result = _empty_result()
    if len(cleaned) < 3:
        return result

    pipeline = nlp or _load_spacy_model()
    doc = pipeline(cleaned)

    for ent in getattr(doc, "ents", ()) or ():
        value = normalize_text(getattr(ent, "text", ""))
        if value and not _is_generic_false_entity(value):
            bucket = _bucket_for_entity(value, getattr(ent, "label_", ""))
            if bucket is None:
                continue
            _append_unique(result[bucket], value)

    return result


@lru_cache(maxsize=1)
def _load_spacy_model(model_name: str = SPACY_MODEL) -> NlpPipeline:
    try:
        import spacy
    except ImportError as exc:
        raise RuntimeError(_missing_spacy_dependency_message(exc)) from exc

    try:
        pipeline = cast(NlpPipeline, spacy.load(model_name))
        return pipeline
    except OSError as exc:
        raise RuntimeError(_missing_model_message(model_name)) from exc


def _missing_model_message(model_name: str) -> str:
    return (
        f"spaCy Spanish model '{model_name}' is not installed. "
        f"Install dependencies from scrapers/requirements.txt or run: {INSTALL_INSTRUCTION}"
    )


def _missing_spacy_dependency_message(exc: ImportError) -> str:
    missing_name = getattr(exc, "name", None)
    if missing_name and missing_name != "spacy":
        return (
            f"spaCy could not import dependency '{missing_name}'. "
            "Install dependencies from scrapers/requirements.txt."
        )
    return (
        "spaCy is not installed. "
        f"Install dependencies from scrapers/requirements.txt or run: {INSTALL_INSTRUCTION}"
    )


def _empty_result() -> EntityExtraction:
    return {"persons": [], "locations": [], "organizations": []}


def _append_unique(values: list[str], value: str) -> None:
    key = normalize_for_match(value)
    existing = {normalize_for_match(item) for item in values}
    if key and key not in existing:
        values.append(value)


def _bucket_for_entity(value: str, label: str) -> str | None:
    if _looks_like_organization(value):
        return "organizations"
    return _LABEL_TO_BUCKET.get(label)


def _looks_like_organization(value: str) -> bool:
    match_value = normalize_for_match(value)
    return any(keyword in match_value for keyword in _ORG_KEYWORDS)


def _is_generic_false_entity(value: str) -> bool:
    return normalize_for_match(value) in _GENERIC_FALSE_ENTITIES
