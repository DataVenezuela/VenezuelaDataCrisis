from pathlib import Path

import pytest

from scrapers.sources.loader import load_sources
from scrapers.validators.source_validator import validate_sources_config


def test_demo_config_is_valid():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.demo.yaml"
    payload = validate_sources_config(path)

    assert "sources" in payload
    assert payload["sources"][0]["enabled"] is True
    assert payload["sources"][0]["parser_asignado"] == "demo_text"


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


def test_max_concurrent_posts_is_loaded_into_source_config(tmp_path):
    config = tmp_path / "max_concurrent_posts.yaml"
    config.write_text(
        """
sources:
  - id: api_parallel_posts
    name: API con POSTs paralelos
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/api"
    refresh_minutes: 30
    parser_asignado: encuentralos
    max_concurrent_posts: 32
""",
        encoding="utf-8",
    )

    _project, sources = load_sources(config)

    assert sources[0].max_concurrent_posts == 32


def test_max_concurrent_posts_defaults_to_none(tmp_path):
    config = tmp_path / "no_max_concurrent_posts.yaml"
    config.write_text(
        """
sources:
  - id: api_without_parallel_posts
    name: API sin POSTs paralelos
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

    assert sources[0].max_concurrent_posts is None


def test_encuentralos_parallelism_config_is_loaded():
    config = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "sources.venezuela.starter.yaml"
    )

    _project, sources = load_sources(config)
    encuentralos = next(
        source for source in sources if source.id == "encuentralos_tecnosoft"
    )

    assert encuentralos.max_concurrent_pages == 32
    assert encuentralos.max_concurrent_posts == 8
    assert encuentralos.probe_limit == 1000


def test_invalid_max_concurrent_posts_is_rejected(tmp_path):
    config = tmp_path / "invalid_max_concurrent_posts.yaml"
    config.write_text(
        """
sources:
  - id: api_invalid_parallel_posts
    name: API con POSTs paralelos invalidos
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/api"
    refresh_minutes: 30
    parser_asignado: encuentralos
    max_concurrent_posts: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="max_concurrent_posts"):
        validate_sources_config(config)


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


# ---------------------------------------------------------------------------
# allowed_domains / rate_limit_per_minute (issue #132)
# ---------------------------------------------------------------------------

def _config_with(tmp_path, extra_lines: str):
    config = tmp_path / "src.yaml"
    config.write_text(
        f"""
sources:
  - id: fuente_test
    name: Fuente de prueba
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/api"
    refresh_minutes: 30
    parser_asignado: encuentralos
{extra_lines}
""",
        encoding="utf-8",
    )
    return config


def test_allowed_domains_and_rate_limit_are_optional(tmp_path):
    # Ausentes: config valida igual que hoy (retrocompatible).
    payload = validate_sources_config(_config_with(tmp_path, ""))
    source = payload["sources"][0]
    assert "allowed_domains" not in source
    assert "rate_limit_per_minute" not in source


def test_valid_allowed_domains_and_rate_limit(tmp_path):
    extra = (
        "    allowed_domains:\n"
        "      - example.org\n"
        "    rate_limit_per_minute: 30\n"
    )
    payload = validate_sources_config(_config_with(tmp_path, extra))
    source = payload["sources"][0]
    assert source["allowed_domains"] == ["example.org"]
    assert source["rate_limit_per_minute"] == 30


def test_valid_probe_limit(tmp_path):
    payload = validate_sources_config(_config_with(tmp_path, "    probe_limit: 1000\n"))

    assert payload["sources"][0]["probe_limit"] == 1000


def test_zero_probe_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="probe_limit"):
        validate_sources_config(_config_with(tmp_path, "    probe_limit: 0\n"))


def test_bool_probe_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="probe_limit"):
        validate_sources_config(_config_with(tmp_path, "    probe_limit: true\n"))


def test_empty_allowed_domains_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="allowed_domains"):
        validate_sources_config(_config_with(tmp_path, "    allowed_domains: []\n"))


def test_allowed_domains_with_blank_entry_is_rejected(tmp_path):
    extra = '    allowed_domains: ["example.org", "  "]\n'
    with pytest.raises(ValueError, match="allowed_domains"):
        validate_sources_config(_config_with(tmp_path, extra))


def test_non_list_allowed_domains_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="allowed_domains"):
        validate_sources_config(_config_with(tmp_path, "    allowed_domains: example.org\n"))


def test_zero_rate_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="rate_limit_per_minute"):
        validate_sources_config(_config_with(tmp_path, "    rate_limit_per_minute: 0\n"))


def test_negative_rate_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="rate_limit_per_minute"):
        validate_sources_config(_config_with(tmp_path, "    rate_limit_per_minute: -5\n"))


def test_bool_rate_limit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="rate_limit_per_minute"):
        validate_sources_config(_config_with(tmp_path, "    rate_limit_per_minute: true\n"))
