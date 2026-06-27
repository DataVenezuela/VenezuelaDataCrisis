from __future__ import annotations

from pathlib import Path

import yaml


SUPPORTED_TYPES = {"html_static", "api_json", "rss", "manual_file"}
REQUIRED_SOURCE_FIELDS = {
    "id",
    "name",
    "type",
    "enabled",
    "trust_tier",
    "url",
    "refresh_minutes",
}


def validate_sources_config(config_path: Path) -> dict:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("El YAML debe ser un objeto.")

    if "sources" not in payload or not isinstance(payload["sources"], list):
        raise ValueError("El YAML debe tener una lista top-level 'sources'.")

    for idx, source in enumerate(payload["sources"]):
        if not isinstance(source, dict):
            raise ValueError(f"source #{idx} debe ser un objeto.")

        missing = REQUIRED_SOURCE_FIELDS - set(source)
        if missing:
            raise ValueError(f"source #{idx} tiene campos faltantes: {sorted(missing)}")

        if source["type"] not in SUPPORTED_TYPES:
            raise ValueError(f"source #{idx} usa type no soportado: {source['type']}")

        if source["trust_tier"] not in {"A", "B", "C", "D", "E"}:
            raise ValueError(f"source #{idx} usa trust_tier inválido: {source['trust_tier']}")

    return payload
