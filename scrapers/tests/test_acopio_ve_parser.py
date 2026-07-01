"""
Tests del AcopioVeParser con datos 100% sinteticos.

No se realiza ninguna llamada de red. El fixture reproduce el contrato
Firebase RTDB de AcopioVE: ``{firebase_key: centro}``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.models import AcopioCenter
from scrapers.parsers.acopio_ve_parser import (
    FUENTE_LABEL,
    SOURCE_KEY,
    AcopioVeParser,
    _coordinates,
    _map_status,
    _normalize_needs,
)
from scrapers.parsers.base import ParserProtocol

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "acopio_ve_sample.json"
_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_raw(payload: Any) -> RawContent:
    return RawContent(
        source_key=SOURCE_KEY,
        source_url="https://acopio-ve-2026-default-rtdb.firebaseio.com/centros.json",
        fetched_at="2026-06-30T12:00:00Z",
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
    return next(c for c in centers if needle.lower() in c.name.lower())


class TestParserProtocol:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(_parser(), ParserProtocol)

    def test_source_key_attribute(self) -> None:
        assert _parser().source_key == SOURCE_KEY


class TestFirebaseContract:
    def setup_method(self) -> None:
        self.centers = _parser().parse(_make_raw(_load_fixture()))

    def test_valid_records_are_parsed(self) -> None:
        assert len(self.centers) == 3
        assert all(isinstance(c, AcopioCenter) for c in self.centers)

    def test_common_fields(self) -> None:
        assert all(c.event_id == _EVENT_ID for c in self.centers)
        assert all(c.fuente == FUENTE_LABEL for c in self.centers)
        assert all(c.trust_tier == "C" for c in self.centers)

    def test_nombre_maps_to_name(self) -> None:
        c = _by_name(self.centers, "Caracas")
        assert c.name == "Centro de Acopio Demo Caracas"

    def test_municipio_estado_maps_to_location_text(self) -> None:
        c = _by_name(self.centers, "Caracas")
        assert c.location_text == "Libertador, Distrito Capital"

    def test_direccion_fallback_for_location_text(self) -> None:
        centers = _parser().parse(_make_raw([{
            "nombre": "Centro Demo Direccion",
            "direccion": "Avenida Sintetica 123",
            "capacidad": "disponible",
        }]))
        assert len(centers) == 1
        assert centers[0].location_text == "Avenida Sintetica 123"

    def test_lat_lng_maps_to_coordinates(self) -> None:
        c = _by_name(self.centers, "Caracas")
        assert c.coordinates == {"lat": 10.492, "lon": -66.942}

    def test_insumos_maps_to_needs(self) -> None:
        c = _by_name(self.centers, "Caracas")
        assert c.needs == ["agua", "alimentos", "medicamentos"]

    def test_capacidad_maps_to_status(self) -> None:
        assert _by_name(self.centers, "Caracas").status == "active"
        assert _by_name(self.centers, "Parcial").status == "active"
        assert _by_name(self.centers, "Lleno").status == "full"

    def test_id_origen_uses_firebase_key(self) -> None:
        c = _by_name(self.centers, "Caracas")
        assert c.nota is not None
        assert "id_origen: firebase_demo_1" in c.nota

    def test_id_origen_prefers_internal_id_when_present(self) -> None:
        centers = _parser().parse(_make_raw({
            "firebase_key_demo": {
                "id": "internal-upstream-id",
                "nombre": "Centro Demo ID Interno",
                "municipio": "Demo",
                "estado": "Demo Estado",
                "capacidad": "disponible",
            }
        }))
        assert len(centers) == 1
        assert centers[0].nota is not None
        assert "id_origen: internal-upstream-id" in centers[0].nota
        assert "firebase_key_demo" not in centers[0].nota

    def test_nota_keeps_trace_fields_without_direct_contact_data(self) -> None:
        c = _by_name(self.centers, "Caracas")
        assert c.nota is not None
        assert "capacidad: disponible" in c.nota
        assert "actualizado: 2026-06-30T00:00:00Z" in c.nota
        assert "fuente_origen: firebase" in c.nota
        assert "Persona Demo" not in c.nota
        assert "+58" not in c.nota


class TestStatusMapping:
    def test_capacidad_values(self) -> None:
        assert _map_status("disponible") == "active"
        assert _map_status("parcial") == "active"
        assert _map_status("lleno") == "full"
        assert _map_status("cerrado") == "closed"
        assert _map_status("desconocido") == "unverified"
        assert _map_status(None) == "unverified"

    def test_legacy_estado_values(self) -> None:
        assert _map_status("abierto", legacy=True) == "active"
        assert _map_status("lleno", legacy=True) == "full"
        assert _map_status("cerrado", legacy=True) == "closed"


class TestNeedsMapping:
    def test_empty_inputs(self) -> None:
        assert _normalize_needs([]) == []
        assert _normalize_needs(None) == []

    def test_firebase_insumos(self) -> None:
        raw = ["Agua potable", "Alimentos no perecederos", "Higiene personal", "Pañales"]
        assert _normalize_needs(raw) == ["agua", "alimentos", "higiene", "pañales"]

    def test_comma_separated_legacy_string(self) -> None:
        assert _normalize_needs("agua,alimentos") == ["agua", "alimentos"]

    def test_unknown_maps_to_otro(self) -> None:
        assert _normalize_needs(["Artículos de bebé"]) == ["otro"]


class TestCoordinates:
    def test_valid_numeric_and_string_values(self) -> None:
        assert _coordinates(10.5, -66.9) == {"lat": 10.5, "lon": -66.9}
        assert _coordinates("10.5", "-66.9") == {"lat": 10.5, "lon": -66.9}

    def test_missing_or_non_numeric_returns_none(self) -> None:
        assert _coordinates(None, -66.9) is None
        assert _coordinates(10.5, None) is None
        assert _coordinates("x", "y") is None

    def test_out_of_range_returns_none(self) -> None:
        assert _coordinates(999.0, 0.0) is None
        assert _coordinates(0.0, 999.0) is None

    def test_nan_and_inf_return_none(self) -> None:
        assert _coordinates(float("nan"), -66.0) is None
        assert _coordinates(10.0, float("inf")) is None
        assert _coordinates(float("-inf"), -66.0) is None


class TestPayloadShapesAndFallbacks:
    _FIREBASE_CENTER = {
        "nombre": "Centro Demo Forma",
        "municipio": "Municipio Demo",
        "estado": "Estado Demo",
        "capacidad": "disponible",
        "insumos": ["agua"],
        "actualizadoEn": "2026-06-30T06:00:00Z",
    }

    _LEGACY_CENTER = {
        "id": "legacy-id-1",
        "name": "Centro Demo Legacy",
        "estado": "abierto",
        "ciudad": "Ciudad Demo",
        "pais": "Pais Demo",
        "lat": 8.97,
        "lng": -79.53,
        "recibe": ["agua"],
        "updated_at": "2026-06-29T06:00:00Z",
    }

    def test_firebase_keyed_dict_form(self) -> None:
        centers = _parser().parse(_make_raw({"firebase_id_1": self._FIREBASE_CENTER}))
        assert len(centers) == 1
        assert centers[0].nota is not None
        assert "id_origen: firebase_id_1" in centers[0].nota

    def test_list_form(self) -> None:
        centers = _parser().parse(_make_raw([self._FIREBASE_CENTER]))
        assert len(centers) == 1

    def test_data_wrapper_form(self) -> None:
        centers = _parser().parse(_make_raw({"data": [self._FIREBASE_CENTER]}))
        assert len(centers) == 1

    def test_legacy_api_fields_still_work_as_fallback(self) -> None:
        centers = _parser().parse(_make_raw({"data": [self._LEGACY_CENTER]}))
        assert len(centers) == 1
        center = centers[0]
        assert center.name == "Centro Demo Legacy"
        assert center.location_text == "Ciudad Demo, Pais Demo"
        assert center.status == "active"
        assert center.needs == ["agua"]
        assert center.nota is not None
        assert "id_origen: legacy-id-1" in center.nota

    def test_record_without_name_is_skipped(self) -> None:
        raw = _make_raw([{"municipio": "Caracas", "estado": "Distrito Capital"}])
        assert _parser().parse(raw) == []

    def test_record_without_location_is_skipped(self) -> None:
        raw = _make_raw([{"nombre": "Centro Demo Sin Ubicacion"}])
        assert _parser().parse(raw) == []

    def test_one_bad_record_does_not_break_batch(self) -> None:
        raw = _make_raw([
            {"nombre": None, "municipio": "Caracas"},
            self._FIREBASE_CENTER,
        ])
        centers = _parser().parse(raw)
        assert len(centers) == 1
        assert centers[0].name == "Centro Demo Forma"

    def test_malformed_raw_content_returns_empty(self) -> None:
        assert _parser().parse(_make_raw("no soy un dict ni una lista")) == []
        assert _parser().parse(_make_raw(None)) == []


class TestPrivacyLogging:
    def test_direct_contact_data_is_not_logged_or_stored(self, caplog: Any) -> None:
        payload = {
            "firebase_demo_sensitive": {
                "nombre": "Centro Demo Privacidad",
                "municipio": "Municipio Demo",
                "estado": "Estado Demo",
                "capacidad": "disponible",
                "contacto": "Persona Demo Privada +58 000-0000000",
            }
        }
        with caplog.at_level(logging.WARNING):
            centers = _parser().parse(_make_raw(payload))

        assert len(centers) == 1
        assert centers[0].nota is not None
        assert "Persona Demo Privada" not in centers[0].nota
        assert "+58" not in centers[0].nota
        assert "Persona Demo Privada" not in caplog.text
        assert "+58" not in caplog.text
