"""Contratos de dedup por tipo de entidad: fuente unica de verdad.

Stage 1 (staging_exporter) y Stage 2 (consolidation, #82) importan de aqui.
Nadie duplica esta logica. Las funciones de fingerprint viven en fingerprint.py
(identidad de contenido); las block keys viven aqui (logica de dedup/bloqueo).

Las funciones operan sobre dict[str, object] (el record post-PII que produce
_apply_pii en run_pipeline), no sobre entidades tipadas: el exporter nunca
reconstruye modelos. El fingerprint exacto de Event/Acopio se delega a
fingerprint.py via wrappers que reconstruyen el modelo minimo.
"""

from __future__ import annotations

from dataclasses import dataclass

from scrapers.dedup.fingerprint import (
    FINGERPRINT_VERSION,
    _hash_normalized_parts,
    _truncate_to_hour,
)
from scrapers.normalizers.phonetic import phonetic_hash
from scrapers.normalizers.text import normalize_for_match

# Version del contrato de identidad de Person (deterministic_id).
PERSON_ID_VERSION = "person-detid-v1"


@dataclass(frozen=True)
class DedupSpec:
    """Contrato declarativo de dedup para un tipo de entidad."""

    entity_type: str
    version: str
    allow_automerge: bool


# Eventos/acopios identicos cross-source se funden solos; Person nunca (PII,
# revision humana obligatoria).
EVENT_SPEC = DedupSpec("Event", FINGERPRINT_VERSION, allow_automerge=True)
ACOPIO_SPEC = DedupSpec("AcopioCenter", FINGERPRINT_VERSION, allow_automerge=True)
PERSON_SPEC = DedupSpec("Person", PERSON_ID_VERSION, allow_automerge=False)

SPECS: dict[str, DedupSpec] = {
    "Event": EVENT_SPEC,
    "AcopioCenter": ACOPIO_SPEC,
    "Person": PERSON_SPEC,
}


def spec_for_entity_type(entity_type: str) -> DedupSpec:
    """Despacha el DedupSpec correcto por nombre de tipo de entidad."""
    spec = SPECS.get(entity_type)
    if spec is None:
        raise KeyError(f"no DedupSpec for entity_type={entity_type!r}")
    return spec


# --- Fingerprints v1 sobre dict ---------------------------------------------

def event_dedup_key(rec: dict[str, object]) -> str:
    """Fingerprint v1 de Event sobre dict: event_type + location_text + hora.

    Excluye description (volatil cross-source). Replica el contrato de
    build_event_fingerprint para no reconstruir el modelo Event.
    """
    date_iso = rec.get("date_iso")
    date_str = date_iso if isinstance(date_iso, str) else None
    return _hash_normalized_parts(
        normalize_for_match(str(rec.get("event_type") or "")),
        normalize_for_match(str(rec.get("location_text") or "")),
        _truncate_to_hour(date_str),
    )


def acopio_dedup_key(rec: dict[str, object]) -> str:
    """Fingerprint v1 de AcopioCenter sobre dict: event_id + name + location."""
    return _hash_normalized_parts(
        normalize_for_match(str(rec.get("event_id") or "")),
        normalize_for_match(str(rec.get("name") or "")),
        normalize_for_match(str(rec.get("location_text") or "")),
    )


# --- Block keys -------------------------------------------------------------

def person_block_keys(rec: dict[str, object]) -> list[str]:
    """Claves de bloqueo de Person, orden estable [fuerte?, fonetica].

    Fuerte (solo si hay cedula_hmac no vacio): ced:{event_id}:{cedula_hmac}.
    cedula_hmac ya es un HMAC opaco; no se re-hashea.
    Fonetica (siempre): phon:{event_id}:{estado}:{phonetic_hash(full_name)}.
    """
    event_id = str(rec.get("event_id") or "")
    estado = normalize_for_match(str(rec.get("last_known_location") or ""))
    ph = phonetic_hash(str(rec.get("full_name") or ""))
    keys: list[str] = []
    cedula_hmac = rec.get("cedula_hmac")
    if isinstance(cedula_hmac, str) and cedula_hmac.strip():
        keys.append(f"ced:{event_id}:{cedula_hmac}")
    keys.append(f"phon:{event_id}:{estado}:{ph}")
    return keys


def acopio_block_keys(rec: dict[str, object]) -> list[str]:
    """Clave de bloqueo fonetica de AcopioCenter, simetrica al fingerprint."""
    event_id = str(rec.get("event_id") or "")
    estado = normalize_for_match(str(rec.get("location_text") or ""))
    ph = phonetic_hash(str(rec.get("name") or ""))
    return [f"phon:{event_id}:{estado}:{ph}"]


# --- Despachadores por tipo -------------------------------------------------

def dedup_key(rec: dict[str, object], entity_type: str) -> str | None:
    """Hash de identidad de contenido por tipo (dedup_hash que va al backend).

    Event/AcopioCenter usan el fingerprint v1. Person usa su deterministic_id
    (ya calculado en el enriquecimiento del pipeline); None si falta, para que
    el backend distinga ausencia de hash de un hash real (columna nullable).
    """
    if entity_type == "Event":
        return event_dedup_key(rec)
    if entity_type == "AcopioCenter":
        return acopio_dedup_key(rec)
    det = rec.get("deterministic_id")
    return str(det) if det else None


def block_keys(rec: dict[str, object], entity_type: str) -> list[str]:
    """Lista de block keys informativas para Stage 2 segun el tipo."""
    if entity_type == "Person":
        return person_block_keys(rec)
    if entity_type == "AcopioCenter":
        return acopio_block_keys(rec)
    return []
