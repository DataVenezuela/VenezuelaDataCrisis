"""Utilidades puras compartidas entre módulos.

Funciones genéricas de string/datos que no pertenecen a ningún dominio
concreto (parsers, sanitizers, normalizers). Centralizarlas aquí evita
dependencias cruzadas entre módulos de dominio y el uso de funciones
"privadas" (prefijo `_`) desde fuera de su módulo de origen (issue #71).
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D+")


def digits_only(value: str) -> str:
    """Elimina todo carácter que no sea dígito.

    Ej.: ``"V-12.345.678"`` -> ``"12345678"``.
    """
    return _DIGITS_RE.sub("", value)


def mask_last4(value: str) -> str:
    """Enmascara dejando "****" + los últimos 4 dígitos.

    Ej.: ``"V-12.345.678"`` -> ``"****5678"``.

    Lanza ``ValueError`` si el valor no contiene ningún dígito.
    O 
    Lanza `ValueError`` si el valor tiene menos de 5 caracteres
    """
    digits = digits_only(value)
    if not digits:
        raise ValueError("No hay dígitos para generar máscara PII")
    if len(digits) < 5: 
        raise ValueError("Debe tener al menos 5 caracteres para generar máscara PII")
        
    return f"****{digits[-4:]}"
