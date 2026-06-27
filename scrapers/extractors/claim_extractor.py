from __future__ import annotations

import json
import re
from pathlib import Path

from scrapers.models.document import Document
from scrapers.normalizers.text import normalize_text


CLAIM_TYPES = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "claim_types.json").read_text(encoding="utf-8")
)


def _split_sentences(text: str) -> list[str]:
    # Split conservador para español/inglés sin depender de NLP externo.
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [normalize_text(c) for c in chunks if len(normalize_text(c)) >= 20]


def extract_claim_candidates(document: Document, event_id: str, default_country: str | None = None) -> list[dict]:
    sentences = _split_sentences(document.text)
    candidates: list[dict] = []

    for sentence in sentences[:500]:
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
                        "description": sentence,
                        "location_text": default_country,
                        "evidence_text": sentence[:500],
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
                "description": document.text[:500],
                "location_text": default_country,
                "evidence_text": document.text[:500],
                "fetched_at": document.fetched_at,
            }
        )

    return candidates
