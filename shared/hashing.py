from __future__ import annotations

import hashlib
import hmac

from scrapers.normalizers.text import normalize_for_match


def sha256_hex(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def identity_token(raw_identifier: str | None, secret: str) -> str | None:
    """HMAC determinista de un documento de identidad (cédula) -> `cedula_hmac`.

    Señal decisiva para deduplicar personas sin almacenar la PII cruda.
    Se normaliza antes para que "V-12.345.678" y "V12345678" produzcan el mismo token.
    Debe computarse en cuarentena, ANTES de redactar.
    """
    if not raw_identifier or not raw_identifier.strip():
        return None
    if not secret:
        raise ValueError("Se requiere PII_HMAC_SECRET para tokenizar identidad")

    normalized = normalize_for_match(raw_identifier).replace(" ", "")
    if not normalized:
        return None
    return hmac.new(secret.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
