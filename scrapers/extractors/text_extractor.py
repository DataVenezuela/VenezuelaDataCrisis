from __future__ import annotations


def extract_plain_text(raw: str) -> tuple[str | None, str]:
    return None, " ".join((raw or "").split())
