"""
scrapers/parsers/acopio_ve_parser.py
=====================================
Parser concreto para la fuente comunitaria **Acopio VE**
(``acopio-ve-2026.web.app`` — issue #99).

Recibe el ``RawContent`` producido por ``ApiAdapter`` contra el endpoint
público de Firebase RTDB y devuelve ``list[AcopioCenter]``.

Mapeo de campos
---------------
API field          → AcopioCenter field
-----------------  -----------------------------------------------
nombre             name            (normalize_text — preserva casing)
estado / municipio location_text   (normalize_location → string legible)
lat / lng          coordinates     ({"lat": ..., "lon": ...}; None si inválidas)
insumos            needs           (texto libre → keyword controlado; ver abajo)
capacidad          status          (ver _CAPACIDAD_STATUS_MAP)
capacidad          nota            (se conserva el valor crudo para trazabilidad)
actualizadoEn      nota            (epoch ms → ISO 8601 UTC)

Mapeo de capacidad → status
---------------------------
API value          → AcopioCenter.status enum
-----------------  -------------------------
disponible         active
parcial            active
lleno              full
*cualquier otro*   unverified

PII
---
Un centro de acopio es un lugar público, no una persona. El modelo
``AcopioCenter`` actual no tiene campos de contacto y la fuente no expone
PII de personas en el esquema documentado, así que este parser **no
tokeniza ni persiste PII**. Los mensajes de log no incluyen valores de
campos del registro.

Forma del payload (supuesto a confirmar)
----------------------------------------
Firebase RTDB en ``/centros.json`` puede devolver el contenido como un
objeto ``{push_id: {centro}}`` o como una lista ``[{centro}]``. El parser
tolera ambas formas (y también un envoltorio ``{"data": [...]}``). La forma
exacta debe confirmarse con el dev de Acopio VE antes de habilitar la
fuente en producción (ver nota en el YAML de fuentes).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models import AcopioCenter
from scrapers.normalizers import normalize_for_match, normalize_location, normalize_text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SOURCE_KEY = "acopio_ve"
FUENTE_LABEL = "acopio-ve-2026.web.app"
DEFAULT_TRUST_TIER = "C"   # data comunitaria en tiempo real, sin validación cruzada

# Valor de capacidad de la fuente → enum status de AcopioCenter.
_CAPACIDAD_STATUS_MAP: dict[str, str] = {
    "disponible": "active",
    "parcial":    "active",
    "lleno":      "full",
}

# Sinónimos de insumos (texto libre) → keyword controlado del contrato.
# Las claves van en forma normalizada para match (minúscula, sin acentos,
# ñ→n) porque se comparan contra la salida de ``normalize_for_match``.
# La comparación es por substring, así que basta la raíz del término
# ("aliment" cubre alimento/alimentos/alimentación).
_NEED_SYNONYMS: tuple[tuple[str, str], ...] = (
    ("agua",            "agua"),
    ("aliment",         "alimentos"),
    ("comida",          "alimentos"),
    ("viver",           "alimentos"),
    ("pereceder",       "alimentos"),
    ("medicament",      "medicamentos"),
    ("medicina",        "medicamentos"),
    ("farmac",          "medicamentos"),
    ("colchon",         "colchonetas"),
    ("ropa",            "ropa"),
    ("vestiment",       "ropa"),
    ("calzado",         "calzado"),
    ("zapato",          "calzado"),
    ("higiene",         "higiene"),
    ("aseo",            "higiene"),
    ("panal",           "pañales"),
    ("leche",           "leche_formula"),
    ("formula",         "leche_formula"),
    ("generador",       "generador"),
    ("planta electrica", "generador"),
    ("combustible",     "combustible"),
    ("gasolina",        "combustible"),
    ("gasoil",          "combustible"),
    ("diesel",          "combustible"),
    ("herramient",      "herramientas"),
    ("voluntari",       "voluntarios"),
    ("transporte",      "transporte"),
    ("vehiculo",        "transporte"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_status(raw_capacidad: Any) -> str:
    """Convierte el valor de capacidad de la fuente al enum status."""
    if not raw_capacidad:
        return "unverified"
    return _CAPACIDAD_STATUS_MAP.get(normalize_for_match(str(raw_capacidad)), "unverified")


def _location_text(rec: dict[str, Any]) -> str | None:
    """
    Construye un ``location_text`` legible a partir de estado y municipio.

    Combina ``"Municipio, Estado"`` y lo pasa por ``normalize_location`` para
    canonicalizar nombres de estados venezolanos. Si la fuente no trae ni
    estado ni municipio, devuelve ``None`` (el registro se omite: el modelo
    exige ``location_text`` no vacío).
    """
    estado = normalize_text(rec.get("estado"))
    municipio = normalize_text(rec.get("municipio"))

    if municipio and estado:
        raw = f"{municipio}, {estado}"
    elif estado:
        raw = estado
    elif municipio:
        raw = municipio
    else:
        return None

    loc = normalize_location(raw)
    loc_estado = loc.get("estado")
    loc_municipio = loc.get("municipio")
    loc_raw = loc.get("raw")

    if loc_municipio and loc_estado:
        return f"{loc_municipio}, {loc_estado}"
    if loc_estado:
        return str(loc_estado)
    if loc_raw:
        return str(loc_raw)
    return raw or None


def _coordinates(lat: Any, lng: Any) -> dict[str, float] | None:
    """
    Convierte lat/lng de la fuente al dict que valida ``AcopioCenter``.

    Devuelve ``None`` (en vez de lanzar) si faltan, no son numéricas o están
    fuera de rango — un centro con nombre y ubicación textual sigue siendo
    útil aunque las coordenadas vengan mal; no se descarta por eso.

    Nota: el modelo usa la clave ``"lon"`` (no ``"lng"``), así que aquí se
    traduce el nombre del campo de la fuente.
    """
    if lat is None or lng is None:
        return None
    try:
        lat_f = float(lat)
        lon_f = float(lng)
    except (TypeError, ValueError):
        return None
    if not -90.0 <= lat_f <= 90.0 or not -180.0 <= lon_f <= 180.0:
        return None
    return {"lat": lat_f, "lon": lon_f}


def _normalize_need(raw_insumo: Any) -> str | None:
    """
    Mapea un insumo de texto libre al keyword controlado del contrato.

    Devuelve ``None`` si el insumo está vacío, o ``"otro"`` si no coincide
    con ningún sinónimo conocido (el contrato exige que lo desconocido caiga
    en ``otro``, nunca que se descarte).
    """
    norm = normalize_for_match(raw_insumo if isinstance(raw_insumo, str) else str(raw_insumo or ""))
    if not norm:
        return None
    for synonym, keyword in _NEED_SYNONYMS:
        if synonym in norm:
            return keyword
    return "otro"


def _normalize_needs(insumos: Any) -> list[str]:
    """Normaliza la lista de insumos a keywords controlados, sin duplicados."""
    if isinstance(insumos, str):
        items: list[Any] = [insumos]
    elif isinstance(insumos, (list, tuple)):
        items = list(insumos)
    else:
        return []

    result: list[str] = []
    for item in items:
        keyword = _normalize_need(item)
        if keyword and keyword not in result:
            result.append(keyword)
    return result


def _iso_from_epoch_ms(value: Any) -> str | None:
    """Convierte un timestamp epoch en milisegundos a ISO 8601 UTC."""
    if value is None:
        return None
    try:
        seconds = float(value) / 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _build_nota(rec: dict[str, Any]) -> str | None:
    """
    Conserva en ``nota`` la capacidad cruda y la fecha de actualización.

    ``capacidad`` se mapea a ``status`` (perdiendo el matiz disponible/parcial),
    así que el valor original se guarda aquí para no perder trazabilidad.
    """
    parts: list[str] = []
    capacidad = normalize_text(rec.get("capacidad"))
    if capacidad:
        parts.append(f"[capacidad:{capacidad}]")
    iso = _iso_from_epoch_ms(rec.get("actualizadoEn"))
    if iso:
        parts.append(f"actualizado_en={iso}")
    return " ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

class AcopioVeParser:
    """
    Parser para la API pública de Acopio VE (Firebase RTDB).

    Implementa ``ParserProtocol``.

    Parameters
    ----------
    event_id:
        UUID del evento al que pertenecen los registros, inyectado por el
        orquestador desde ``project.event_id`` del YAML de config. El parser
        no lo deriva ni lo valida — solo lo propaga a cada ``AcopioCenter``.
    """

    source_key: str = SOURCE_KEY

    def __init__(self, event_id: str) -> None:
        self._event_id = event_id

    # ------------------------------------------------------------------
    # ParserProtocol: parse
    # ------------------------------------------------------------------

    def parse(self, raw: RawContent, **kwargs: Any) -> list[AcopioCenter]:
        """
        Extrae centros de un RawContent y devuelve list[AcopioCenter].

        Tolerante a errores por registro: si un registro no puede convertirse
        en AcopioCenter, se omite y se loguea; el resto sigue.
        """
        records = self._extract_records(raw)

        results: list[AcopioCenter] = []
        for rec in records:
            try:
                center = self._parse_record(rec)
                if center is not None:
                    results.append(center)
            except Exception as exc:
                log.warning("%s: registro malformado omitido: %s", SOURCE_KEY, exc)

        log.debug("%s: %d/%d centros parseados", SOURCE_KEY, len(results), len(records))
        return results

    @staticmethod
    def _extract_records(raw: RawContent) -> list[dict[str, Any]]:
        """
        Normaliza las distintas formas del payload a una lista de dicts.

        Soporta lista directa, envoltorio ``{"data": [...]}`` y el objeto
        ``{push_id: {centro}}`` típico de Firebase RTDB.
        """
        payload = raw.get("raw_content")

        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]

        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
            # Firebase RTDB: objeto keyado por push-id → tomar los valores.
            return [v for v in payload.values() if isinstance(v, dict)]

        log.warning(
            "%s: raw_content inesperado (tipo %s) — página ignorada",
            SOURCE_KEY, type(payload).__name__,
        )
        return []

    # ------------------------------------------------------------------
    # Lógica por registro
    # ------------------------------------------------------------------

    def _parse_record(self, rec: dict[str, Any]) -> AcopioCenter | None:
        """
        Convierte un dict de la fuente en AcopioCenter.

        Devuelve None si falta el nombre o no se puede construir una ubicación
        (ambos obligatorios en el modelo). No lanza: cualquier fallo de
        validación Pydantic se captura y loguea.
        """
        name = normalize_text(rec.get("nombre"))
        if not name:
            log.warning("%s: registro sin nombre — omitido", SOURCE_KEY)
            return None

        location_text = _location_text(rec)
        if not location_text:
            log.warning("%s: registro sin ubicación — omitido", SOURCE_KEY)
            return None

        coordinates = _coordinates(rec.get("lat"), rec.get("lng"))
        needs = _normalize_needs(rec.get("insumos"))
        status = _map_status(rec.get("capacidad"))
        nota = _build_nota(rec)

        try:
            return AcopioCenter(
                name=name,
                event_id=self._event_id,
                location_text=location_text,
                coordinates=coordinates,
                needs=needs,
                status=status,
                trust_tier=DEFAULT_TRUST_TIER,
                confidence_score=0.0,
                fuente=FUENTE_LABEL,
                nota=nota,
            )
        except Exception as exc:
            log.warning("%s: registro no pudo construirse como AcopioCenter: %s", SOURCE_KEY, exc)
            return None
