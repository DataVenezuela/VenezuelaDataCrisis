"""Tokenización de PII para redacción interna vs. exportación.

CONTRATO DE CAMPOS (no malinterpretar):

- `cedula_hmac` (campo EXPORTADO a JSONL / DB VARCHAR(64)) = HEX PURO de 64
  caracteres, SIN prefijo. Su ÚNICA fuente declarada es
  `shared.hashing.identity_token` (equivalente a `shared.hashing.hmac_hex` y a
  `hmac_digest` de este módulo). NUNCA se exporta el valor con prefijo.

- `hmac_token` devuelve la forma INTERNA con el prefijo "hmac_sha256:". Esa
  forma es SOLO para logs / placeholders de redacción (ver pii_redactor.py) y
  NUNCA debe exportarse ni cablearse hacia `cedula_hmac`. Para exportar, usa
  `hmac_digest` (o directamente `identity_token`/`hmac_hex` de shared.hashing).
"""

from __future__ import annotations

import os

from shared.hashing import hmac_hex


HMAC_PREFIX = "hmac_sha256:"


def hmac_token(value: str, secret_env: str = "PII_HMAC_SECRET") -> str:
    """Token HMAC con prefijo "hmac_sha256:" para uso INTERNO.

    Forma con prefijo destinada EXCLUSIVAMENTE a logs y placeholders de
    redacción (`pii_redactor.py`). NUNCA exportar este valor ni asignarlo a
    `cedula_hmac`: el contrato de 64-hex sin prefijo pertenece a
    `hmac_digest` / `shared.hashing.identity_token`. Para exportar, usa esas
    funciones (que devuelven hex puro de 64 chars).
    """
    secret = os.getenv(secret_env)
    if not secret:
        raise RuntimeError(
            f"Falta variable {secret_env}. No uses hash simple para cédulas/teléfonos."
        )
    digest = hmac_hex(value, secret)
    return f"{HMAC_PREFIX}{digest or ''}"


def hmac_digest(value: str, secret_env: str = "PII_HMAC_SECRET") -> str:
    """Digest HMAC en HEX PURO (64 chars, SIN prefijo) — forma EXPORTABLE.

    Esta es la forma segura para poblar `cedula_hmac` (export JSONL / DB
    VARCHAR(64)). Equivale a `shared.hashing.identity_token(value, secret)`.
    """
    return hmac_token(value, secret_env=secret_env).removeprefix(HMAC_PREFIX)
