from __future__ import annotations

import builtins
import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

import scrapers.normalizers.nlp_extractor as nlp_mod
from scrapers.normalizers import extract_entities
from scrapers.normalizers.nlp_extractor import VENEZUELAN_REGIONAL_VARIANTS


@dataclass(frozen=True)
class FakeEntity:
    text: str
    label_: str


@dataclass(frozen=True)
class FakeDoc:
    ents: tuple[FakeEntity, ...]


def _fake_nlp(entities: list[FakeEntity]):
    calls: list[str] = []

    def fake_pipeline(text: str) -> FakeDoc:
        calls.append(text)
        return FakeDoc(tuple(entities))

    return fake_pipeline, calls


@pytest.mark.parametrize(
    "text, entities, expected",
    [
        (
            "  Se reporta Persona Demo en Caracas con apoyo de Cruz Roja.  ",
            [
                FakeEntity("Persona Demo", "PER"),
                FakeEntity("Caracas", "LOC"),
                FakeEntity("Cruz Roja", "ORG"),
            ],
            {
                "persons": ["Persona Demo"],
                "locations": ["Caracas"],
                "organizations": ["Cruz Roja"],
            },
        ),
        (
            "Vecinos vieron a Mariela Demo por el Este y avisaron a Proteccion Civil.",
            [
                FakeEntity("Mariela Demo", "PERSON"),
                FakeEntity("el Este", "GPE"),
                FakeEntity("Proteccion Civil", "ORG"),
            ],
            {
                "persons": ["Mariela Demo"],
                "locations": ["el Este"],
                "organizations": ["Proteccion Civil"],
            },
        ),
        (
            "Juancho Demo coordina donaciones en la Costa con Caritas.",
            [
                FakeEntity("Juancho Demo", "PER"),
                FakeEntity("la Costa", "LOC"),
                FakeEntity("Caritas", "ORG"),
            ],
            {
                "persons": ["Juancho Demo"],
                "locations": ["la Costa"],
                "organizations": ["Caritas"],
            },
        ),
    ],
)
def test_extract_entities_from_venezuelan_colloquial_texts(
    text: str,
    entities: list[FakeEntity],
    expected: dict[str, list[str]],
) -> None:
    fake_pipeline, calls = _fake_nlp(entities)

    assert extract_entities(text, nlp=fake_pipeline) == expected
    assert calls == [" ".join(text.split())]


def test_extract_entities_ignores_unknown_labels_and_deduplicates() -> None:
    fake_pipeline, _calls = _fake_nlp(
        [
            FakeEntity("Caracas", "LOC"),
            FakeEntity(" caracas ", "GPE"),
            FakeEntity("Dato sin mapear", "MISC"),
            FakeEntity("Cruz Roja", "ORG"),
            FakeEntity("cruz roja", "ORG"),
        ]
    )

    assert extract_entities("Cruz Roja reporta actividad en Caracas.", nlp=fake_pipeline) == {
        "persons": [],
        "locations": ["Caracas"],
        "organizations": ["Cruz Roja"],
    }


def test_extract_entities_reclassifies_common_venezuelan_organizations() -> None:
    fake_pipeline, _calls = _fake_nlp(
        [
            FakeEntity("Hospital Universitario de Caracas", "LOC"),
            FakeEntity("Proteccion Civil", "LOC"),
            FakeEntity("Alcaldia de Maracaibo", "GPE"),
            FakeEntity("Caritas", "LOC"),
        ]
    )

    assert extract_entities("Texto con instituciones venezolanas.", nlp=fake_pipeline) == {
        "persons": [],
        "locations": [],
        "organizations": [
            "Hospital Universitario de Caracas",
            "Proteccion Civil",
            "Alcaldia de Maracaibo",
            "Caritas",
        ],
    }


