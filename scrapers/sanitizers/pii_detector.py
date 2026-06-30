from __future__ import annotations

import re
from typing import Any


PATTERNS = {
    # Emergency-safe patterns, kept contextual to avoid redacting technical IDs/timestamps.
    "identity_document": re.compile(
        r"""
        \b(?:
            [VEJG]\s*[-.]?\s*\d{6,10}
            |
            (?:DNI|CI|C[ÉE]DULA|RUT)\s*[:#-]?\s*\d{6,10}
        )\b
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    "phone": re.compile(
        r"""
        (?:
            (?:\+?58|0058)[\s.-]?(?:0?4(?:12|14|16|24|26)|2\d{2})[\s.-]?\d{3}[\s.-]?\d{4}
            |
            \b0?4(?:12|14|16|24|26)[\s.-]?\d{3}[\s.-]?\d{4}\b
            |
            \b(?:tel[eé]fono|tlf|celular|m[oó]vil|whatsapp|contacto)\s*[:#-]?\s*(?:\+?\d[\d\s().-]{7,}\d)
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "possible_minor": re.compile(r"\b(?:niñ[oa]|menor|adolescente|beb[ée]|infante)\b", re.IGNORECASE),
}


def detect_pii(text: str | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not text:
        return findings

    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(
                {
                    "kind": kind,
                    "value": match.group(0),
                    "start": match.start(),
                    "end": match.end(),
                }
            )

    return sorted(findings, key=lambda item: item["start"])
