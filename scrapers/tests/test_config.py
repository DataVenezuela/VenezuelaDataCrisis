from pathlib import Path

import pytest

from scrapers.validators.source_validator import validate_sources_config


def test_demo_config_is_valid():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.demo.yaml"
    payload = validate_sources_config(path)

    assert "sources" in payload
    assert payload["sources"][0]["enabled"] is True
    assert payload["sources"][0]["parser_asignado"] == "text"


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
