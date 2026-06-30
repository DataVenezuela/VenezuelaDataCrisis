from pathlib import Path

import pytest

from scrapers.sources.loader import load_sources
from scrapers.validators.source_validator import validate_sources_config


def test_demo_config_is_valid():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.demo.yaml"
    payload = validate_sources_config(path)

    assert "sources" in payload
    assert payload["sources"][0]["enabled"] is True
    assert payload["sources"][0]["parser_asignado"] == "text"


def test_starter_config_enabled_sources_have_registered_parser():
    """En el starter config, toda fuente enabled debe tener un parser registrado.

    El registry de _get_parser solo conoce 'encuentralos'; cualquier otra
    fuente con un parser_asignado no registrado debe quedar enabled: false para
    no contar como fuente omitida en cada corrida (issue #125, mejora 2).
    """
    path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "sources.venezuela.starter.yaml"
    )
    payload = validate_sources_config(path)

    # Set de parsers concretos registrados en _get_parser (run_pipeline).
    registered = {"encuentralos"}
    enabled = [s for s in payload["sources"] if s.get("enabled")]
    assert enabled, "el starter config deberia tener al menos una fuente enabled"
    for source in enabled:
        assert source["parser_asignado"] in registered, (
            f"fuente enabled {source['id']!r} usa parser no registrado "
            f"{source['parser_asignado']!r}: deberia estar enabled: false"
        )
    # encuentralos sigue habilitada.
    assert any(s["id"] == "encuentralos_tecnosoft" for s in enabled)


def test_custom_template_config_is_valid():
    path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "sources.custom.template.yaml"
    )
    payload = validate_sources_config(path)

    assert len(payload["sources"]) == 5
    assert {source["type"] for source in payload["sources"]} >= {"webapp_js", "pdf"}


def test_missing_required_field_is_rejected(tmp_path):
    config = tmp_path / "missing.yaml"
    config.write_text(
        """
sources:
  - id: fuente_incompleta
    name: Fuente incompleta
    type: html_static
    enabled: true
    trust_tier: C
    url: "https://example.org"
    refresh_minutes: 30
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="parser_asignado"):
        validate_sources_config(config)


def test_invalid_type_is_rejected(tmp_path):
    config = tmp_path / "invalid_type.yaml"
    config.write_text(
        """
sources:
  - id: fuente_tipo_invalido
    name: Fuente con tipo invalido
    type: spreadsheet
    enabled: true
    trust_tier: C
    url: "https://example.org"
    refresh_minutes: 30
    parser_asignado: html
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="type no soportado"):
        validate_sources_config(config)


def test_zero_max_retries_is_rejected(tmp_path):
    config = tmp_path / "zero_retries.yaml"
    config.write_text(
        """
sources:
  - id: webapp_sin_intentos
    name: WebApp con max_retries en cero
    type: webapp_js
    enabled: true
    trust_tier: C
    url: "https://example.org/app"
    refresh_minutes: 30
    parser_asignado: html
    max_retries: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_retries"):
        validate_sources_config(config)


def test_zero_page_size_is_rejected(tmp_path):
    config = tmp_path / "zero_page_size.yaml"
    config.write_text(
        """
sources:
  - id: api_page_size_cero
    name: API con page_size en cero
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/api"
    refresh_minutes: 30
    parser_asignado: encuentralos
    page_size: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="page_size"):
        validate_sources_config(config)


def test_page_size_is_loaded_into_source_config(tmp_path):
    config = tmp_path / "page_size.yaml"
    config.write_text(
        """
sources:
  - id: api_page_size_custom
    name: API con page_size custom
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/api"
    refresh_minutes: 30
    parser_asignado: encuentralos
    page_size: 500
""",
        encoding="utf-8",
    )

    _project, sources = load_sources(config)
    assert sources[0].page_size == 500


def test_page_size_defaults_to_none(tmp_path):
    config = tmp_path / "no_page_size.yaml"
    config.write_text(
        """
sources:
  - id: api_sin_page_size
    name: API sin page_size declarado
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/api"
    refresh_minutes: 30
    parser_asignado: encuentralos
""",
        encoding="utf-8",
    )

    _project, sources = load_sources(config)
    assert sources[0].page_size is None


def test_unsafe_source_id_is_rejected(tmp_path):
    """id se usa como segmento de URL en /api/source-watermarks/{id}."""
    config = tmp_path / "unsafe_id.yaml"
    config.write_text(
        """
sources:
  - id: "fuente/con/slash"
    name: Fuente con slash en el id
    type: html_static
    enabled: true
    trust_tier: C
    url: "https://example.org"
    refresh_minutes: 30
    parser_asignado: html
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="letras, numeros"):
        validate_sources_config(config)


def test_duplicate_source_id_is_rejected(tmp_path):
    config = tmp_path / "duplicate_id.yaml"
    config.write_text(
        """
sources:
  - id: fuente_dup
    name: Primera
    type: html_static
    enabled: true
    trust_tier: C
    url: "https://example.org/a"
    refresh_minutes: 30
    parser_asignado: html
  - id: fuente_dup
    name: Segunda
    type: html_static
    enabled: true
    trust_tier: C
    url: "https://example.org/b"
    refresh_minutes: 30
    parser_asignado: html
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicado"):
        validate_sources_config(config)


def test_invalid_trust_tier_is_rejected(tmp_path):
    config = tmp_path / "invalid_trust.yaml"
    config.write_text(
        """
sources:
  - id: fuente_trust_invalido
    name: Fuente con trust invalido
    type: html_static
    enabled: true
    trust_tier: E
    url: "https://example.org"
    refresh_minutes: 30
    parser_asignado: html
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trust_tier invalido"):
        validate_sources_config(config)


def test_legacy_parser_field_is_normalized(tmp_path):
    config = tmp_path / "legacy_parser.yaml"
    config.write_text(
        """
sources:
  - id: fuente_legacy
    name: Fuente legacy
    type: html_static
    enabled: true
    trust_tier: C
    url: "https://example.org"
    refresh_minutes: 30
    parser: html
""",
        encoding="utf-8",
    )

    payload = validate_sources_config(config)

    assert payload["sources"][0]["parser_asignado"] == "html"
