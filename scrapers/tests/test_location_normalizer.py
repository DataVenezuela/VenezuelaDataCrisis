from __future__ import annotations

import requests

from scrapers.normalizers import normalize_location
from scrapers.normalizers.location import geocode_osm


def test_location_normalizer_returns_schema_shape_offline() -> None:
    result = normalize_location("  Dtto.   Capital  ")

    assert result == {
        "raw": "Dtto. Capital",
        "estado": "Distrito Capital",
        "municipio": "Libertador",
        "parroquia": None,
        "lat": None,
        "lng": None,
    }


def test_location_normalizer_covers_state_variations() -> None:
    cases = [
        ("Distrito Capital", "Distrito Capital"),
        ("Dtto. Capital", "Distrito Capital"),
        ("Edo. Miranda", "Miranda"),
        ("Estado Zulia", "Zulia"),
        ("Nueva Esparta", "Nueva Esparta"),
        ("Vargas", "La Guaira"),
    ]

    for raw, expected_state in cases:
        assert normalize_location(raw)["estado"] == expected_state


def test_location_normalizer_detects_common_municipalities() -> None:
    cases = [
        ("Mun. San Felipe, Edo. Yaracuy", "Yaracuy", "San Felipe"),
        ("Maracaibo, Estado Zulia", "Zulia", "Maracaibo"),
        ("Barquisimeto, Lara", "Lara", "Iribarren"),
        ("Municipio Sucre, Miranda", "Miranda", "Sucre"),
    ]

    for raw, expected_state, expected_municipality in cases:
        result = normalize_location(raw)
        assert result["estado"] == expected_state
        assert result["municipio"] == expected_municipality


def test_location_normalizer_can_infer_state_from_unique_municipality() -> None:
    result = normalize_location("Municipio San Felipe")

    assert result["estado"] == "Yaracuy"
    assert result["municipio"] == "San Felipe"


def test_location_normalizer_handles_empty_input_without_geocoding() -> None:
    result = normalize_location(None, geocode=True)

    assert result == {
        "raw": None,
        "estado": None,
        "municipio": None,
        "parroquia": None,
        "lat": None,
        "lng": None,
    }


def test_location_normalizer_does_not_call_geocoder_by_default() -> None:
    def unexpected_geocoder(_query: str, _timeout: float, _user_agent: str) -> tuple[float, float]:
        raise AssertionError("geocoder should not be called when geocode=False")

    result = normalize_location("Distrito Capital", geocoder=unexpected_geocoder)

    assert result["estado"] == "Distrito Capital"
    assert result["lat"] is None
    assert result["lng"] is None


def test_location_normalizer_uses_optional_geocoder() -> None:
    calls: list[tuple[str, float, str]] = []

    def fake_geocoder(query: str, timeout: float, user_agent: str) -> tuple[float, float]:
        calls.append((query, timeout, user_agent))
        return 10.5, -66.9

    result = normalize_location(
        "Distrito Capital",
        geocode=True,
        timeout=2.5,
        user_agent="test-agent",
        geocoder=fake_geocoder,
    )

    assert result["lat"] == 10.5
    assert result["lng"] == -66.9
    assert calls == [("Distrito Capital, Venezuela", 2.5, "test-agent")]


def test_location_normalizer_keeps_coordinates_null_when_geocoder_fails() -> None:
    def failing_geocoder(_query: str, _timeout: float, _user_agent: str) -> tuple[float, float]:
        raise requests.Timeout("demo timeout")

    result = normalize_location("Estado Miranda", geocode=True, geocoder=failing_geocoder)

    assert result["estado"] == "Miranda"
    assert result["lat"] is None
    assert result["lng"] is None


def test_geocode_osm_uses_nominatim_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, str]]:
            return [{"lat": "10.5", "lon": "-66.9"}]

    def fake_get(url: str, *, params: dict[str, object], headers: dict[str, str], timeout: float) -> FakeResponse:
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)

    assert geocode_osm("Ciudad Demo, Venezuela", timeout=3.0, user_agent="test-agent") == (10.5, -66.9)
    assert captured["params"] == {"q": "Ciudad Demo, Venezuela", "format": "json", "limit": 1}
    assert captured["headers"] == {"User-Agent": "test-agent"}
    assert captured["timeout"] == 3.0


def test_geocode_osm_returns_none_on_empty_result(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, str]]:
            return []

    def fake_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)

    assert geocode_osm("Ciudad Demo") is None


def test_geocode_osm_returns_none_on_request_error(monkeypatch) -> None:
    def fake_get(*_args: object, **_kwargs: object) -> object:
        raise requests.Timeout("demo timeout")

    monkeypatch.setattr(requests, "get", fake_get)

    assert geocode_osm("Ciudad Demo") is None
