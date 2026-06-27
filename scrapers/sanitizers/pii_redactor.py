from __future__ import annotations

from scrapers.sanitizers.pii_detector import detect_pii
from scrapers.sanitizers.pii_tokenizer import pii_token


def _merge_findings(findings: list[dict]) -> list[dict]:
    """Merge overlapping detector spans so replacement indexes stay valid."""
    merged: list[dict] = []

    for finding in sorted(findings, key=lambda item: (item["start"], item["end"])):
        if not merged or finding["start"] >= merged[-1]["end"]:
            merged.append(dict(finding))
            continue

        previous = merged[-1]
        previous["end"] = max(previous["end"], finding["end"])
        if finding["end"] - finding["start"] > previous["end"] - previous["start"]:
            previous["kind"] = finding["kind"]
        else:
            previous["kind"] = "pii"

    return merged


def redact_pii(text: str | None) -> str:
    """Sanitiza PII en el texto redactando datos genéricos y tokenizando cédulas y teléfonos usando PBKDF2."""
    if not text:
        return ""

    findings = _merge_findings(detect_pii(text))
    if not findings:
        return text

    redacted = text
    for finding in sorted(findings, key=lambda item: item["start"], reverse=True):
        start = finding["start"]
        end = finding["end"]
        kind = finding["kind"]
        raw_value = finding["value"]

        if kind in ("identity_document", "phone"):
            token_hash = pii_token(raw_value, kind)
            if token_hash:
                replacement = f"[TOKEN_{kind.upper()}:pbkdf2:{token_hash}]"
            else:
                replacement = f"[REDACTED_{kind.upper()}]"
        else:
            replacement = f"[REDACTED_{kind.upper()}]"

        redacted = redacted[:start] + replacement + redacted[end:]

    return redacted
