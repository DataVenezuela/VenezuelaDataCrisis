from __future__ import annotations

from typing import Any, Protocol

from scrapers.models.source import SourceConfig


RawContent = str | bytes | dict[str, Any]


class AdapterProtocol(Protocol):
    def fetch(self, source_config: SourceConfig) -> RawContent:
        ...
