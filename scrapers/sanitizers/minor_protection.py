"""Reducción de campos identificables para registros de personas menores de edad.

Cuando ``is_minor`` es explícitamente ``true``, se reduce la información
potencialmente identificable antes del export — no se elimina el registro,
solo se acota qué tan localizable/identificable queda.

``is_minor`` en ``None`` o ``False`` no dispara ninguna reducción: solo el
valor explícito ``True`` activa la protección (no se asume minoría de edad
por ausencia de dato).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Campos que se anulan por completo cuando is_minor=True: alta capacidad de
# identificar/localizar a la persona (foto) o de mostrarla parcialmente en
# claro (cedula_masked). Parte del contrato público del módulo (ver docstring
# arriba) — no es un detalle de implementación, por eso no lleva "_".
MINOR_REDACTED_FIELDS = ("foto", "cedula_masked")


def protect_minor_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    """Devuelve una copia de ``record`` con campos sensibles reducidos si es menor.

    No modifica ``cedula_hmac`` (hash, no identificable por sí solo y
    necesario para matching de Stage 1) ni ningún otro campo no listado.
    """
    if record.get("is_minor") is not True:
        return dict(record)

    sanitized = dict(record)
    for field in MINOR_REDACTED_FIELDS:
        if field in sanitized:
            sanitized[field] = None

    location = sanitized.get("last_known_location")
    if isinstance(location, str) and "," in location:
        # Solo el formato exacto "Municipio, Estado" (dos partes, ambas no
        # vacías) se acota de forma segura a "Estado". Cualquier otro
        # formato con coma (texto libre con más de un separador, o con coma
        # final) no garantiza que el último segmento sea el estado — por
        # ejemplo "Maracaibo, Zulia, Venezuela" daría "Venezuela", no el
        # estado. Ante esa ambigüedad se redacta del todo (fail-closed) en
        # vez de exponer una ubicación mal acotada.
        parts = [p.strip() for p in location.split(",")]
        if len(parts) == 2 and all(parts):
            sanitized["last_known_location"] = parts[1]
        else:
            sanitized["last_known_location"] = None

    return sanitized
