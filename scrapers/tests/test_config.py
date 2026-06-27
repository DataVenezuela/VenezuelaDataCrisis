from pathlib import Path

from scrapers.validators.source_validator import validate_sources_config


def test_demo_config_is_valid():
    path = Path(__file__).resolve().parents[1] / "config" / "sources.demo.yaml"
    payload = validate_sources_config(path)

    assert "sources" in payload
    assert payload["sources"][0]["enabled"] is True
