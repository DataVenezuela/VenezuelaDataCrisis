from __future__ import annotations

import re
import hashlib
import os
from shared.config import PII_HMAC_SECRET


def normalize_id_document(val: str) -> str:
    """Normaliza cédulas venezolanas a un formato estándar: nacionalidad + digitos (ej: v12345678)."""
    val = (val or "").lower().strip()
    val = re.sub(r"\b(?:dni|ci|cedula|cédula|rut|v|e|j|g)\s*[:#-]?\s*", "", val)
    digits = "".join(ch for ch in val if ch.isdigit())
    if not digits:
        return ""
    
    orig_clean = re.sub(r"[^a-z0-9]", "", val)
    if orig_clean and orig_clean[0] in ("v", "e", "j", "g"):
        return orig_clean[0] + digits
    
    return "v" + digits


def normalize_phone(val: str) -> str:
    """Normaliza números de teléfono al formato internacional estándar (ej: 584121234567)."""
    val = (val or "").strip()
    digits = "".join(ch for ch in val if ch.isdigit())
    if not digits:
        return ""

    if digits.startswith("0058"):
        digits = digits[2:]
    elif digits.startswith("04") and len(digits) == 11:
        digits = "58" + digits[1:]
    elif digits.startswith("4") and len(digits) == 10:
        digits = "58" + digits

    return digits


def pii_token(value: str, kind: str, secret_env: str = "PII_HMAC_SECRET") -> str:
    """
    Genera un token criptográfico fuerte utilizando PBKDF2 con 600,000 iteraciones
    para mitigar ataques de fuerza bruta offline en el espacio de cédulas venezolanas.
    """
    secret = os.getenv(secret_env, PII_HMAC_SECRET)
    if not secret:
        raise RuntimeError(
            f"Falta variable {secret_env}. Se requiere clave para PBKDF2."
        )

    if kind == "identity_document":
        normalized = normalize_id_document(value)
    elif kind == "phone":
        normalized = normalize_phone(value)
    else:
        normalized = " ".join((value or "").lower().strip().split())

    if not normalized:
        return ""

    password_bytes = normalized.encode("utf-8")
    salt_bytes = secret.encode("utf-8")
    iterations = 600000

    dk = hashlib.pbkdf2_hmac("sha256", password_bytes, salt_bytes, iterations)
    return dk.hex()
