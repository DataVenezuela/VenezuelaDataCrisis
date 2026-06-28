from __future__ import annotations

import json
from typing import Any


def _flatten(obj: Any, prefix: str = "") -> list[str]:
    lines: list[str] = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            new_prefix = f"{prefix}.{key}" if prefix else str(key)
            lines.extend(_flatten(value, new_prefix))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj[:100]):
            new_prefix = f"{prefix}[{idx}]"
            lines.extend(_flatten(value, new_prefix))
    else:
        value = str(obj)
        if value and value.lower() != "none":
            lines.append(f"{prefix}: {value}")

    return lines


def extract_json_text(raw: str) -> tuple[str | None, str, dict[str, Any]]:
    payload = json.loads(raw)
    lines = _flatten(payload)
    title = None

    if isinstance(payload, dict):
        title = payload.get("title") or payload.get("name") or payload.get("id")

    return str(title) if title else None, "\n".join(lines), {"json_root_type": type(payload).__name__}
