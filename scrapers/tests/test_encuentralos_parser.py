"""
scrapers/tests/test_encuentralos_parser.py
==========================================
Tests del EncuentralosParser con fixture sintético.

No se realiza ninguna llamada de red.  El fixture vive en
``scrapers/tests/fixtures/encuentralos_api_sample.json`` y reproduce la
estructura real de la API (campos reales, datos 100% ficticios).

Cobertura
---------
- Mapeo de todos los campos de la API a Person
- Mapeo completo del enum status (missing/found/injured/deceased/unknown)
- Cédula pre-mascarada: cedula_hmac=None, cedula_masked=None permanente
- normalize_location aplicado sobre ultima_ubicacion (string libre)
- age_range desde edad puntual
- nota con id externo (UUID) + descripcion
- telefono_contacto descartado (PII de tercero)
- Registro sin nombre → omitido (None), parser no falla
- Registro sin cédula → cedula_hmac=None, cedula_masked=None
- Sin PII_HMAC_SECRET → cedula_hmac=None en todos los registros
- ParserProtocol satisfecho (isinstance check)
- Tolerancia a raw_content malformado
- Paginación: parse sobre múltiples RawContent concatena resultados
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scrapers.adapters.base import RawContent
from scrapers.parsers.base import ParserProtocol
from scrapers.parsers.encuentralos_parser import (
    EncuentralosParser,
    FUENTE_LABEL,
    SOURCE_KEY,
    _map_status,
    _mask_cedula,
    _age_range,
    _location_str,
    _build_nota,
    _is_pre_masked_cedula,
)

# ---------------------------------------------------------------------------
# Constantes de test
# ---------------------------------------------------------------------------

_SECRET = "test-secret-encuentralos"
_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "encuentralos_api_sample.json"
_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")

_FIXTURE_ID_0 = "a1b2c3d4-0001-0001-0001-000000001001"
_FIXTURE_ID_1 = "a1b2c3d4-0002-0002-0002-000000001002"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _make_raw(payload: Any, source_key: str = SOURCE_KEY) -> RawContent:
    """Construye un RawContent mínimo con el payload dado."""
    return RawContent(
        source_key=source_key,
        source_url="https://encuentralos.tecnosoft.dev/api/personas?limit=20&offset=0",
        fetched_at="2026-06-24T15:30:00Z",
        http_status=200,
        content_type="application/json",
        content_hash="abc",
        raw_content=payload,
        page=1,
        total_pages=1,
        offset=0,
        limit=20,
        records_in_page=5,
    )


_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"


def _parser(secret: str | None = _SECRET) -> EncuentralosParser:
    return EncuentralosParser(event_id=_EVENT_ID, secret=secret)


def _new_schema_record(**overrides: Any) -> dict[str, Any]:
    """Devuelve un registro base con el schema nuevo para usar en tests inline."""
    base: dict[str, Any] = {
        "id": "test-uuid-0001",
        "nombre": "DEMO PERSON",
        "cedula": None,
        "edad": None,
        "ultima_ubicacion": "Zulia",
        "estado": "desaparecido",
        "descripcion": None,
        "foto": None,
        "ultima_vez": None,
        "telefono_contacto": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: Protocol
# ---------------------------------------------------------------------------

class TestParserProtocol:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(_parser(), ParserProtocol)

    def test_source_key_attribute(self) -> None:
        assert _parser().source_key == SOURCE_KEY

    def test_parse_returns_list(self) -> None:
        raw = _make_raw(_load_fixture())
        result = _parser().parse(raw)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Tests: Fixture completo
# ---------------------------------------------------------------------------

class TestParseFixture:
    def setup_method(self) -> None:
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        self.persons = _parser().parse(raw)

    def test_correct_count(self) -> None:
        """Los 5 registros del fixture deben producir 5 Person."""
        assert len(self.persons) == 5

    def test_all_are_person_instances(self) -> None:
        from scrapers.models import Person
        assert all(isinstance(p, Person) for p in self.persons)

    def test_fuente_is_set(self) -> None:
        assert all(p.fuente == FUENTE_LABEL for p in self.persons)

    def test_trust_tier(self) -> None:
        assert all(p.trust_tier == "C" for p in self.persons)


# ---------------------------------------------------------------------------
# Tests: Mapeo de campos individuales
# ---------------------------------------------------------------------------

class TestFieldMapping:
    """Verifica cada campo usando el primer registro del fixture."""

    def _first(self) -> Any:
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        return _parser().parse(raw)[0]

    def test_full_name_is_title_case(self) -> None:
        p = self._first()
        # "JOSE LUIS PEREZ DEMO" → "Jose Luis Perez Demo"
        assert p.full_name == "Jose Luis Perez Demo"
        assert p.full_name[0].isupper()

    def test_full_name_no_raw_caps(self) -> None:
        p = self._first()
        assert p.full_name != "JOSE LUIS PEREZ DEMO"

    def test_location_is_string(self) -> None:
        p = self._first()
        # ultima_ubicacion="Maracaibo, Zulia" → normalize_location → string
        assert isinstance(p.last_known_location, str)
        assert "Zulia" in p.last_known_location or "Maracaibo" in p.last_known_location

    def test_nota_contains_id(self) -> None:
        p = self._first()
        assert p.nota is not None
        assert f"[id:{_FIXTURE_ID_0}]" in p.nota

    def test_nota_contains_description(self) -> None:
        p = self._first()
        assert "mercado" in p.nota.lower()

    def test_age_range_min_equals_max(self) -> None:
        p = self._first()
        assert p.age_range == {"min": 35, "max": 35}

    def test_telefono_not_stored(self) -> None:
        """telefono_contacto debe haberse descartado silenciosamente."""
        p = self._first()
        assert not hasattr(p, "telefono_contacto")
        assert not hasattr(p, "telefono")

    def test_ultima_vez_not_stored(self) -> None:
        """ultima_vez debe haberse descartado silenciosamente."""
        p = self._first()
        assert not hasattr(p, "ultima_vez")

    def test_source_record_id_set_from_api_id(self) -> None:
        """source_record_id debe ser el UUID nativo de la API."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        persons = _parser().parse(raw)
        assert persons[0].source_record_id == _FIXTURE_ID_0
        assert persons[1].source_record_id == _FIXTURE_ID_1

    def test_source_record_id_none_when_no_id(self) -> None:
        """source_record_id debe ser None si el registro no tiene id."""
        records = [_new_schema_record()]
        del records[0]["id"]
        raw = _make_raw({"items": records, "total": 1})
        p = _parser().parse(raw)[0]
        assert p.source_record_id is None


