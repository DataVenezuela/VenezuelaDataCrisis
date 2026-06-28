"""
scrapers/parsers/encuentralos_parser.py
========================================
Parser concreto para ``encuentralos.tecnosoft.dev``.

Recibe el ``RawContent`` producido por ``ApiAdapter`` contra
``/api/personas?limit=20&offset=N`` y devuelve ``list[Person]``.

Mapeo de campos
---------------
API field          → Person field
-----------------  -----------------------
nombre             full_name  (normalize_proper_name)
cedula             cedula_hmac  (identity_token via PII_HMAC_SECRET)
                   cedula_masked  (últimos 4 dígitos con máscara)
estado / municipio last_known_location  (normalize_location → str legible)
status             status  (ver _STATUS_MAP abajo)
observaciones      nota
edad               age_range  (edad puntual → {"min": N, "max": N})
edad               is_minor  (True si edad < 18; None si no hay edad)
foto               foto  (URL o None — sin modificar, el parser no descarga)
id                 nota  (prefijo "[id:N]" si hay observaciones)

Mapeo de status
---------------
API value          → Person.status enum
-----------------  ----------------------
desaparecido       missing
encontrado         found
herido             injured
fallecido          deceased
*cualquier otro*   unknown

Cédula
------
Si el campo ``cedula`` está presente y no vacío:
  - ``cedula_hmac``   = identity_token(cedula, secret)  → 64-hex sin prefijo
  - ``cedula_masked`` = "****" + últimos 4 dígitos numéricos

El secreto se obtiene de la variable de entorno ``PII_HMAC_SECRET``.
Si la variable no está, el parser produce ``cedula_hmac=None`` y loguea un
warning — nunca lanza excepción por un registro individual.

Nota de seguridad
-----------------
El campo ``telefono_contacto`` de la API NO se persiste.  El parser lo
descarta explícitamente para evitar almacenar PII de terceros (familiar
que reportó la persona desaparecida).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models import Person
from scrapers.normalizers import derive_is_minor, normalize_location, normalize_proper_name
from scrapers.sanitizers.pii_tokenizer import _masked_last4
from shared.hashing import identity_token

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SOURCE_KEY = "encuentralos_tecnosoft"
FUENTE_LABEL = "encuentralos.tecnosoft.dev"
DEFAULT_TRUST_TIER = "C"   # voluntario/comunidad con ownership visible

# Mapeo de los valores de status que devuelve la API al enum de Person.
_STATUS_MAP: dict[str, str] = {
    "desaparecido":  "missing",
    "desaparecida":  "missing",
    "encontrado":    "found",
    "encontrada":    "found",
    "herido":        "injured",
    "herida":        "injured",
    "fallecido":     "deceased",
    "fallecida":     "deceased",
    "muerto":        "deceased",
    "muerta":        "deceased",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_status(raw_status: str | None) -> str:
    """Convierte el valor de status de la API al enum interno de Person."""
    if not raw_status:
        return "unknown"
    normalized = raw_status.strip().lower()
    return _STATUS_MAP.get(normalized, "unknown")


def _mask_cedula(raw: str) -> str:
    """Devuelve "****XXXX" con los últimos 4 dígitos de la cédula."""
    try:
        return _masked_last4(raw)
    except ValueError:
        return "****????"


def _location_str(location_obj: dict[str, Any]) -> str | None:
    """
    Convierte el LocationObject de normalize_location en un string legible.

    Prioriza: municipio + estado → solo estado → raw → None.
    """
    estado = location_obj.get("estado")
    municipio = location_obj.get("municipio")
    raw = location_obj.get("raw")

    if municipio and estado:
        return f"{municipio}, {estado}"
    if estado:
        return str(estado)
    if raw:
        return str(raw)
    return None


def _age_range(edad: Any) -> dict[str, int] | None:
    """Edad puntual de la API → age_range con min==max."""
    if edad is None:
        return None
    try:
        age = int(edad)
        if age < 0 or age > 130:
            return None
        return {"min": age, "max": age}
    except (TypeError, ValueError):
        return None


def _build_nota(record: dict[str, Any]) -> str | None:
    """
    Combina el id externo y las observaciones en una nota.

    Formato: "[id:1234] Texto de observaciones." o solo el texto.
    El id se conserva para mantener trazabilidad hacia la fuente original.
    """
    parts: list[str] = []
    rec_id = record.get("id")
    if rec_id is not None:
        parts.append(f"[id:{rec_id}]")
    obs = (record.get("observaciones") or "").strip()
    if obs:
        parts.append(obs)
    return " ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

class EncuentralosParser:
    """
    Parser para la API de personas de encuentralos.tecnosoft.dev.

    Implementa ``ParserProtocol``.

    Parameters
    ----------
    event_id:
        UUID del evento al que pertenecen los registros, inyectado por el
        orquestador desde ``project.event_id`` del YAML de config.  El
        parser no lo deriva ni lo valida — solo lo propaga a cada ``Person``.
    secret:
        Secreto HMAC para tokenizar cédulas.  Si no se pasa, se lee de
        la variable de entorno ``PII_HMAC_SECRET``.  Si tampoco está la
        variable, ``cedula_hmac`` quedará ``None`` en los registros.
    """

    source_key: str = SOURCE_KEY

    def __init__(self, event_id: str, secret: str | None = None) -> None:
        self._event_id = event_id
        self._secret: str | None = secret or os.getenv("PII_HMAC_SECRET") or None
        if not self._secret:
            log.warning(
                "PII_HMAC_SECRET no configurado — cedula_hmac será None en todos los registros. "
                "Configura la variable de entorno antes de ejecutar en producción."
            )

    # ------------------------------------------------------------------
    # ParserProtocol: parse
    # ------------------------------------------------------------------

    def parse(self, raw: RawContent, **kwargs: Any) -> list[Person]:
        """
        Extrae registros de un RawContent de la API y devuelve list[Person].

        Tolerante a errores por registro: si un registro no puede convertirse
        en Person, se omite y se loguea el id externo.  El resto sigue.
        """
        payload = raw.get("raw_content", {})

        # La API devuelve {"data": [...], "total": N}
        if isinstance(payload, dict):
            records = payload.get("data") or []
        elif isinstance(payload, list):
            # Compatibilidad si el adapter entregó la lista directa
            records = payload
        else:
            log.warning(
                "%s: raw_content inesperado (tipo %s) — página ignorada",
                SOURCE_KEY, type(payload).__name__,
            )
            return []

        results: list[Person] = []
        for rec in records:
            try:
                person = self._parse_record(rec)
                if person is not None:
                    results.append(person)
            except Exception as exc:
                log.warning(
                    "%s: registro malformado omitido: %s",
                    SOURCE_KEY, exc,
                )

        log.debug("%s: %d/%d registros parseados", SOURCE_KEY, len(results), len(records))
        return results

    # ------------------------------------------------------------------
    # Lógica por registro
    # ------------------------------------------------------------------

    def _parse_record(self, rec: dict[str, Any]) -> Person | None:
        """
        Convierte un dict de la API en Person.

        Devuelve None si el registro no tiene nombre (campo obligatorio).
        No lanza excepción — cualquier fallo de validación Pydantic se
        captura y loguea.
        """
        rec_id = rec.get("id", "?")

        # ── full_name ─────────────────────────────────────────────────
        raw_nombre = rec.get("nombre") or ""
        full_name = normalize_proper_name(raw_nombre)
        if not full_name:
            log.warning("%s: registro id=%s sin nombre — omitido", SOURCE_KEY, rec_id)
            return None

        # ── cédula → hmac + masked ─────────────────────────────────────
        cedula_hmac: str | None = None
        cedula_masked: str | None = None
        raw_cedula = rec.get("cedula")
        if raw_cedula:
            raw_cedula_str = str(raw_cedula).strip()
            if raw_cedula_str:
                cedula_masked = _mask_cedula(raw_cedula_str)
                if self._secret:
                    try:
                        cedula_hmac = identity_token(raw_cedula_str, self._secret)
                    except Exception as exc:
                        log.warning(
                            "%s: id=%s error tokenizando cédula: %s",
                            SOURCE_KEY, rec_id, exc,
                        )

        # ── ubicación ─────────────────────────────────────────────────
        # Combinar estado + municipio en un string para normalize_location
        raw_estado = (rec.get("estado") or "").strip()
        raw_municipio = (rec.get("municipio") or "").strip()

        if raw_municipio and raw_estado:
            location_raw = f"{raw_municipio}, {raw_estado}"
        elif raw_estado:
            location_raw = raw_estado
        elif raw_municipio:
            location_raw = raw_municipio
        else:
            location_raw = None

        last_known_location: str | None = None
        if location_raw:
            loc_obj = normalize_location(location_raw)
            last_known_location = _location_str(loc_obj)

        # ── status ────────────────────────────────────────────────────
        status = _map_status(rec.get("status"))

        # ── edad → age_range / is_minor ─────────────────────────────────
        age_range = _age_range(rec.get("edad"))
        is_minor = derive_is_minor(age_range)

        # ── nota (id externo + observaciones) ─────────────────────────
        nota = _build_nota(rec)

        # ── foto (URL cruda — el parser no descarga) ───────────────────
        foto = rec.get("foto") or None

        # telefono_contacto se descarta aquí — PII de tercero, no se persiste.

        # ── construir Person ──────────────────────────────────────────
        try:
            return Person(
                full_name=full_name,
                event_id=self._event_id,
                cedula_hmac=cedula_hmac,
                cedula_masked=cedula_masked,
                age_range=age_range,
                is_minor=is_minor,
                last_known_location=last_known_location,
                status=status,
                trust_tier=DEFAULT_TRUST_TIER,
                confidence_score=0.0,
                nota=nota,
                foto=foto,
                fuente=FUENTE_LABEL,
            )
        except Exception as exc:
            log.warning(
                "%s: id=%s no pudo construirse como Person: %s",
                SOURCE_KEY, rec_id, exc,
            )
            return None
