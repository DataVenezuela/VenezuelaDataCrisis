from __future__ import annotations

import hashlib
import hmac
import os


def hmac_token(value: str, secret_env: str = "PII_HMAC_SECRET") -> str:
    secret = os.getenv(secret_env)
    if not secret:
        raise RuntimeError(
            f"Falta variable {secret_env}. No uses hash simple para cédulas/teléfonos."
        )
    normalized = " ".join((value or "").lower().strip().split())
    digest = hmac.new(secret.encode(), normalized.encode(), hashlib.sha256).hexdigest()
    return f"hmac_sha256:{digest}"