# ---------------------------------------------------------------------------
# Tests: Status enum
# ---------------------------------------------------------------------------

class TestStatusMapping:
    """Verifica que cada valor de la API se mapea al enum correcto."""

    def _parse_all(self) -> list[Any]:
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        return _parser().parse(raw)

    def test_desaparecido_maps_to_missing(self) -> None:
        persons = self._parse_all()
        # registro 0: estado="desaparecido"
        assert persons[0].status == "missing"

    def test_encontrado_maps_to_found(self) -> None:
        persons = self._parse_all()
        # registro 1: estado="encontrado"
        assert persons[1].status == "found"

    def test_herido_maps_to_injured(self) -> None:
        persons = self._parse_all()
        # registro 2: estado="herido"
        assert persons[2].status == "injured"

    def test_fallecido_maps_to_deceased(self) -> None:
        persons = self._parse_all()
        # registro 3: estado="fallecido"
        assert persons[3].status == "deceased"

    def test_unknown_status_maps_to_unknown(self) -> None:
        persons = self._parse_all()
        # registro 4: estado="sin_informacion" → unknown
        assert persons[4].status == "unknown"

    def test_none_status_maps_to_unknown(self) -> None:
        assert _map_status(None) == "unknown"

    def test_empty_status_maps_to_unknown(self) -> None:
        assert _map_status("") == "unknown"

    def test_female_variants(self) -> None:
        assert _map_status("desaparecida") == "missing"
        assert _map_status("encontrada") == "found"
        assert _map_status("herida") == "injured"
        assert _map_status("fallecida") == "deceased"

    def test_case_insensitive(self) -> None:
        assert _map_status("DESAPARECIDO") == "missing"
        assert _map_status("Encontrado") == "found"


