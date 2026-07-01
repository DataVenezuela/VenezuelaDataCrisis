"""
scrapers/parsers/acopio_ve_parser.py
=====================================
Parser concreto para la fuente comunitaria **Acopio VE** (issue #99).

Recibe el ``RawContent`` producido por ``ApiAdapter`` contra Firebase RTDB
(``centros.json`` / ``rg_centros.json``) y devuelve ``list[AcopioCenter]``.

Mapeo de campos (contrato canónico AcopioVE / Firebase)
---------------------------------------------------------
API field        -> AcopioCenter field
---------------  -----------------------------------------------
nombre           name            (normalize_text — preserva casing)
municipio/estado location_text   ("Municipio, Estado"; fallback a direccion)
lat / lng        coordinates     ({"lat": ..., "lon": ...}; None si inválidas)
insumos          needs           (categorías -> keyword controlado; ver abajo)
capacidad        status          (ver _CAPACIDAD_STATUS_MAP)
key Firebase /   nota            (trazabilidad: id upstream, capacidad cruda,
id / _source /                    fecha y fuente upstream)
actualizadoEn

Mapeo de capacidad -> status
-------------------------
API value        -> AcopioCenter.status enum
---------------  -------------------------
disponible       active
parcial          active
lleno            full
*cualquier otro* unverified

Categorías de ``insumos``
------------------------
La fuente devuelve ``insumos`` con casing mixto y categorías multi-palabra
(``"Alimentos no perecederos"``, ``"Artículos de bebé"``, ``"Frazadas"``...).
Se mapean al vocabulario controlado del contrato por substring; lo que no
encaja cae en ``otro`` (regla del contrato) y el valor crudo se
conserva en ``nota`` para no perder ninguna categoría real.

PII
---
Un centro de acopio es un lugar público, no una persona. El modelo
``AcopioCenter`` no tiene campos de comunicación directa. Ese dato de la
fuente no se almacena ni se loguea. Los mensajes de log no incluyen valores
de campos del registro.

Forma del payload
-----------------
Firebase devuelve ``{id: {centro}}``. El parser también tolera ``{"data":
[... ]}`` y una lista directa por robustez/fallback legacy.
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

# Valor del campo ``capacidad`` de la fuente -> enum status de AcopioCenter.
_CAPACIDAD_STATUS_MAP: dict[str, str] = {
    "disponible": "active",
    "parcial": "active",
    "lleno": "full",
    "cerrado": "closed",
}

# Fallback legacy para la API intermedia observada en PR #123.
_LEGACY_ESTADO_STATUS_MAP: dict[str, str] = {
    "abierto": "active",
    "lleno": "full",
    "cerrado": "closed",
}

# Sinónimos de categorías de insumos -> keyword controlado del contrato.
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

def _map_status(raw_value: Any, *, legacy: bool = False) -> str:
    """Convierte capacidad, o estado legacy, al enum status del modelo."""
    if not raw_value:
        return "unverified"
    mapping = _LEGACY_ESTADO_STATUS_MAP if legacy else _CAPACIDAD_STATUS_MAP
    return mapping.get(normalize_for_match(str(raw_value)), "unverified")


def _location_text(rec: dict[str, Any]) -> str | None:
    """
    Construye un ``location_text`` legible.

    Contrato canónico: ``"Municipio, Estado"`` con fallback a ``direccion``.
    Fallback legacy: ``ciudad/pais`` y ``address``. Si no hay nada, ``None``
    (el registro se omite: el modelo exige ``location_text`` no vacío).
    """
    municipio = normalize_text(rec.get("municipio"))
    estado_geo = normalize_text(rec.get("estado")) if _is_canonical_record(rec) else None

    if municipio and estado_geo:
        return f"{municipio}, {estado_geo}"
    if municipio:
        return municipio
    if estado_geo:
        return estado_geo

    direccion = normalize_text(rec.get("direccion"))
    if direccion:
        return direccion

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


def _safe_text(value: Any) -> str | None:
    """Normaliza valores escalares sin asumir que todos llegan como string."""
    if value is None:
        return None
    return normalize_text(str(value))


def _normalize_needs(raw_needs: Any) -> list[str]:
    """Normaliza insumos/recibe (lista o string separado por comas) a keywords."""
    if isinstance(raw_needs, str):
        items: list[Any] = raw_needs.split(",")
    elif isinstance(raw_needs, (list, tuple)):
        items = list(raw_needs)
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

    Incluye el UUID upstream, la capacidad cruda, los insumos crudos, la
    fecha de actualización y la fuente upstream. No incluye canales directos
    de comunicación personal.
    """
    parts: list[str] = []

    tipo = _safe_text(rec.get("tipo"))
    if tipo:
        parts.append(f"[tipo:{tipo}]")

    upstream_id = _safe_text(rec.get("id_origen") or rec.get("id") or rec.get("_firebase_key"))
    if upstream_id:
        parts.append(f"id_origen: {upstream_id}")

    capacidad = _safe_text(rec.get("capacidad"))
    if capacidad:
        parts.append(f"capacidad: {capacidad}")

    raw_needs = rec.get("insumos", rec.get("recibe"))
    if isinstance(raw_needs, (list, tuple)) and raw_needs:
        parts.append("insumos: " + ", ".join(_safe_text(r) or "" for r in raw_needs))
    elif isinstance(raw_needs, str) and raw_needs.strip():
        parts.append(f"insumos: {_safe_text(raw_needs)}")

    updated_at = _safe_text(rec.get("actualizadoEn") or rec.get("updated_at"))
    if updated_at:
        parts.append(f"actualizado: {updated_at}")

    fuente_upstream = _safe_text(rec.get("_source") or rec.get("fuente"))
    if fuente_upstream:
        parts.append(f"fuente_origen: {fuente_upstream}")

    return " | ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

