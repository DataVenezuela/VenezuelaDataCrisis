"""
scrapers/tests/test_acopio_ve_parser.py
========================================
Tests del AcopioVeParser con fixture sintético (issue #99).

No se realiza ninguna llamada de red. El fixture vive en
``scrapers/tests/fixtures/acopio_ve_sample.json`` y reproduce la
estructura del endpoint Firebase RTDB (campos reales, datos 100% ficticios).

Cobertura
---------
- Mapeo de todos los campos de la fuente a AcopioCenter
- capacidad → status (disponible/parcial → active, lleno → full, ausente → unverified)
- insumos (texto libre) → keywords controlados; desconocido → "otro"; sin duplicados
- coordinates: lat/lng → {"lat", "lon"}; inválidas → None sin descartar el registro
- location_text desde estado/municipio (normalize_location)
- nota con capacidad cruda + fecha de actualización (trazabilidad)
- Registro sin nombre → omitido; registro sin ubicación → omitido
- Tolerancia: un registro malo no rompe el resto; raw_content malformado
- Formas de payload: lista, {"data": [...]} y {push_id: centro} (Firebase)
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
        source_url="https://acopio-ve-2026-default-rtdb.firebaseio.com/centros.json",
        fetched_at="2026-06-28T12:00:00Z",
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
        c = _by_name(self.centers, "Demo Caracas")
        # normalize_text no fuerza Title Case: el nombre se conserva.
        assert c.name == "Centro de Acopio Demo Caracas"

    def test_location_text_municipio_estado(self) -> None:
        c = _by_name(self.centers, "Demo Caracas")
        assert c.location_text == "Libertador, Distrito Capital"

    def test_location_text_only_estado(self) -> None:
        c = _by_name(self.centers, "Demo Lara")
        assert c.location_text == "Lara"

    def test_coordinates_use_lon_key(self) -> None:
        c = _by_name(self.centers, "Demo Caracas")
        assert c.coordinates == {"lat": 10.492, "lon": -66.942}

    def test_needs_mapped_to_keywords(self) -> None:
        c = _by_name(self.centers, "Demo Caracas")
        assert c.needs == ["agua", "alimentos", "medicamentos"]

    def test_needs_panales_and_leche(self) -> None:
        c = _by_name(self.centers, "San Felipe")
        assert c.needs == ["colchonetas", "pañales", "leche_formula"]

    def test_needs_unknown_to_otro(self) -> None:
        c = _by_name(self.centers, "Demo Zulia")
        assert c.needs == ["otro"]

    def test_nota_has_capacidad_and_date(self) -> None:
        c = _by_name(self.centers, "Demo Caracas")
        assert c.nota is not None
        assert "[capacidad:disponible]" in c.nota
        assert "actualizado_en=" in c.nota

    def test_nota_none_when_no_metadata(self) -> None:
        # Demo Zulia: capacidad null + actualizadoEn null → nota None
        c = _by_name(self.centers, "Demo Zulia")
        assert c.nota is None


# ---------------------------------------------------------------------------
# Tests: capacidad → status
# ---------------------------------------------------------------------------

class TestStatusMapping:
    def test_disponible_is_active(self) -> None:
        assert _map_status("disponible") == "active"

    def test_parcial_is_active(self) -> None:
        assert _map_status("parcial") == "active"

    def test_lleno_is_full(self) -> None:
        assert _map_status("lleno") == "full"

    def test_unknown_is_unverified(self) -> None:
        assert _map_status("cualquier_cosa") == "unverified"

    def test_none_is_unverified(self) -> None:
        assert _map_status(None) == "unverified"

    def test_case_and_accent_insensitive(self) -> None:
        assert _map_status("  DISPONIBLE ") == "active"


# ---------------------------------------------------------------------------
# Tests: insumos → needs
# ---------------------------------------------------------------------------

class TestNeedsMapping:
    def test_empty_list(self) -> None:
        assert _normalize_needs([]) == []

    def test_none(self) -> None:
        assert _normalize_needs(None) == []

    def test_string_input_coerced(self) -> None:
        assert _normalize_needs("Agua potable") == ["agua"]

    def test_dedup_preserves_order(self) -> None:
        assert _normalize_needs(["Agua", "agua potable", "Medicinas"]) == ["agua", "medicamentos"]

    def test_unknown_maps_to_otro(self) -> None:
        assert _normalize_needs(["objeto raro sin categoria"]) == ["otro"]


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


# ---------------------------------------------------------------------------
# Tests: Robustez
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_record_without_name_is_skipped(self) -> None:
        raw = _make_raw([{"estado": "Lara", "lat": 10.0, "lng": -69.0}])
        assert _parser().parse(raw) == []

    def test_record_without_location_is_skipped(self) -> None:
        raw = _make_raw([{"nombre": "Centro Demo Sin Ubicacion"}])
        assert _parser().parse(raw) == []

    def test_bad_coordinates_keep_record(self) -> None:
        raw = _make_raw([{
            "nombre": "Centro Demo Coords Malas",
            "estado": "Lara",
            "lat": 999.0,
            "lng": -69.0,
        }])
        centers = _parser().parse(raw)
        assert len(centers) == 1
        assert centers[0].coordinates is None

    def test_one_bad_record_does_not_break_others(self) -> None:
        raw = _make_raw([
            {"nombre": None, "estado": "Lara"},                  # se omite
            {"nombre": "Centro Demo Valido", "estado": "Zulia"},  # válido
        ])
        centers = _parser().parse(raw)
        assert len(centers) == 1
        assert centers[0].name == "Centro Demo Valido"

    def test_malformed_raw_content_returns_empty(self) -> None:
        assert _parser().parse(_make_raw("no soy un dict ni una lista")) == []
        assert _parser().parse(_make_raw(None)) == []


# ---------------------------------------------------------------------------
# Tests: Formas del payload (lista / data-wrapper / Firebase dict)
# ---------------------------------------------------------------------------

class TestPayloadShapes:
    _CENTER = {
        "nombre": "Centro Demo Forma",
        "estado": "Yaracuy",
        "municipio": "San Felipe",
        "lat": 10.34,
        "lng": -68.74,
        "capacidad": "disponible",
        "insumos": ["Agua"],
        "actualizadoEn": 1751000000000,
    }

    def test_list_form(self) -> None:
        centers = _parser().parse(_make_raw([self._CENTER]))
        assert len(centers) == 1

    def test_data_wrapper_form(self) -> None:
        centers = _parser().parse(_make_raw({"data": [self._CENTER]}))
        assert len(centers) == 1

    def test_firebase_dict_form(self) -> None:
        centers = _parser().parse(_make_raw({"-Npush123": self._CENTER}))
        assert len(centers) == 1