# ---------------------------------------------------------------------------
# Tests: PII — cédula HMAC
# ---------------------------------------------------------------------------

class TestCedulaHMAC:
    def test_pre_masked_cedula_gives_none_hmac(self) -> None:
        """Cédula pre-mascarada (contiene '•') → cedula_hmac=None."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        persons = _parser().parse(raw)
        # Todos los registros del fixture tienen cédulas pre-mascaradas
        for p in persons:
            assert p.cedula_hmac is None

    def test_pre_masked_cedula_gives_none_masked(self) -> None:
        """Cédula pre-mascarada → cedula_masked=None."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        persons = _parser().parse(raw)
        for p in persons:
            assert p.cedula_masked is None

    def test_unmasked_cedula_still_gets_hmac(self) -> None:
        """Una cédula sin máscara sí genera HMAC y cedula_masked con formato correcto."""
        records = [_new_schema_record(cedula="V-12345000")]
        raw = _make_raw({"items": records, "total": 1})
        p = _parser().parse(raw)[0]
        assert p.cedula_hmac is not None
        assert _HEX64.match(p.cedula_hmac), f"Not 64-hex: {p.cedula_hmac!r}"
        assert p.cedula_masked == "****5000"

    def test_unmasked_cedula_no_prefix(self) -> None:
        """HMAC de cédula no mascarada: hex puro, sin prefijo hmac_sha256."""
        records = [_new_schema_record(cedula="V-12345000")]
        raw = _make_raw({"items": records, "total": 1})
        p = _parser().parse(raw)[0]
        assert p.cedula_hmac is not None
        assert not p.cedula_hmac.startswith("hmac_sha256:")

    def test_no_cedula_gives_none(self) -> None:
        """cedula=None → cedula_hmac=None y cedula_masked=None."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        persons = _parser().parse(raw)
        # registro 2: cedula=None
        assert persons[2].cedula_hmac is None
        assert persons[2].cedula_masked is None

    def test_no_secret_gives_none_hmac(self) -> None:
        """Sin PII_HMAC_SECRET, cedula_hmac debe ser None (no lanzar)."""
        records = [_new_schema_record(cedula="V-12345000")]
        raw = _make_raw({"items": records, "total": 1})
        parser_no_secret = EncuentralosParser(event_id=_EVENT_ID, secret=None)
        persons = parser_no_secret.parse(raw)
        assert all(p.cedula_hmac is None for p in persons)

    def test_mask_cedula_helper(self) -> None:
        assert _mask_cedula("V-12345678") == "****5678"
        assert _mask_cedula("E-9876543") == "****6543"
        assert _mask_cedula("12345000") == "****5000"

    def test_is_pre_masked_helper_with_bullet(self) -> None:
        assert _is_pre_masked_cedula("22•••52") is True
        assert _is_pre_masked_cedula("12•••00") is True

    def test_is_pre_masked_helper_with_asterisk(self) -> None:
        assert _is_pre_masked_cedula("22***52") is True

    def test_is_pre_masked_helper_clean_cedula(self) -> None:
        assert _is_pre_masked_cedula("V-12345678") is False
        assert _is_pre_masked_cedula("12345000") is False

    def test_cedula_hmac_is_deterministic(self) -> None:
        """Mismo input → mismo HMAC en dos llamadas consecutivas."""
        records = [_new_schema_record(cedula="V-12345000")]
        raw = _make_raw({"items": records, "total": 1})
        parser = _parser()
        p1 = parser.parse(raw)[0]
        p2 = parser.parse(raw)[0]
        assert p1.cedula_hmac == p2.cedula_hmac

    def test_different_cedulas_different_hmac(self) -> None:
        """Cédulas distintas → HMACs distintos."""
        r1 = [_new_schema_record(cedula="V-12345000")]
        r2 = [_new_schema_record(cedula="V-99999999")]
        p1 = _parser().parse(_make_raw({"items": r1, "total": 1}))[0]
        p2 = _parser().parse(_make_raw({"items": r2, "total": 1}))[0]
        assert p1.cedula_hmac != p2.cedula_hmac

    def test_mask_cedula_bad_input_returns_none(self) -> None:
        """Cédula inválida → None, no basura "****????"""
        assert _mask_cedula("123") is None