def _is_canonical_record(rec: dict[str, Any]) -> bool:
    """Detecta el contrato Firebase para no confundir estado geografico con status."""
    return any(key in rec for key in ("nombre", "municipio", "direccion", "capacidad", "insumos", "actualizadoEn"))


class AcopioVeParser:
    """
    Parser para la fuente pública de Acopio VE.

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
                log.warning(
                    "%s: registro malformado omitido (error_type=%s)",
                    SOURCE_KEY,
                    type(exc).__name__,
                )

        log.debug("%s: %d/%d centros parseados", SOURCE_KEY, len(results), len(records))
        return results

    @staticmethod
    def _extract_records(raw: RawContent) -> list[dict[str, Any]]:
        """
        Normaliza las distintas formas del payload a una lista de dicts.

        Forma canónica de Firebase: ``{id: {centro}}``. También tolera una
        lista directa y ``{"data": [...]}`` por fallback legacy.
        """
        payload = raw.get("raw_content")

        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]

        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)]
            records: list[dict[str, Any]] = []
            for key, value in payload.items():
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("_firebase_key", str(key))
                    records.append(item)
            return records

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
        name = normalize_text(rec.get("nombre") or rec.get("name"))
        if not name:
            log.warning("%s: registro sin nombre — omitido", SOURCE_KEY)
            return None

        location_text = _location_text(rec)
        if not location_text:
            log.warning("%s: registro sin ubicación — omitido", SOURCE_KEY)
            return None

        coordinates = _coordinates(rec.get("lat"), rec.get("lng"))
        needs = _normalize_needs(rec.get("insumos", rec.get("recibe")))
        if "capacidad" in rec:
            status = _map_status(rec.get("capacidad"))
        elif _is_canonical_record(rec):
            status = "unverified"
        else:
            status = _map_status(rec.get("estado"), legacy=True)
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
            log.warning(
                "%s: registro no pudo construirse como AcopioCenter (error_type=%s)",
                SOURCE_KEY,
                type(exc).__name__,
            )
            return None
