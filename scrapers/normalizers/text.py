from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_match(text: str | None) -> str:
    text = normalize_text(text).lower()
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )
    text = re.sub(r"[^a-z0-9áéíóúñü\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()
