from __future__ import annotations

import pytest

from scrapers.normalizers.text import (
    _load_abbreviations,
    expand_abbreviations,
    normalize_for_match,
    normalize_proper_name,
    normalize_text,
    normalize_unicode,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Edo. Zulia", "Estado Zulia"),
        ("Mun. Libertador", "Municipio Libertador"),
        ("Av. Bolívar con Urb. El Marqués", "Avenida Bolívar con Urbanización El Marqués"),
        ("Pq. del Este", "Parque del Este"),
        ("C/C Sambil", "Centro Comercial Sambil"),
        ("Dtto. Capital", "Distrito Capital"),
        ("Mun.    Maracaibo", "Municipio Maracaibo"),
        ("reportan en Edo. Miranda y Mun. Sucre", "reportan en Estado Miranda y Municipio Sucre"),
        ("sin abreviaciones aqui", "sin abreviaciones aqui"),
        ("AV. URDANETA", "Avenida URDANETA"),
        ("Sect. 5, Res. La Trinidad", "Sector 5, Residencias La Trinidad"),
    ],
)
def test_expand_abbreviations_ve(raw: str, expected: str) -> None:
    assert expand_abbreviations(raw) == expected


def test_expand_abbreviations_accepts_custom_mapping() -> None:
    custom = {"hosp": "Hospital"}
    assert expand_abbreviations("Hosp. Vargas", custom) == "Hospital Vargas"
    # El mapa por defecto no debe filtrarse cuando se inyecta uno custom.
    assert expand_abbreviations("Edo. Zulia", custom) == "Edo. Zulia"


def test_expand_abbreviations_empty_and_none() -> None:
    assert expand_abbreviations(None) == ""
    assert expand_abbreviations("") == ""


def test_normalize_proper_name_casing_and_connectors() -> None:
    assert normalize_proper_name("JOSÉ  pérez de la cruz") == "José Pérez de la Cruz"
    assert normalize_proper_name("maría RODRÍGUEZ") == "María Rodríguez"
    assert normalize_proper_name("de la rosa") == "De la Rosa"  # conector inicial sí capitaliza
    assert normalize_proper_name(None) == ""
    assert normalize_proper_name("") == ""


def test_normalize_unicode_configurable_form() -> None:
    assert normalize_unicode("  hola   mundo ") == "hola mundo"
    # NFKC descompone el indicador ordinal 'º' (U+00BA) a 'o'; NFC lo preserva.
    assert normalize_unicode("Nº", form="NFKC") == "No"
    assert normalize_unicode("Nº", form="NFC") == "Nº"
    assert normalize_unicode(None) == ""
    assert normalize_unicode("") == ""


def test_load_abbreviations_returns_nonempty_mapping() -> None:
    table = _load_abbreviations()
    assert isinstance(table, dict)
    assert table  # no vacio
    assert table.get("edo.") == "Estado"


def test_loader_degrades_to_fallback_when_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Smoke de degradacion: si el JSON de config no existe, el loader cae al
    # fallback embebido sin lanzar excepcion (no rompe el pipeline).
    import scrapers.normalizers.text as text_mod
    from pathlib import Path

    text_mod._load_abbreviations.cache_clear()
    monkeypatch.setattr(text_mod, "_ABBREV_PATH", Path("ruta/que/no/existe/ve_abbreviations.json"))
    try:
        table = text_mod._load_abbreviations()
        assert table == text_mod._FALLBACK_ABBREVIATIONS
        assert table.get("edo.") == "Estado"
    finally:
        # Restaura el cache para no contaminar otros tests.
        text_mod._load_abbreviations.cache_clear()


def test_backward_compatible_existing_functions() -> None:
    # Guardia de retrocompat: claim_extractor (PR#2), fingerprint y geo.py (PR#3)
    # dependen de estos comportamientos exactos.
    assert normalize_text("  Hola   mundo ") == "Hola mundo"
    assert normalize_text(None) == ""
    # 'Nº' -> 'No' bajo NFKC (U+00BA se descompone a 'o'); '!' y ',' -> espacio.
    assert normalize_for_match("Área Nº 3, Caracas!") == "area no 3 caracas"
    assert normalize_for_match(None) == ""
