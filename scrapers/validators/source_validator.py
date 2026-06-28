from __future__ import annotations

from pathlib import Path

import yaml


SUPPORTED_TYPES = {"html_static", "api_json", "rss", "manual_file", "webapp_js", "pdf"}
SUPPORTED_TRUST_TIERS = {"A", "B", "C", "D"}
REQUIRED_SOURCE_FIELDS = {
    "id",
    "name",
    "type",
    "enabled",
    "trust_tier",
    "url",
    "refresh_minutes",
    "parser_asignado",
}


def _source_label(idx: int, source: dict) -> str:
    source_id = source.get("id")
    if source_id:
        return f"source #{idx} ({source_id})"
    return f"source #{idx}"


def _normalize_legacy_parser(source: dict) -> None:
    if "parser_asignado" not in source and "parser" in source:
        source["parser_asignado"] = source["parser"]


def _validate_non_empty_string(source: dict, field: str, label: str) -> None:
    value = source[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} debe tener '{field}' como texto no vacio.")


def _validate_bool(source: dict, field: str, label: str) -> None:
    if not isinstance(source[field], bool):
        raise ValueError(f"{label} debe tener '{field}' como booleano.")


def _validate_positive_int(source: dict, field: str, label: str) -> None:
    value = source[field]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{label} debe tener '{field}' como entero positivo.")


def _validate_optional_fields(source: dict, label: str) -> None:
    required_keywords = source.get("required_keywords", [])
    if required_keywords is not None:
        if not isinstance(required_keywords, list) or not all(
            isinstance(keyword, str) for keyword in required_keywords
        ):
            raise ValueError(
                f"{label} debe tener 'required_keywords' como lista de textos."
            )

    if (
        "notes" in source
        and source["notes"] is not None
        and not isinstance(source["notes"], str)
    ):
        raise ValueError(f"{label} debe tener 'notes' como texto o null.")

    timeout_seconds = source.get("timeout_seconds")
    if timeout_seconds is not None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ) or timeout_seconds <= 0:
            raise ValueError(
                f"{label} debe tener 'timeout_seconds' como numero positivo."
            )

    max_retries = source.get("max_retries")
    if max_retries is not None:
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 1:
            raise ValueError(
                f"{label} debe tener 'max_retries' como entero positivo (representa el numero "
                f"total de intentos; 0 dejaria el adapter sin ningun intento)."
            )


def validate_sources_config(config_path: Path) -> dict:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise ValueError("El YAML debe ser un objeto.")

    if "sources" not in payload or not isinstance(payload["sources"], list):
        raise ValueError("El YAML debe tener una lista top-level 'sources'.")

    for idx, source in enumerate(payload["sources"]):
        if not isinstance(source, dict):
            raise ValueError(f"source #{idx} debe ser un objeto.")

        _normalize_legacy_parser(source)
        label = _source_label(idx, source)

        missing = REQUIRED_SOURCE_FIELDS - set(source)
        if missing:
            raise ValueError(f"{label} tiene campos faltantes: {sorted(missing)}")

        if source["type"] not in SUPPORTED_TYPES:
            raise ValueError(
                f"{label} usa type no soportado: {source['type']}. "
                f"Valores validos: {sorted(SUPPORTED_TYPES)}"
            )

        if source["trust_tier"] not in SUPPORTED_TRUST_TIERS:
            raise ValueError(
                f"{label} usa trust_tier invalido: {source['trust_tier']}. "
                f"Valores validos: {sorted(SUPPORTED_TRUST_TIERS)}"
            )

        for field in ["id", "name", "type", "trust_tier", "url", "parser_asignado"]:
            _validate_non_empty_string(source, field, label)

        _validate_bool(source, "enabled", label)
        _validate_positive_int(source, "refresh_minutes", label)
        _validate_optional_fields(source, label)

    return payload
