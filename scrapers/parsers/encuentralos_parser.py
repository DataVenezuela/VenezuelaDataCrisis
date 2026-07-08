"""
scrapers/parsers/encuentralos_parser.py
========================================
Parser concreto para ``encuentralos.tecnosoft.dev``.

Recibe el ``RawContent`` producido por ``ApiAdapter`` contra
``/api/personas?limit=20&offset=N`` y devuelve ``list[Person]``.

Mapeo de campos (schema vigente)
---------------------------------
API field          → Person field
-----------------  -----------------------
nombre             full_name  (normalize_proper_name)
cedula (cleartext) cedula_hmac + cedula_masked; identity_kind="hmac"; pii_provenance="cleartext"
cedula (masked)    unmapped["cedula_source_masked"]; identity_kind="none"; pii_provenance="source_masked_lossy"
cedula (absent)    identity_kind="none"; pii_provenance="cleartext"
ultima_ubicacion   last_known_location  (string libre → normalize_location → str legible)
estado             status  (ver _STATUS_MAP abajo)
descripcion        nota
edad               age_range  (edad puntual → {"min": N, "max": N})
edad               is_minor  (True si edad < 18; None si no hay edad)
foto               foto  (URL o None — sin modificar, el parser no descarga)
id                 nota  (prefijo "[id:UUID]")
ultima_vez         (descartado)

Mapeo de status
---------------
API value          → Person.status enum
-----------------  ----------------------
desaparecido       missing
encontrado         found
herido             injured
fallecido          deceased
*cualquier otro*   unknown

Cédula pre-mascarada
---------------------
La API entrega la cédula censurada (e.g. ``"22•••52"``).
No es posible calcular el HMAC sobre el valor censurado; en ese caso
``cedula_hmac`` y ``cedula_masked`` quedan ``None``, ``identity_kind``
es ``"none"``, ``pii_provenance`` es ``"source_masked_lossy"``, y el
valor censurado se guarda en ``unmapped["cedula_source_masked"]`` para
que el detector de PII confirme que ya viene enmascarado por la fuente.
Si la cédula llega en claro, se tokeniza normalmente (identity_kind="hmac").

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
from shared.hashing import identity_token
from shared.helpers import mask_last4

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
    "sin_informacion": "unknown",
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


def _mask_cedula(raw: str) -> str | None:
    """Devuelve "****XXXX" con los últimos 4 dígitos de la cédula, o None si la cédula es inválida."""
    try:
        return mask_last4(raw)
    except ValueError:
        return None


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


def _is_pre_masked_cedula(raw: str) -> bool:
    """Detecta '•' (bullet) o '*' (asterisco), los marcadores que esta API usa actualmente."""
    return "•" in raw or "*" in raw


def _build_nota(record: dict[str, Any]) -> str | None:
    """
    Combina el id externo y la descripcion en una nota.

    Formato: "[id:UUID] Texto de descripcion." o solo el texto.
    El id se conserva para mantener trazabilidad hacia la fuente original.
    """
    parts: list[str] = []
    rec_id = record.get("id")
    if rec_id is not None:
        parts.append(f"[id:{rec_id}]")
    desc = (record.get("descripcion") or "").strip()
    if desc:
        parts.append(desc)
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
        # La API devuelve {"items": [...], "total": N}
        if isinstance(payload, dict):
            records = payload.get("items") or []
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
        raw_id = rec.get("id")
        rec_id = str(raw_id) if raw_id is not None else "?"
        source_record_id: str | None = str(raw_id) if raw_id is not None else None

        # ── full_name ─────────────────────────────────────────────────
        raw_nombre = rec.get("nombre") or ""
        full_name = normalize_proper_name(raw_nombre)
        if not full_name:
            log.warning("%s: registro id=%s sin nombre — omitido", SOURCE_KEY, rec_id)
            return None

        # ── cédula → hmac + masked ─────────────────────────────────────
        # La API entrega la cédula pre-mascarada ("22•••52"); HMAC imposible.
        cedula_hmac: str | None = None
        cedula_masked: str | None = None
        identity_kind = "none"
        pii_provenance = "cleartext"
        unmapped: dict[str, Any] | None = None
        raw_cedula = rec.get("cedula")
        if raw_cedula:
            raw_cedula_str = str(raw_cedula).strip()
            if raw_cedula_str and _is_pre_masked_cedula(raw_cedula_str):
                # La fuente entregó la cédula ya censurada — no podemos
                # calcular HMAC. Guardamos el valor en unmapped para que
                # el detector de PII confirme que ya viene enmascarado.
                pii_provenance = "source_masked_lossy"
                unmapped = {"cedula_source_masked": raw_cedula_str}
            elif raw_cedula_str:
                pii_provenance = "cleartext"
                cedula_masked = _mask_cedula(raw_cedula_str)
                if self._secret:
                    try:
                        cedula_hmac = identity_token(raw_cedula_str, self._secret)
                        identity_kind = "hmac"
                    except Exception as exc:
                        log.warning(
                            "%s: id=%s error tokenizando cédula: %s",
                            SOURCE_KEY, rec_id, exc,
                        )

        # ── ubicación ─────────────────────────────────────────────────
        # La API ahora entrega ultima_ubicacion como string libre.
        _ult = rec.get("ultima_ubicacion")
        location_raw: str | None = _ult.strip() or None if isinstance(_ult, str) else None

        last_known_location: str | None = None
        if location_raw:
            loc_obj = normalize_location(location_raw)
            last_known_location = _location_str(loc_obj)

        # ── status ────────────────────────────────────────────────────
        status = _map_status(rec.get("estado"))

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
                identity_kind=identity_kind,
                pii_provenance=pii_provenance,
                age_range=age_range,
                is_minor=is_minor,
                last_known_location=last_known_location,
                status=status,
                trust_tier=DEFAULT_TRUST_TIER,
                confidence_score=0.0,
                nota=nota,
                foto=foto,
                source_record_id=source_record_id,
                fuente=FUENTE_LABEL,
                unmapped=unmapped,
            )
        except Exception as exc:
            log.warning(
                "%s: id=%s no pudo construirse como Person: %s",
                SOURCE_KEY, rec_id, exc,
            )
            return None
