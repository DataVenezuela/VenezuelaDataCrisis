"""
scrapers/parsers/acopio_ve_parser.py
=====================================
Parser concreto para la fuente comunitaria **Acopio VE** (issue #99).

Recibe el ``RawContent`` producido por ``ApiAdapter`` contra
``https://api.acopiove.org/v1/centros`` y devuelve
``list[AcopioCenter]``.

La fuente lista centros de acopio y refugios de la diáspora venezolana en
todo el mundo (no solo en Venezuela), así que la ubicación se toma tal cual
la entrega la API (``ciudad`` + ``pais``) sin normalización geográfica
venezolana.

Mapeo de campos (contra el contrato real de ``/centros``)
---------------------------------------------------------
API field        -> AcopioCenter field
---------------  -----------------------------------------------
name             name            (normalize_text — preserva casing)
ciudad / pais    location_text   ("Ciudad, Pais"; fallback a address)
lat / lng        coordinates     ({"lat": ..., "lon": ...}; None si inválidas)
recibe           needs           (categorías -> keyword controlado; ver abajo)
estado           status          (ver _ESTADO_STATUS_MAP)
id / tipo /      nota            (trazabilidad: id upstream, tipo, necesidad,
recibe /                          recibe crudo, fecha y fuente upstream)
necesita_ahora /
updated_at / fuente

Mapeo de estado -> status
-------------------------
API value        -> AcopioCenter.status enum
---------------  -------------------------
abierto          active
lleno            full
cerrado          closed
*cualquier otro* unverified

Categorías de ``recibe``
------------------------
La API devuelve ``recibe`` con casing mixto y categorías multi-palabra
(``"Alimentos no perecederos"``, ``"Artículos de bebé"``, ``"Frazadas"``...).
Se mapean al vocabulario controlado del contrato por substring; lo que no
encaja cae en ``otro`` (regla del contrato) y el ``recibe`` crudo se
conserva en ``nota`` para no perder ninguna categoría real.

PII
---
Un centro de acopio es un lugar público, no una persona. El modelo
``AcopioCenter`` no tiene campos de contacto, así que el campo ``contacto``
de la fuente (que a veces incluye datos de contacto directo) **no se almacena
ni se loguea**.
Los mensajes de log no incluyen valores de campos del registro.

Forma del payload
-----------------
``/centros`` devuelve ``{"data": [ {centro}, ... ]}``. El parser también
tolera una lista directa y el objeto ``{id: {centro}}`` por robustez.
"""

from __future__ import annotations

import logging
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models import AcopioCenter
from scrapers.normalizers import normalize_for_match, normalize_text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SOURCE_KEY = "acopio_ve"
FUENTE_LABEL = "acopiove.org"
DEFAULT_TRUST_TIER = "C"   # data comunitaria en tiempo real, sin validación cruzada

# Valor del campo ``estado`` de la fuente -> enum status de AcopioCenter.
_ESTADO_STATUS_MAP: dict[str, str] = {
    "abierto": "active",
    "lleno":   "full",
    "cerrado": "closed",
}

