from __future__ import annotations

import json
import re
from pathlib import Path

from scrapers.models.document import Document
from scrapers.normalizers.text import normalize_text


CLAIM_TYPES = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "claim_types.json").read_text(encoding="utf-8")
)

_SENTENCE_SPLIT_RE = re.compile(
    r"""
    (?<!\bSr\.)
    (?<!\bSra\.)
    (?<!\bDr\.)
    (?<!\bDra\.)
    (?<!\bIng\.)
    (?<!\bLic\.)
    (?<!\bArq\.)
    (?<!\bProf\.)
    (?<!\betc\.)
    (?<!\bNo\.)
    (?<!\bNro\.)
    (?<!\bEE\.UU\.)
    (?<!\bU\.S\.A\.)
    (?<!\bS\.A\.)
    (?<!\bC\.A\.)
    (?<!\bC\.V\.)
    (?<=[.!?])
    \s+
    (?=(?:["'“”«»(\[])*(?:[A-ZÁÉÍÓÚÑ0-9]|https?://|www\.))
    """,
    re.VERBOSE | re.IGNORECASE,
)

_MAX_EVIDENCE_TEXT_LENGTH = 900
_MAX_DESCRIPTION_TEXT_LENGTH = 1200


def _split_sentences(text: str) -> list[str]:
    normalized_text = normalize_text(text)
    if not normalized_text:
        return []

    chunks = re.split(_SENTENCE_SPLIT_RE, normalized_text)
    return [chunk for chunk in (normalize_text(c) for c in chunks) if len(chunk) >= 20]


def _truncate_text(text: str, max_length: int) -> str:
    normalized_text = normalize_text(text)
    if len(normalized_text) <= max_length:
        return normalized_text

    truncated = normalized_text[:max_length].rstrip()
    boundary = truncated.rfind(" ")
    if boundary > max_length // 2:
        return truncated[:boundary].rstrip()

    return truncated


def extract_claim_candidates(document: Document, event_id: str, default_country: str | None = None) -> list[dict]:
    sentences = _split_sentences(document.text)
    candidates: list[dict] = []

    for sentence in sentences:
        lower = sentence.lower()
        for claim_type, keywords in CLAIM_TYPES.items():
            if any(keyword.lower() in lower for keyword in keywords):
                candidates.append(
                    {
                        "event_id": event_id,
                        "source_id": document.source_id,
                        "source_name": document.source_name,
                        "source_url": document.source_url,
                        "claim_type": claim_type,
                        "description": _truncate_text(sentence, _MAX_DESCRIPTION_TEXT_LENGTH),
                        "location_text": default_country,
                        "evidence_text": _truncate_text(sentence, _MAX_EVIDENCE_TEXT_LENGTH),
                        "fetched_at": document.fetched_at,
                    }
                )
                break

    # Fallback: si no hay match, guardar resumen mínimo como situation.report.
    if not candidates and document.text:
        candidates.append(
            {
                "event_id": event_id,
                "source_id": document.source_id,
                "source_name": document.source_name,
                "source_url": document.source_url,
                "claim_type": "situation.report",
                "description": _truncate_text(document.text, _MAX_DESCRIPTION_TEXT_LENGTH),
                "location_text": default_country,
                "evidence_text": _truncate_text(document.text, _MAX_EVIDENCE_TEXT_LENGTH),
                "fetched_at": document.fetched_at,
            }
        )

    return candidates
