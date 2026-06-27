from __future__ import annotations

import hashlib
import hmac
import os


HMAC_PREFIX = "hmac_sha256:"


def _normalize_value(value: str) -> str:
    return " ".join((value or "").lower().strip().split())

def hmac_token(value: str, secret_env: str = "PII_HMAC_SECRET") -> str:
    secret = os.getenv(secret_env)
    if not secret:
        raise RuntimeError(
            f"Falta variable {secret_env}. No uses hash simple para cédulas/teléfonos."
        )
    normalized = _normalize_value(value)
    digest = hmac.new(secret.encode(), normalized.encode(), hashlib.sha256).hexdigest()
    return f"{HMAC_PREFIX}{digest}"


def hmac_digest(value: str, secret_env: str = "PII_HMAC_SECRET") -> str:
    return hmac_token(value, secret_env=secret_env).removeprefix(HMAC_PREFIX)
