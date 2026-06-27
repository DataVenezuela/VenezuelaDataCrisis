from __future__ import annotations

import logging

from scrapers.sanitizers.pii_detector import detect_pii
from scrapers.sanitizers.pii_tokenizer import HMAC_PREFIX, hmac_token


logger = logging.getLogger(__name__)
_missing_secret_warned = False


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


def _truncate_hmac_token(token: str) -> str:
    return token.removeprefix(HMAC_PREFIX)


def _redacted_placeholder(finding: dict) -> str:
    kind = str(finding.get("kind", "pii")).upper()
    value = str(finding.get("value", ""))

    if finding.get("kind") in {"identity_document", "phone"}:
        try:
            return f"[REDACTED_{kind}_{_truncate_hmac_token(hmac_token(value))}]"
        except RuntimeError as exc:
            global _missing_secret_warned
            if not _missing_secret_warned:
                logger.warning("PII_HMAC_SECRET no está configurado; usando redacción estática para PII sensible.")
                _missing_secret_warned = True
            logger.debug("Fallback estático para %s: %s", finding.get("kind"), exc)

    return f"[REDACTED_{kind}]"


def redact_pii(text: str | None) -> str:
    if not text:
        return ""

    findings = _merge_findings(detect_pii(text))
    if not findings:
        return text

    redacted = text
    for finding in sorted(findings, key=lambda item: item["start"], reverse=True):
        start = finding["start"]
        end = finding["end"]
        redacted = redacted[:start] + _redacted_placeholder(finding) + redacted[end:]

    return redacted