def test_extract_entities_filters_generic_false_entities() -> None:
    fake_pipeline, _calls = _fake_nlp(
        [
            FakeEntity("Vecinos", "LOC"),
            FakeEntity("Familiares", "PER"),
            FakeEntity("Voluntarios", "ORG"),
            FakeEntity("Caracas", "LOC"),
        ]
    )

    assert extract_entities("Vecinos reportan desde Caracas.", nlp=fake_pipeline) == {
        "persons": [],
        "locations": ["Caracas"],
        "organizations": [],
    }


def test_extract_entities_handles_empty_or_short_input_without_loading_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_loader() -> object:
        raise AssertionError("spaCy model should not be loaded for empty or short input")

    monkeypatch.setattr(nlp_mod, "_load_spacy_model", unexpected_loader)

    for raw in (None, "", "  ", "ok"):
        assert extract_entities(raw) == {"persons": [], "locations": [], "organizations": []}


def test_extract_entities_handles_doc_without_entities() -> None:
    def fake_pipeline(_text: str) -> SimpleNamespace:
        return SimpleNamespace(ents=None)

    assert extract_entities("Texto narrativo sin entidades detectadas.", nlp=fake_pipeline) == {
        "persons": [],
        "locations": [],
        "organizations": [],
    }


def test_extract_entities_raises_clear_error_when_model_loader_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_loader() -> object:
        raise RuntimeError("Install with: python -m spacy download es_core_news_sm")

    monkeypatch.setattr(nlp_mod, "_load_spacy_model", missing_loader)

    with pytest.raises(RuntimeError, match="python -m spacy download es_core_news_sm"):
        extract_entities("Texto narrativo suficientemente largo")


def test_load_spacy_model_reports_install_instruction_when_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    nlp_mod._load_spacy_model.cache_clear()

    def fake_load(model_name: str) -> object:
        assert model_name == "es_core_news_sm"
        raise OSError("model not found")

    monkeypatch.setitem(sys.modules, "spacy", SimpleNamespace(load=fake_load))

    try:
        with pytest.raises(RuntimeError, match="python -m spacy download es_core_news_sm"):
            nlp_mod._load_spacy_model()
    finally:
        nlp_mod._load_spacy_model.cache_clear()


def test_load_spacy_model_reports_install_instruction_when_spacy_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nlp_mod._load_spacy_model.cache_clear()
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> object:
        if name == "spacy":
            raise ImportError("spacy missing", name="spacy")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "spacy", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        with pytest.raises(RuntimeError, match="python -m spacy download es_core_news_sm"):
            nlp_mod._load_spacy_model()
    finally:
        nlp_mod._load_spacy_model.cache_clear()


def test_load_spacy_model_reports_missing_spacy_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    nlp_mod._load_spacy_model.cache_clear()
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> object:
        if name == "spacy":
            raise ImportError("click missing", name="click")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "spacy", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    try:
        with pytest.raises(RuntimeError, match="dependency 'click'"):
            nlp_mod._load_spacy_model()
    finally:
        nlp_mod._load_spacy_model.cache_clear()


def test_load_spacy_model_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    nlp_mod._load_spacy_model.cache_clear()
    load_calls: list[str] = []
    pipeline = object()

    def fake_load(model_name: str) -> object:
        load_calls.append(model_name)
        return pipeline

    monkeypatch.setitem(sys.modules, "spacy", SimpleNamespace(load=fake_load))

    try:
        assert nlp_mod._load_spacy_model() is pipeline
        assert nlp_mod._load_spacy_model() is pipeline
        assert load_calls == ["es_core_news_sm"]
    finally:
        nlp_mod._load_spacy_model.cache_clear()


def test_venezuelan_regional_variants_are_documented() -> None:
    assert "Juancho" in VENEZUELAN_REGIONAL_VARIANTS["name_diminutives"]
    assert "Mariela" in VENEZUELAN_REGIONAL_VARIANTS["name_diminutives"]
    assert "el Este" in VENEZUELAN_REGIONAL_VARIANTS["colloquial_toponyms"]
    assert "la Costa" in VENEZUELAN_REGIONAL_VARIANTS["colloquial_toponyms"]