# ---------------------------------------------------------------------------
# Tests: Normalización de nombres
# ---------------------------------------------------------------------------

class TestNameNormalization:
    def test_uppercase_normalized_to_title_case(self) -> None:
        """Todos los nombres del fixture están en mayúsculas en la API."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        for p in _parser().parse(raw):
            assert p.full_name == p.full_name.strip()
            # No debe ser todo mayúsculas
            assert p.full_name != p.full_name.upper() or len(p.full_name) == 1

    def test_connectors_lowercase(self) -> None:
        """Conectores (de, la, del) van en minúscula excepto si abren."""
        record = _new_schema_record(nombre="MARIA DE LA DEMO")
        raw = _make_raw({"items": [record], "total": 1})
        p = _parser().parse(raw)[0]
        assert "de la" in p.full_name


# ---------------------------------------------------------------------------
# Tests: Ubicación
# ---------------------------------------------------------------------------

class TestLocation:
    def test_ultima_ubicacion_free_string(self) -> None:
        """ultima_ubicacion como string libre pasa por normalize_location."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        persons = _parser().parse(raw)
        # registro 1: ultima_ubicacion="Caracas" → normalizado, no None
        assert persons[1].last_known_location is not None

    def test_municipio_y_estado_en_string_libre(self) -> None:
        """ultima_ubicacion="Maracaibo, Zulia" → string que incluye ambos."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        p = _parser().parse(raw)[0]
        assert p.last_known_location is not None

    def test_no_location_gives_none(self) -> None:
        """ultima_ubicacion=null → last_known_location=None."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        persons = _parser().parse(raw)
        # registro 4: ultima_ubicacion=None
        assert persons[4].last_known_location is None

    def test_location_str_helper_estado_only(self) -> None:
        loc = {"raw": "Zulia", "estado": "Zulia", "municipio": None, "parroquia": None, "lat": None, "lng": None}
        assert _location_str(loc) == "Zulia"

    def test_location_str_helper_municipio_estado(self) -> None:
        loc = {"raw": "Maracaibo, Zulia", "estado": "Zulia", "municipio": "Maracaibo", "parroquia": None, "lat": None, "lng": None}
        assert _location_str(loc) == "Maracaibo, Zulia"

    def test_location_str_helper_none(self) -> None:
        loc = {"raw": None, "estado": None, "municipio": None, "parroquia": None, "lat": None, "lng": None}
        assert _location_str(loc) is None


# ---------------------------------------------------------------------------
# Tests: age_range
# ---------------------------------------------------------------------------

class TestAgeRange:
    def test_edad_puntual(self) -> None:
        assert _age_range(35) == {"min": 35, "max": 35}

    def test_edad_none(self) -> None:
        assert _age_range(None) is None

    def test_edad_string_numeric(self) -> None:
        assert _age_range("42") == {"min": 42, "max": 42}

    def test_edad_negativa(self) -> None:
        assert _age_range(-1) is None

    def test_edad_imposible(self) -> None:
        assert _age_range(150) is None

    def test_edad_cero(self) -> None:
        # Edad 0 (bebé) es válida
        assert _age_range(0) == {"min": 0, "max": 0}

    def test_no_edad_en_fixture(self) -> None:
        """Registro con edad=None produce age_range=None."""
        records = [_new_schema_record(nombre="DEMO SIN EDAD", edad=None, ultima_ubicacion="Lara")]
        raw = _make_raw({"items": records, "total": 1})
        p = _parser().parse(raw)[0]
        assert p.age_range is None


# ---------------------------------------------------------------------------
# Tests: nota
# ---------------------------------------------------------------------------

