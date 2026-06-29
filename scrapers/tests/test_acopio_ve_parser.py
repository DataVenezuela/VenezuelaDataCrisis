"""
scrapers/tests/test_acopio_ve_parser.py
========================================
Tests del AcopioVeParser con fixture sintético (issue #99).

No se realiza ninguna llamada de red. El fixture vive en
``scrapers/tests/fixtures/acopio_ve_sample.json`` y reproduce la estructura
real del endpoint ``api.acopiove.org/v1/centros`` (campos reales, datos 100%
ficticios — sin nombres, direcciones ni contactos reales).

Cobertura
---------
- Mapeo de los campos reales (name/ciudad/pais/lat/lng/recibe/estado) a AcopioCenter
- estado → status (abierto → active, lleno → full, cerrado → closed, ausente → unverified)
- recibe → keywords controlados; categorías reales con casing mixto; desconocido → "otro"
- coordinates: lat/lng → {"lat", "lon"}; inválidas/NaN/Inf → None sin descartar el registro
- location_text desde ciudad + pais (centros internacionales; sin normalize_location VE)
- nota con id upstream, tipo, recibe crudo y fecha (trazabilidad; sin PII de contacto)
- Registro sin nombre → omitido; registro sin ubicación → omitido
- Tolerancia: un registro malo no rompe el resto; raw_content malformado
- Formas de payload: {"data": [...]} (real), lista y {id: centro}
- ParserProtocol satisfecho
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models import AcopioCenter
from scrapers.parsers.base import ParserProtocol
from scrapers.parsers.acopio_ve_parser import (
    AcopioVeParser,
    FUENTE_LABEL,
    SOURCE_KEY,
    _coordinates,
    _map_status,
    _normalize_needs,
)

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "acopio_ve_sample.json"
_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_raw(payload: Any) -> RawContent:
    """Construye un RawContent mínimo con el payload dado."""
    return RawContent(
        source_key=SOURCE_KEY,
        source_url="https://api.acopiove.org/v1/centros",
        fetched_at="2026-06-29T12:00:00Z",
        http_status=200,
        content_type="application/json",
        content_hash="sha256:abc",
        raw_content=payload,
        page=1,
        total_pages=1,
    )


def _parser() -> AcopioVeParser:
    return AcopioVeParser(event_id=_EVENT_ID)


def _by_name(centers: list[AcopioCenter], needle: str) -> AcopioCenter:
    """Devuelve el primer centro cuyo nombre contiene ``needle``."""
    return next(c for c in centers if needle.lower() in c.name.lower())


# ---------------------------------------------------------------------------
# Tests: Protocol
# ---------------------------------------------------------------------------

class TestParserProtocol:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(_parser(), ParserProtocol)

    def test_source_key_attribute(self) -> None:
        assert _parser().source_key == SOURCE_KEY

    def test_parse_returns_list(self) -> None:
        result = _parser().parse(_make_raw(_load_fixture()))
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Tests: Fixture completo
# ---------------------------------------------------------------------------

class TestParseFixture:
    def setup_method(self) -> None:
        self.centers = _parser().parse(_make_raw(_load_fixture()))

    def test_correct_count(self) -> None:
        """Los 4 centros válidos del fixture deben producir 4 AcopioCenter."""
        assert len(self.centers) == 4

    def test_all_are_acopio_instances(self) -> None:
        assert all(isinstance(c, AcopioCenter) for c in self.centers)

    def test_fuente_is_set(self) -> None:
        assert all(c.fuente == FUENTE_LABEL for c in self.centers)

    def test_trust_tier(self) -> None:
        assert all(c.trust_tier == "C" for c in self.centers)

    def test_event_id_propagated(self) -> None:
        assert all(c.event_id == _EVENT_ID for c in self.centers)


# ---------------------------------------------------------------------------
# Tests: Mapeo de campos individuales
# ---------------------------------------------------------------------------

class TestFieldMapping:
    def setup_method(self) -> None:
        self.centers = _parser().parse(_make_raw(_load_fixture()))

    def test_name_preserves_casing(self) -> None:
        c = _by_name(self.centers, "Demo Diáspora")
        # normalize_text no fuerza Title Case: el nombre se conserva.
        assert c.name == "Centro de Acopio Demo Diáspora"

    def test_location_text_ciudad_pais(self) -> None:
        c = _by_name(self.centers, "Demo Diáspora")
        assert c.location_text == "Ciudad de Panamá, Panamá"

    def test_location_text_only_ciudad(self) -> None:
        # Registro con pais=null → solo ciudad.
        c = _by_name(self.centers, "Solo Ciudad")
        assert c.location_text == "Ciudad Solo Demo"

    def test_coordinates_use_lon_key(self) -> None:
        c = _by_name(self.centers, "Demo Diáspora")
        assert c.coordinates == {"lat": 8.9714493, "lon": -79.5341802}

    def test_needs_mapped_to_keywords(self) -> None:
        # ["agua", "Alimentos no perecederos", "Medicamentos"]
        c = _by_name(self.centers, "Demo Diáspora")
        assert c.needs == ["agua", "alimentos", "medicamentos"]

    def test_needs_mixed_casing_and_phrases(self) -> None:
        # ["Ropa", "Higiene personal", "Pañales", "Frazadas"]
        c = _by_name(self.centers, "Refugio Demo Norte")
        assert c.needs == ["ropa", "higiene", "pañales", "colchonetas"]

    def test_needs_unknown_to_otro(self) -> None:
        # "Artículos de bebé" no encaja en el vocabulario controlado.
        c = _by_name(self.centers, "Solo Ciudad")
        assert c.needs == ["otro"]

    def test_nota_has_tipo_and_recibe_raw(self) -> None:
        c = _by_name(self.centers, "Demo Diáspora")
        assert c.nota is not None
        assert "[tipo:acopio]" in c.nota
        # El recibe crudo se conserva para no perder categorías reales.
        assert "Alimentos no perecederos" in c.nota

    def test_nota_has_upstream_id(self) -> None:
        c = _by_name(self.centers, "Demo Diáspora")
        assert c.nota is not None
        assert "id_origen: 892c4d85-3fc8-4700-9ac9-8369fb1019fc" in c.nota

    def test_nota_excludes_contacto(self) -> None:
        # contacto puede traer PII de contacto directo — nunca debe ir a nota.
        c = _by_name(self.centers, "Demo Diáspora")
        assert c.nota is not None
        assert "contacto" not in c.nota.lower()


# ---------------------------------------------------------------------------
# Tests: estado → status
# ---------------------------------------------------------------------------

class TestStatusMapping:
    def test_abierto_is_active(self) -> None:
        assert _map_status("abierto") == "active"

    def test_lleno_is_full(self) -> None:
        assert _map_status("lleno") == "full"

    def test_cerrado_is_closed(self) -> None:
        assert _map_status("cerrado") == "closed"

    def test_unknown_is_unverified(self) -> None:
        assert _map_status("cualquier_cosa") == "unverified"

    def test_none_is_unverified(self) -> None:
        assert _map_status(None) == "unverified"

    def test_case_and_accent_insensitive(self) -> None:
        assert _map_status("  ABIERTO ") == "active"


# ---------------------------------------------------------------------------
# Tests: recibe → needs
# ---------------------------------------------------------------------------

class TestNeedsMapping:
    def test_empty_list(self) -> None:
        assert _normalize_needs([]) == []

    def test_none(self) -> None:
        assert _normalize_needs(None) == []

    def test_real_categories_with_mixed_casing(self) -> None:
        # Valores reales observados en la API.
        recibe = ["Agua", "Alimentos no perecederos", "Higiene personal", "Pañales"]
        assert _normalize_needs(recibe) == ["agua", "alimentos", "higiene", "pañales"]

    def test_comma_separated_string(self) -> None:
        # El doc de la API menciona la forma "agua,alimentos".
        assert _normalize_needs("agua,alimentos") == ["agua", "alimentos"]

    def test_dedup_preserves_order(self) -> None:
        assert _normalize_needs(["Agua", "agua", "Medicamentos"]) == ["agua", "medicamentos"]

    def test_frazadas_maps_to_colchonetas(self) -> None:
        assert _normalize_needs(["Frazadas"]) == ["colchonetas"]

    def test_medical_supplies_map_to_medicamentos(self) -> None:
        # Insumos médicos reales se agrupan al keyword de salud más cercano.
        recibe = ["Gasas", "Insumos médicos", "Primeros Auxilios", "Mascarillas"]
        assert _normalize_needs(recibe) == ["medicamentos"]

    def test_hygiene_products_map_to_higiene(self) -> None:
        recibe = ["Jabón", "Alcohol en gel", "Toallas húmedas"]
        assert _normalize_needs(recibe) == ["higiene"]

    def test_unknown_maps_to_otro(self) -> None:
        # "Artículos de bebé" no tiene keyword en el vocabulario controlado.
        assert _normalize_needs(["Artículos de bebé"]) == ["otro"]


# ---------------------------------------------------------------------------
# Tests: coordinates
# ---------------------------------------------------------------------------

class TestCoordinates:
    def test_valid(self) -> None:
        assert _coordinates(10.5, -66.9) == {"lat": 10.5, "lon": -66.9}

    def test_missing_returns_none(self) -> None:
        assert _coordinates(None, -66.9) is None
        assert _coordinates(10.5, None) is None

    def test_non_numeric_returns_none(self) -> None:
        assert _coordinates("x", "y") is None

    def test_out_of_range_returns_none(self) -> None:
        assert _coordinates(999.0, 0.0) is None
        assert _coordinates(0.0, 999.0) is None

    def test_nan_and_inf_return_none(self) -> None:
        # NaN/Inf no lanzan en float(), pero toda comparación de orden con NaN
        # da False (IEEE 754), así que `not -90 <= nan <= 90` es True y el
        # chequeo de rango los descarta: nunca se cuelan como coordenadas.
        nan = float("nan")
        inf = float("inf")
        assert _coordinates(nan, -66.0) is None
        assert _coordinates(10.0, inf) is None
        assert _coordinates(nan, inf) is None
        assert _coordinates(-inf, -66.0) is None


# ---------------------------------------------------------------------------
# Tests: Robustez
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_record_without_name_is_skipped(self) -> None:
        raw = _make_raw([{"ciudad": "Caracas", "pais": "Venezuela", "lat": 10.0, "lng": -69.0}])
        assert _parser().parse(raw) == []

    def test_record_without_location_is_skipped(self) -> None:
        # Sin ciudad, pais ni address → no se puede construir location_text.
        raw = _make_raw([{"name": "Centro Demo Sin Ubicacion"}])
        assert _parser().parse(raw) == []

    def test_address_fallback_when_no_ciudad_pais(self) -> None:
        raw = _make_raw([{"name": "Centro Demo Address", "address": "Calle Demo 500"}])
        centers = _parser().parse(raw)
        assert len(centers) == 1
        assert centers[0].location_text == "Calle Demo 500"

    def test_bad_coordinates_keep_record(self) -> None:
        raw = _make_raw([{
            "name": "Centro Demo Coords Malas",
            "ciudad": "Caracas",
            "pais": "Venezuela",
            "lat": 999.0,
            "lng": -69.0,
        }])
        centers = _parser().parse(raw)
        assert len(centers) == 1
        assert centers[0].coordinates is None

    def test_one_bad_record_does_not_break_others(self) -> None:
        raw = _make_raw([
            {"name": None, "ciudad": "Caracas"},                       # se omite
            {"name": "Centro Demo Valido", "ciudad": "Valencia"},      # válido
        ])
        centers = _parser().parse(raw)
        assert len(centers) == 1
        assert centers[0].name == "Centro Demo Valido"

    def test_malformed_raw_content_returns_empty(self) -> None:
        assert _parser().parse(_make_raw("no soy un dict ni una lista")) == []
        assert _parser().parse(_make_raw(None)) == []


# ---------------------------------------------------------------------------
# Tests: Formas del payload ({"data": [...]} / lista / {id: centro})
# ---------------------------------------------------------------------------

class TestPayloadShapes:
    _CENTER = {
        "name": "Centro Demo Forma",
        "tipo": "acopio",
        "estado": "abierto",
        "ciudad": "Ciudad Demo",
        "pais": "Panamá",
        "lat": 8.97,
        "lng": -79.53,
        "recibe": ["agua"],
        "updated_at": "2026-06-29T06:00:00Z",
    }

    def test_data_wrapper_form(self) -> None:
        # Forma real de /centros.
        centers = _parser().parse(_make_raw({"data": [self._CENTER]}))
        assert len(centers) == 1

    def test_list_form(self) -> None:
        centers = _parser().parse(_make_raw([self._CENTER]))
        assert len(centers) == 1

    def test_id_keyed_dict_form(self) -> None:
        centers = _parser().parse(_make_raw({"abc123": self._CENTER}))
        assert len(centers) == 1
