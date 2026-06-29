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
from collections.abc import Mapping
from typing import Any

from shared.hashing import hmac_hex
from shared.helpers import mask_last4


HMAC_PREFIX = "hmac_sha256:"
DEFAULT_EXPORT_SALT_ENV = "PII_SALT"

_CEDULA_INPUT_KEYS = ("cedula", "cédula", "identity_document", "documento_identidad")
_TELEFONO_INPUT_KEYS = ("telefono", "teléfono", "phone", "mobile", "celular")


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


def _required_salt(secret_env: str = DEFAULT_EXPORT_SALT_ENV) -> str:
    salt = os.getenv(secret_env)
    if not salt:
        raise RuntimeError(
            f"Falta variable {secret_env}. Configura un salt para tokenizar PII con HMAC SHA-256."
        )
    return salt


def mask_cedula(cedula: str, salt: str) -> tuple[str, str]:
    """Devuelve (`cedula_hmac`, `cedula_masked`) sin conservar cédula cruda.

    El HMAC se calcula sobre la normalización canónica de `shared.hashing`
    (la misma que usa `identity_token`), para que el redactor y el matcher
    de Personas coincidan en el token de la misma cédula (issue #51).
    """
    digest = hmac_hex(cedula, salt)
    if digest is None:
        raise ValueError("No hay dígitos de cédula para tokenizar")
    return digest, mask_last4(cedula)


def mask_telefono(telefono: str, salt: str) -> tuple[str, str]:
    """Devuelve (`telefono_hmac`, `telefono_masked`) sin conservar teléfono crudo.

    Ver `mask_cedula`: misma normalización canónica compartida con el matcher.
    """
    digest = hmac_hex(telefono, salt)
    if digest is None:
        raise ValueError("No hay dígitos de teléfono para tokenizar")
    return digest, mask_last4(telefono)


def tokenize_pii_fields(
    record: Mapping[str, Any],
    *,
    secret_env: str = DEFAULT_EXPORT_SALT_ENV,
) -> dict[str, Any]:
    """Reemplaza cédulas/teléfonos conocidos por campos HMAC exportables.

    El resultado nunca conserva los campos originales de cédula o teléfono en
    claro. Campos no PII se copian intactos.
    """
    salt = _required_salt(secret_env)
    sanitized = {
        key: value
        for key, value in record.items()
        if key not in {*_CEDULA_INPUT_KEYS, *_TELEFONO_INPUT_KEYS}
    }

    cedula = next((record[key] for key in _CEDULA_INPUT_KEYS if key in record and record[key]), None)
    if cedula is not None:
        cedula_hmac, cedula_masked = mask_cedula(str(cedula), salt)
        sanitized["cedula_hmac"] = cedula_hmac
        sanitized["cedula_masked"] = cedula_masked

    telefono = next((record[key] for key in _TELEFONO_INPUT_KEYS if key in record and record[key]), None)
    if telefono is not None:
        telefono_hmac, telefono_masked = mask_telefono(str(telefono), salt)
        sanitized["telefono_hmac"] = telefono_hmac
        sanitized["telefono_masked"] = telefono_masked

    return sanitized
