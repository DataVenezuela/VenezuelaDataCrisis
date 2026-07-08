from pathlib import Path

import httpx
import pytest

from scrapers.exporters.staging_exporter import StagingConfig
from scrapers.sources.loader import load_sources
from scrapers.validators.source_validator import validate_sources_config


def test_demo_config_is_valid():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.demo.yaml"
    payload = validate_sources_config(path)

    assert "sources" in payload
    assert payload["sources"][0]["enabled"] is True
    assert payload["sources"][0]["parser_asignado"] == "demo_text"


def test_sample_config_enabled_sources_have_registered_parser():
    """Toda fuente enabled debe tener un parser registrado.

    El registry de _get_parser solo conoce 'encuentralos'; cualquier otra
    fuente con un parser_asignado no registrado debe quedar enabled: false para
    no contar como fuente omitida en cada corrida (issue #125, mejora 2).
    El fixture usa identidades sinteticas (*.invalid): el repo nunca versiona
    la lista real de fuentes (ADR 0009).
    """
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "sources.sample.yaml"
    )
    payload = validate_sources_config(path)

    # Set de parsers concretos registrados en _get_parser (run_pipeline).
    registered = {"encuentralos"}
    enabled = [s for s in payload["sources"] if s.get("enabled")]
    assert enabled, "el fixture deberia tener al menos una fuente enabled"
    for source in enabled:
        assert source["parser_asignado"] in registered, (
            f"fuente enabled {source['id']!r} usa parser no registrado "
            f"{source['parser_asignado']!r}: deberia estar enabled: false"
        )
    assert any(s["id"] == "sample_enabled_api" for s in enabled)


def test_custom_config_is_valid_and_thin():
    path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "sources.custom.yaml"
    )
    payload = validate_sources_config(path)

    # El config de produccion (versionado) es formato thin: cada fuente es solo el
    # binding source_id (UUID) -> parser + enabled; url/name/type viven en la DB.
    # Ninguna entrada debe exponer identidad de fuente en el repo (ADR 0009).
    assert payload["sources"], "sources.custom.yaml deberia listar al menos una entrada thin"
    for source in payload["sources"]:
        assert "url" not in source, "una entrada thin no debe exponer la url en el repo"
        assert "name" not in source, "una entrada thin no debe exponer el name en el repo"
        assert source["parser_asignado"]


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


def test_parallelism_config_is_loaded():
    config = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "sources.sample.yaml"
    )

    _project, sources = load_sources(config)
    source = next(
        source for source in sources if source.id == "sample_enabled_api"
    )

    assert source.max_concurrent_pages == 32
    assert source.max_concurrent_posts == 8
    assert source.probe_limit == 1000


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


# ---------------------------------------------------------------------------
# full_scan (issue #216)
# ---------------------------------------------------------------------------

def test_full_scan_true_is_valid(tmp_path):
    payload = validate_sources_config(_config_with(tmp_path, "    full_scan: true\n"))
    assert payload["sources"][0]["full_scan"] is True


def test_full_scan_false_is_valid(tmp_path):
    payload = validate_sources_config(_config_with(tmp_path, "    full_scan: false\n"))
    assert payload["sources"][0]["full_scan"] is False


def test_full_scan_absent_is_valid(tmp_path):
    payload = validate_sources_config(_config_with(tmp_path, ""))
    assert "full_scan" not in payload["sources"][0]


def test_full_scan_non_bool_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="full_scan"):
        validate_sources_config(_config_with(tmp_path, "    full_scan: yes_please\n"))


# ---------------------------------------------------------------------------
# Fuentes thin (uuid -> parser) resueltas contra la DB (ADR 0009)
# ---------------------------------------------------------------------------

_UUID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _thin_config(tmp_path, uuid: str = _UUID_A, parser: str = "encuentralos", enabled: bool = True):
    config = tmp_path / "thin.yaml"
    config.write_text(
        f"""
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
sources:
  - id: {uuid}
    parser_asignado: {parser}
    enabled: {str(enabled).lower()}
""",
        encoding="utf-8",
    )
    return config


def _patch_db(monkeypatch, handler):
    """Hace que el loader resuelva fuentes thin contra un MockTransport en memoria."""
    cfg = StagingConfig(
        supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt="jwt"
    )
    monkeypatch.setattr(StagingConfig, "from_env", classmethod(lambda cls: cfg))
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("scrapers.sources.loader.httpx.Client", fake_client)


def test_thin_entry_is_valid_and_requires_uuid(tmp_path):
    # Una entrada thin valida (id UUID + parser + enabled, sin url) pasa.
    validate_sources_config(_thin_config(tmp_path))

    # Un id que no es UUID en una entrada thin se rechaza.
    bad = tmp_path / "bad_thin.yaml"
    bad.write_text(
        """
sources:
  - id: no_es_un_uuid
    parser_asignado: html
    enabled: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="UUID"):
        validate_sources_config(bad)


def test_thin_config_merges_db_definition(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/sources"
        assert request.method == "GET"
        assert request.url.params.get("source_id") == f"in.({_UUID_A})"
        return httpx.Response(
            200,
            json=[
                {
                    "source_id": _UUID_A,
                    "display_name": "Fuente Reservada",
                    "source_type": "api_json",
                    "url": "https://reservada.example/api",
                    "required_keywords": ["terremoto"],
                    "governed_tier": "B",
                    "refresh_minutes": 30,
                    "active": True,
                    "page_size": 250,
                }
            ],
        )

    _patch_db(monkeypatch, handler)
    _project, sources = load_sources(_thin_config(tmp_path))

    assert len(sources) == 1
    source = sources[0]
    assert source.id == _UUID_A
    assert source.parser_asignado == "encuentralos"  # del repo (el "shape")
    assert source.url == "https://reservada.example/api"  # de la DB
    assert source.type == "api_json"
    assert source.trust_tier == "B"
    assert source.required_keywords == ["terremoto"]
    assert source.page_size == 250
    assert source.enabled is True


def test_thin_enabled_is_anded_with_db_active(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "source_id": _UUID_A,
                    "display_name": "X",
                    "source_type": "api_json",
                    "url": "https://x",
                    "governed_tier": "C",
                    "refresh_minutes": 30,
                    "active": False,
                }
            ],
        )

    _patch_db(monkeypatch, handler)
    _project, sources = load_sources(_thin_config(tmp_path, enabled=True))
    # enabled efectivo = repo enabled (True) AND db active (False)
    assert sources[0].enabled is False


def test_thin_config_fails_closed_when_row_missing(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])  # la fuente no existe en sources

    _patch_db(monkeypatch, handler)
    with pytest.raises(ValueError, match="no existe en la tabla sources"):
        load_sources(_thin_config(tmp_path))


def test_thin_config_fails_closed_on_incomplete_db_row(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"source_id": _UUID_A, "display_name": "X", "source_type": "api_json"}],
        )  # falta url/governed_tier/refresh_minutes

    _patch_db(monkeypatch, handler)
    with pytest.raises(ValueError, match="requerido para operar"):
        load_sources(_thin_config(tmp_path))


def test_thin_config_requires_supabase_env(tmp_path, monkeypatch):
    monkeypatch.setattr(StagingConfig, "from_env", classmethod(lambda cls: None))
    with pytest.raises(ValueError, match="SUPABASE"):
        load_sources(_thin_config(tmp_path))