# Sinónimos de categorías de ``recibe`` -> keyword controlado del contrato.
# Claves en forma normalizada (minúscula, sin acentos, ñ->n) porque se comparan
# contra la salida de ``normalize_for_match``. Comparación por substring: basta
# la raíz ("aliment" cubre "Alimentos no perecederos").
#
# Las raíces salen de las categorías reales observadas en la API (issue #99):
# la fuente usa texto libre, así que medicamentos/higiene/alimentos agrupan
# también sus insumos (gasas, jabón, enlatados, etc.) al keyword más cercano
# del vocabulario controlado. Lo que no encaja cae en ``otro`` y el ``recibe``
# crudo queda en ``nota``. "Artículos de bebé" no tiene keyword bueno -> otro.
_NEED_SYNONYMS: tuple[tuple[str, str], ...] = (
    # agua
    ("agua",            "agua"),
    # alimentos (incluye no perecederos, enlatados, snacks)
    ("aliment",         "alimentos"),
    ("comida",          "alimentos"),
    ("viver",           "alimentos"),
    ("pereceder",       "alimentos"),
    ("enlatado",        "alimentos"),
    ("galleta",         "alimentos"),
    ("cereal",          "alimentos"),
    # medicamentos e insumos médicos / primeros auxilios
    ("medic",           "medicamentos"),
    ("farmac",          "medicamentos"),
    ("primeros auxilios", "medicamentos"),
    ("auxilio",         "medicamentos"),
    ("gasa",            "medicamentos"),
    ("venda",           "medicamentos"),
    ("sutura",          "medicamentos"),
    ("antisep",         "medicamentos"),
    ("analges",         "medicamentos"),
    ("paracetamol",     "medicamentos"),
    ("ibuprofen",       "medicamentos"),
    ("antidiarreic",    "medicamentos"),
    ("suero",           "medicamentos"),
    ("curacion",        "medicamentos"),
    ("pomada",          "medicamentos"),
    ("micropore",       "medicamentos"),
    ("termometro",      "medicamentos"),
    ("mascarilla",      "medicamentos"),
    ("cubreboca",       "medicamentos"),
    ("guante",          "medicamentos"),
    # higiene (incluye aseo personal e insumos de higiene)
    ("higiene",         "higiene"),
    ("aseo",            "higiene"),
    ("jabon",           "higiene"),
    ("alcohol",         "higiene"),
    ("toalla",          "higiene"),
    ("dental",          "higiene"),
    ("cepillo",         "higiene"),
    ("tampon",          "higiene"),
    # colchonetas y abrigo de cama
    ("colchon",         "colchonetas"),
    ("frazada",         "colchonetas"),
    ("cobija",          "colchonetas"),
    ("manta",           "colchonetas"),
    ("sabana",          "colchonetas"),
    # ropa y calzado
    ("ropa",            "ropa"),
    ("vestiment",       "ropa"),
    ("abrigo",          "ropa"),
    ("calzado",         "calzado"),
    ("zapato",          "calzado"),
    # pañales y leche de fórmula
    ("panal",           "pañales"),
    ("leche",           "leche_formula"),
    ("formula",         "leche_formula"),
    ("mamadera",        "leche_formula"),
    # otros recursos
    ("generador",       "generador"),
    ("combustible",     "combustible"),
    ("gasolina",        "combustible"),
    ("herramient",      "herramientas"),
    ("voluntari",       "voluntarios"),
    ("transporte",      "transporte"),
    ("vehiculo",        "transporte"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _map_status(raw_estado: Any) -> str:
    """Convierte el campo ``estado`` de la fuente al enum status del modelo."""
    if not raw_estado:
        return "unverified"
    return _ESTADO_STATUS_MAP.get(normalize_for_match(str(raw_estado)), "unverified")


def _location_text(rec: dict[str, Any]) -> str | None:
    """
    Construye un ``location_text`` legible: ``"Ciudad, Pais"``.

    Los centros son internacionales, así que NO se aplica normalización
    geográfica venezolana. Fallback a ``address`` y, si no hay nada, ``None``
    (el registro se omite: el modelo exige ``location_text`` no vacío).
    """
    ciudad = normalize_text(rec.get("ciudad"))
    pais = normalize_text(rec.get("pais"))

    if ciudad and pais:
        return f"{ciudad}, {pais}"
    if ciudad:
        return ciudad
    if pais:
        return pais

    address = normalize_text(rec.get("address"))
    return address or None


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


def _normalize_need(raw_categoria: Any) -> str | None:
    """
    Mapea una categoría de ``recibe`` al keyword controlado del contrato.

    Devuelve ``None`` si está vacía, o ``"otro"`` si no coincide con ningún
    sinónimo conocido (el contrato exige que lo desconocido caiga en ``otro``,
    nunca que se descarte — y el ``recibe`` crudo queda en ``nota``).
    """
    norm = normalize_for_match(raw_categoria if isinstance(raw_categoria, str) else str(raw_categoria or ""))
    if not norm:
        return None
    for synonym, keyword in _NEED_SYNONYMS:
        if synonym in norm:
            return keyword
    return "otro"


def _normalize_needs(recibe: Any) -> list[str]:
    """Normaliza ``recibe`` (lista o string separado por comas) a keywords."""
    if isinstance(recibe, str):
        items: list[Any] = recibe.split(",")
    elif isinstance(recibe, (list, tuple)):
        items = list(recibe)
    else:
        return []

    result: list[str] = []
    for item in items:
        keyword = _normalize_need(item)
        if keyword and keyword not in result:
            result.append(keyword)
    return result


def _build_nota(rec: dict[str, Any]) -> str | None:
    """
    Conserva en ``nota`` metadatos de trazabilidad sin PII.

    Incluye el UUID upstream, tipo (acopio/refugio), la necesidad declarada, el
    ``recibe`` crudo (para no perder categorías que cayeron en ``otro``), la
    fecha de actualización y la fuente upstream. No incluye ``contacto``
    (posible PII).
    """
    parts: list[str] = []

    tipo = normalize_text(rec.get("tipo"))
    if tipo:
        parts.append(f"[tipo:{tipo}]")

    upstream_id = normalize_text(rec.get("id"))
    if upstream_id:
        parts.append(f"id_origen: {upstream_id}")

    necesita = normalize_text(rec.get("necesita_ahora"))
    if necesita:
        parts.append(f"necesita: {necesita}")

    recibe = rec.get("recibe")
    if isinstance(recibe, (list, tuple)) and recibe:
        parts.append("recibe: " + ", ".join(normalize_text(str(r)) for r in recibe))
    elif isinstance(recibe, str) and recibe.strip():
        parts.append(f"recibe: {normalize_text(recibe)}")

    updated_at = normalize_text(rec.get("updated_at"))
    if updated_at:
        parts.append(f"actualizado: {updated_at}")

    fuente_upstream = normalize_text(rec.get("fuente"))
    if fuente_upstream:
        parts.append(f"fuente_origen: {fuente_upstream}")

    return " | ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

class AcopioVeParser:
    """
    Parser para la API pública de Acopio VE (``api.acopiove.org/v1/centros``).

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

        Forma real de ``/centros``: ``{"data": [...]}``. También tolera una
        lista directa y el objeto ``{id: {centro}}`` por robustez.
        """
        payload = raw.get("raw_content")

        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]

        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
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
        name = normalize_text(rec.get("name"))
        if not name:
            log.warning("%s: registro sin nombre — omitido", SOURCE_KEY)
            return None

        location_text = _location_text(rec)
        if not location_text:
            log.warning("%s: registro sin ubicación — omitido", SOURCE_KEY)
            return None

        coordinates = _coordinates(rec.get("lat"), rec.get("lng"))
        needs = _normalize_needs(rec.get("recibe"))
        status = _map_status(rec.get("estado"))
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