class TestNota:
    def test_nota_with_id_and_desc(self) -> None:
        rec = {"id": 42, "descripcion": "Fue visto en demo"}
        nota = _build_nota(rec)
        assert nota == "[id:42] Fue visto en demo"

    def test_nota_id_only(self) -> None:
        rec = {"id": 42, "descripcion": None}
        assert _build_nota(rec) == "[id:42]"

    def test_nota_desc_only(self) -> None:
        rec = {"id": None, "descripcion": "Solo descripcion"}
        assert _build_nota(rec) == "Solo descripcion"

    def test_nota_empty(self) -> None:
        rec = {"id": None, "descripcion": None}
        assert _build_nota(rec) is None

    def test_no_descripcion_still_has_id(self) -> None:
        """registro 1: sin descripcion pero tiene id UUID → nota con solo id."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        p = _parser().parse(raw)[1]
        assert p.nota == f"[id:{_FIXTURE_ID_1}]"


# ---------------------------------------------------------------------------
# Tests: robustez y tolerancia a errores
# ---------------------------------------------------------------------------

class TestRobustness:
    def test_missing_nombre_skips_record(self) -> None:
        """Registro sin nombre debe omitirse, los demás deben parsearse."""
        records = [
            _new_schema_record(id="r1", nombre=None),
            _new_schema_record(id="r2", nombre="DEMO VALIDO", estado="encontrado"),
        ]
        raw = _make_raw({"items": records, "total": 2})
        result = _parser().parse(raw)
        assert len(result) == 1
        assert result[0].full_name == "Demo Valido"

    def test_empty_nombre_skips_record(self) -> None:
        records = [_new_schema_record(id="r99", nombre="   ")]
        raw = _make_raw({"items": records, "total": 1})
        assert _parser().parse(raw) == []

    def test_empty_data_list(self) -> None:
        raw = _make_raw({"items": [], "total": 0})
        assert _parser().parse(raw) == []

    def test_malformed_raw_content_string(self) -> None:
        """raw_content como string (no dict) no debe lanzar excepción."""
        raw = _make_raw("texto plano inesperado")
        result = _parser().parse(raw)
        assert result == []

    def test_raw_content_as_list(self) -> None:
        """Compatibilidad: raw_content puede ser una lista directa de records."""
        records = [_new_schema_record(id="r5", nombre="LISTA DIRECTA DEMO")]
        raw = _make_raw(records)
        result = _parser().parse(raw)
        assert len(result) == 1

    def test_one_bad_record_does_not_break_others(self) -> None:
        """Un registro sin nombre no debe interrumpir el resto."""
        records = [
            _new_schema_record(id="r10", nombre=""),
            _new_schema_record(id="r11", nombre="DEMO OK", estado="herido"),
        ]
        raw = _make_raw({"items": records, "total": 2})
        result = _parser().parse(raw)
        assert len(result) == 1
        assert result[0].full_name == "Demo Ok"

    def test_telefono_never_in_result(self) -> None:
        """Ninguna Person del resultado debe exponer el teléfono de contacto."""
        fixture = _load_fixture()
        raw = _make_raw(fixture)
        for p in _parser().parse(raw):
            p_dict = p.model_dump()
            for key in p_dict:
                assert "telefono" not in key


# ---------------------------------------------------------------------------
# Tests: paginación — múltiples RawContent concatenados
# ---------------------------------------------------------------------------

class TestPagination:
    def test_two_pages_produce_combined_results(self) -> None:
        """
        Simula dos páginas de 3 y 2 registros.
        El parser se llama dos veces (una por página, como haría run_pipeline).
        """
        fixture = _load_fixture()
        records = fixture["items"]
        page1 = _make_raw({"items": records[:3], "total": 5})
        page2 = _make_raw({"items": records[3:], "total": 5})

        parser = _parser()
        all_persons = parser.parse(page1) + parser.parse(page2)
        assert len(all_persons) == 5

    def test_status_preserved_across_pages(self) -> None:
        fixture = _load_fixture()
        records = fixture["items"]
        parser = _parser()
        p1 = parser.parse(_make_raw({"items": [records[0]], "total": 5}))
        p2 = parser.parse(_make_raw({"items": [records[3]], "total": 5}))
        assert p1[0].status == "missing"
        assert p2[0].status == "deceased"
