from __future__ import annotations

import hashlib
from datetime import datetime

from scrapers.models import AcopioCenter, Event
from scrapers.normalizers.text import normalize_for_match

# Version global del contrato de fingerprint. Alimenta aportes.dedup_version.
FINGERPRINT_VERSION = "v1"


def _truncate_to_hour(date_iso: str | None) -> str:
    """Devuelve 'YYYY-MM-DDTHH' a partir de un ISO-8601, o '' si falta/invalido.

    Trunca a la hora para distinguir replicas separadas sin sobre-dividir
    cuando dos fuentes reportan el mismo evento con minutos distintos.
    Degradacion con gracia: nunca lanza.
    """
    if not date_iso:
        return ""
    try:
        dt = datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.strftime("%Y-%m-%dT%H")


def build_fingerprint(
    event_id: str,
    claim_type: str,
    location_text: str | None,
    description: str,
) -> str:
    # Legacy de claims (usado por deduplicator.py, se borra en #82). No es v1.
    return _hash_normalized_parts(
        normalize_for_match(event_id),
        normalize_for_match(claim_type),
        normalize_for_match(location_text or ""),
        normalize_for_match(description)[:300],
    )


def build_event_fingerprint(event: Event) -> str:
    """Fingerprint v1 de Event: solo campos estables.

    Incluye event_type, location_text y la hora truncada de date_iso.
    Excluye description (volatil cross-source). magnitude/depth/status/
    affected_states no existen en el modelo, quedan excluidos por construccion.
    """
    return _hash_normalized_parts(
        normalize_for_match(event.event_type),
        normalize_for_match(event.location_text or ""),
        _truncate_to_hour(event.date_iso),
    )


def build_acopio_fingerprint(center: AcopioCenter) -> str:
    """Fingerprint v1 de AcopioCenter: incluye event_id.

    Incluye event_id, name, location_text. Excluye status/needs/coordinates;
    capacity/current_load/confidence/contacto no existen en el modelo.
    """
    return _hash_normalized_parts(
        normalize_for_match(center.event_id),
        normalize_for_match(center.name),
        normalize_for_match(center.location_text),
    )


def build_entity_fingerprint(entity: Event | AcopioCenter) -> str:
    """Dispatch fingerprint generation for supported typed entities."""
    if isinstance(entity, Event):
        return build_event_fingerprint(entity)
    if isinstance(entity, AcopioCenter):
        return build_acopio_fingerprint(entity)
    raise TypeError("build_entity_fingerprint only accepts Event or AcopioCenter")


def _hash_parts(*parts: str) -> str:
    return _hash_normalized_parts(*(normalize_for_match(part) for part in parts))


def _hash_normalized_parts(*parts: str) -> str:
    normalized = "|".join(parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
