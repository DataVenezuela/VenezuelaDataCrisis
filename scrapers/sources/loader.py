from __future__ import annotations

from pathlib import Path

from scrapers.models.source import SourceConfig
from scrapers.validators.source_validator import validate_sources_config


def load_sources(config_path: Path) -> tuple[dict, list[SourceConfig]]:
    payload = validate_sources_config(config_path)
    project = payload.get("project", {})
    sources: list[SourceConfig] = []

    for source in payload["sources"]:
        sources.append(
            SourceConfig(
                id=source["id"],
                name=source["name"],
                type=source["type"],
                enabled=bool(source["enabled"]),
                trust_tier=source["trust_tier"],
                url=source["url"],
                refresh_minutes=int(source["refresh_minutes"]),
                parser=source.get("parser", "auto"),
                required_keywords=source.get("required_keywords", []) or [],
                notes=source.get("notes"),
            )
        )

    return project, sources
