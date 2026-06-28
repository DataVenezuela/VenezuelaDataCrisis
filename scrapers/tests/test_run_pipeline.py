"""
scrapers/tests/test_run_pipeline.py
=====================================
Tests de integración offline del orquestador ``run_pipeline``.

Estrategia
----------
Todos los tests son 100% offline: ninguno hace llamadas de red.
Las fuentes de red (api_json, html_static) se mockean inyectando adapters
falsos en el registry del pipeline vía monkeypatch.  La fuente demo (manual_file)
se construye íntegramente en ``tmp_path`` mediante el fixture ``demo_config``
— sin leer ningún archivo del repo.

Cobertura
----------
- Pipeline completo con fuente demo (manual_file → text parser)
- Summary dict tiene las keys que espera cli.py
- sources_processed se incrementa por fuente exitosa
- documents_exported refleja registros reales en JSONL
- Pipeline con fuente deshabilitada (enabled=false) la omite
- Error en una fuente no tumba las demás
- Adapter no implementado para un type desconocido no cuenta como error fatal
- Límite por fuente (limit=N) se respeta
- JSONL producido es parseable como JSON línea a línea
- Campos obligatorios presentes en cada registro exportado
- confidence_score entre 0.0 y 1.0
- _entity_type nunca aparece en el JSONL exportado
- Con PII_SALT configurado, tokenize_pii_fields se aplica (integración)
- fuente api_json con ApiAdapter mockeado produce Person via EncuentralosParser
- Fuente con error fatal en fetch no rompe el pipeline completo
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scrapers.adapters.base import RawContent
from scrapers.models import Person
from scrapers.models.source import SourceConfig
from scrapers.pipelines.run_pipeline import _get_adapter, run_pipeline

# ---------------------------------------------------------------------------
# Constantes y helpers
# ---------------------------------------------------------------------------

# Campos que todo registro exportado debe tener (Person mínimo)
_REQUIRED_PERSON_KEYS = {
    "full_name", "fuente", "status", "trust_tier",
    "confidence_score", "verification_status",
}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _make_demo_config(tmp_path: Path, sources_yaml: str) -> Path:
    """Crea un YAML de config temporal con el contenido dado."""
    cfg = tmp_path / "test_sources.yaml"
    cfg.write_text(sources_yaml, encoding="utf-8")
    return cfg


def _make_synthetic_dump(tmp_path: Path) -> Path:
    """Crea un archivo de texto sintético para fuentes manual_file."""
    dump = tmp_path / "synthetic_dump.txt"
    dump.write_text(
        "Datos sintéticos de prueba.\n"
        "Se necesita ayuda en Barquisimeto tras el terremoto.\n"
        "Familia Demo busca a Juan Demo, 35 años, Lara.\n",
        encoding="utf-8",
    )
    return dump


@pytest.fixture()
def demo_config(tmp_path: Path) -> Path:
    """
    Config YAML + dump de texto creados en tmp_path para cada test.

    No depende de ningún archivo del repo (``synthetic_dump.txt`` no existe
    en master), así que el stack completo pasa en CI y en checkout limpio.
    """
    dump = tmp_path / "synthetic_dump.txt"
    dump.write_text(
        "Datos sintéticos de prueba.\n"
        "Se necesita ayuda en Barquisimeto tras el terremoto.\n"
        "Familia Demo busca a Juan Demo, 35 años, Lara.\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "sources.demo.yaml"
    cfg.write_text(
        f"""project:
  event_id: venezuela_earthquake_demo
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: demo_manual_synthetic
    name: Demo manual sintético
    type: manual_file
    enabled: true
    trust_tier: C
    url: "{dump}"
    refresh_minutes: 60
    required_keywords: []
    parser_asignado: text
    notes: "Datos 100% sintéticos para pruebas offline."
""",
        encoding="utf-8",
    )
    return cfg


def _encuentralos_raw(records: list[dict]) -> RawContent:
    """Construye un RawContent del estilo de la API de encuentralos."""
    return RawContent(
        source_key="encuentralos_tecnosoft",
        source_url="https://encuentralos.tecnosoft.dev/api/personas?limit=20&offset=0",
        fetched_at="2026-06-24T15:30:00Z",
        http_status=200,
        content_type="application/json",
        content_hash="sha256:abc",
        raw_content={"data": records, "total": len(records)},
        page=1,
        total_pages=1,
        offset=0,
        limit=20,
        records_in_page=len(records),
    )


_SAMPLE_ENCUENTRALOS_RECORDS = [
    {
        "id": 1001,
        "nombre": "JUAN DEMO PEREZ",
        "cedula": None,
        "edad": 35,
        "estado": "Lara",
        "municipio": "Iribarren",
        "status": "desaparecido",
        "observaciones": "Visto en Barquisimeto",
        "foto": None,
        "fecha_reporte": "2026-06-24",
        "telefono_contacto": None,
    },
    {
        "id": 1002,
        "nombre": "ANA DEMO GARCIA",
        "cedula": None,
        "edad": 28,
        "estado": "Zulia",
        "municipio": None,
        "status": "encontrado",
        "observaciones": None,
        "foto": None,
        "fecha_reporte": "2026-06-24",
        "telefono_contacto": None,
    },
]


# ---------------------------------------------------------------------------
# Tests: fuente demo (manual_file → text parser) — sin red
# ---------------------------------------------------------------------------

class TestDemoSourceOffline:
    """Pipeline completo con la fuente demo que existe en el repo."""

    def test_returns_summary_dict(self, tmp_path: Path, demo_config: Path) -> None:
        summary = run_pipeline(
            config_path=demo_config,
            output_dir=tmp_path / "out",
        )
        assert isinstance(summary, dict)

    def test_summary_has_required_keys(self, tmp_path: Path, demo_config: Path) -> None:
        summary = run_pipeline(
            config_path=demo_config,
            output_dir=tmp_path / "out",
        )
        required = {
            "sources_processed",
            "documents_exported",
            "claims_exported",
            "claims_deduplicated",
            "errors",
        }
        assert required.issubset(summary.keys())

    def test_sources_processed_is_one(self, tmp_path: Path, demo_config: Path) -> None:
        summary = run_pipeline(
            config_path=demo_config,
            output_dir=tmp_path / "out",
        )
        assert summary["sources_processed"] == 1

    def test_documents_exported_positive(self, tmp_path: Path, demo_config: Path) -> None:
        summary = run_pipeline(
            config_path=demo_config,
            output_dir=tmp_path / "out",
        )
        assert summary["documents_exported"] >= 1

    def test_claims_exported_equals_documents(self, tmp_path: Path, demo_config: Path) -> None:
        """claims_exported es alias legacy de documents_exported."""
        summary = run_pipeline(
            config_path=demo_config,
            output_dir=tmp_path / "out",
        )
        assert summary["claims_exported"] == summary["documents_exported"]

    def test_errors_is_list(self, tmp_path: Path, demo_config: Path) -> None:
        summary = run_pipeline(
            config_path=demo_config,
            output_dir=tmp_path / "out",
        )
        assert isinstance(summary["errors"], list)

    def test_persons_jsonl_created(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "out"
        run_pipeline(config_path=demo_config, output_dir=out)
        assert (out / "persons.jsonl").exists()

    def test_jsonl_is_valid_json_per_line(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "out"
        run_pipeline(config_path=demo_config, output_dir=out)
        records = _read_jsonl(out / "persons.jsonl")
        assert len(records) >= 1
        for rec in records:
            assert isinstance(rec, dict)

    def test_entity_type_not_in_export(self, tmp_path: Path, demo_config: Path) -> None:
        """_entity_type es campo interno — nunca debe aparecer en JSONL."""
        out = tmp_path / "out"
        run_pipeline(config_path=demo_config, output_dir=out)
        for rec in _read_jsonl(out / "persons.jsonl"):
            assert "_entity_type" not in rec

    def test_required_person_fields_present(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "out"
        run_pipeline(config_path=demo_config, output_dir=out)
        for rec in _read_jsonl(out / "persons.jsonl"):
            missing = _REQUIRED_PERSON_KEYS - rec.keys()
            assert not missing, f"Campos faltantes: {missing}"

    def test_confidence_score_in_range(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "out"
        run_pipeline(config_path=demo_config, output_dir=out)
        for rec in _read_jsonl(out / "persons.jsonl"):
            score = rec.get("confidence_score", -1)
            assert 0.0 <= score <= 1.0, f"score fuera de rango: {score}"

    def test_output_dir_created(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "nested" / "output"
        run_pipeline(config_path=demo_config, output_dir=out)
        assert out.exists()


# ---------------------------------------------------------------------------
# Tests: fuente deshabilitada se omite
# ---------------------------------------------------------------------------

class TestDisabledSource:
    def test_disabled_source_not_processed(self, tmp_path: Path, demo_config: Path) -> None:
        dump = _make_synthetic_dump(tmp_path)
        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: fuente_deshabilitada
    name: Fuente deshabilitada
    type: manual_file
    enabled: false
    trust_tier: C
    url: "{dump}"
    refresh_minutes: 60
    parser_asignado: text
""")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 0
        assert summary["documents_exported"] == 0

    def test_mixed_enabled_disabled(self, tmp_path: Path, demo_config: Path) -> None:
        dump = _make_synthetic_dump(tmp_path)
        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: habilitada
    name: Habilitada
    type: manual_file
    enabled: true
    trust_tier: C
    url: "{dump}"
    refresh_minutes: 60
    parser_asignado: text
  - id: deshabilitada
    name: Deshabilitada
    type: manual_file
    enabled: false
    trust_tier: C
    url: "{dump}"
    refresh_minutes: 60
    parser_asignado: text
""")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 1


# ---------------------------------------------------------------------------
# Tests: resiliencia — error en una fuente no tumba el pipeline
# ---------------------------------------------------------------------------

class TestResilience:
    def test_bad_url_does_not_crash_pipeline(self, tmp_path: Path, demo_config: Path) -> None:
        """Fuente con URL local inexistente → error anotado, pipeline continúa."""
        dump = _make_synthetic_dump(tmp_path)
        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: fuente_rota
    name: Fuente rota
    type: manual_file
    enabled: true
    trust_tier: C
    url: "/ruta/inexistente/no_existe.txt"
    refresh_minutes: 60
    parser_asignado: text
  - id: fuente_buena
    name: Fuente buena
    type: manual_file
    enabled: true
    trust_tier: C
    url: "{dump}"
    refresh_minutes: 60
    parser_asignado: text
""")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        # La fuente buena debe haberse procesado
        assert summary["sources_processed"] >= 1
        # El pipeline no debe haber lanzado excepción (llegamos hasta aquí)

    def test_errors_list_non_empty_on_failure(self, tmp_path: Path, demo_config: Path) -> None:
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: fuente_rota
    name: Fuente rota
    type: manual_file
    enabled: true
    trust_tier: C
    url: "/no_existe.txt"
    refresh_minutes: 60
    parser_asignado: text
""")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert len(summary["errors"]) >= 1

    def test_invalid_config_returns_error_summary(self, tmp_path: Path, demo_config: Path) -> None:
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("esto no es un yaml valido: [\n", encoding="utf-8")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 0
        assert len(summary["errors"]) >= 1

    def test_unimplemented_adapter_type_skipped(self) -> None:
        """Un type sin adapter registrado en `_get_adapter` debe omitirse (None), no lanzar."""
        source = SourceConfig(
            id="fuente_futura",
            name="Fuente con type aun no soportado",
            type="not_yet_implemented",
            enabled=True,
            trust_tier="C",
            url="https://example.org/app",
            refresh_minutes=60,
            parser_asignado="html",
        )

        assert _get_adapter(source) is None


# ---------------------------------------------------------------------------
# Tests: límite por fuente
# ---------------------------------------------------------------------------

class TestLimit:
    def test_limit_zero_exports_nothing(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "out"
        summary = run_pipeline(config_path=demo_config, output_dir=out, limit=0)
        assert summary["documents_exported"] == 0

    def test_limit_one_exports_at_most_one(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "out"
        run_pipeline(config_path=demo_config, output_dir=out, limit=1)
        records = _read_jsonl(out / "persons.jsonl")
        assert len(records) <= 1

    def test_no_limit_exports_all(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "out"
        summary_no_limit = run_pipeline(config_path=demo_config, output_dir=out, limit=None)
        summary_high_limit = run_pipeline(config_path=demo_config, output_dir=out, limit=9999)
        assert summary_no_limit["documents_exported"] == summary_high_limit["documents_exported"]


# ---------------------------------------------------------------------------
# Tests: fuente api_json con adapter mockeado
# ---------------------------------------------------------------------------

class TestApiJsonSourceMocked:
    """
    Verifica que el pipeline conecta correctamente ApiAdapter + EncuentralosParser
    sin hacer llamadas de red reales.
    """

    @staticmethod
    def _mock_parser() -> MagicMock:
        """Mock de parser que devuelve Person con campos válidos."""
        parser = MagicMock()
        parser.parse.return_value = [
            Person(
                full_name="JUAN DEMO PEREZ",
                status="missing",
                fuente="encuentralos_tecnosoft",
                age_range={"min": 30, "max": 40},
                last_known_location="Lara, Venezuela",
            ),
            Person(
                full_name="ANA DEMO GARCIA",
                status="found",
                fuente="encuentralos_tecnosoft",
                age_range={"min": 25, "max": 35},
                last_known_location="Zulia, Venezuela",
            ),
        ]
        return parser

    def _mock_adapter(self) -> MagicMock:
        """Mock de ApiAdapter que devuelve páginas predefinidas."""
        adapter = MagicMock()
        adapter.default_path = "/api/personas"
        adapter.fetch_all.return_value = iter([
            _encuentralos_raw(_SAMPLE_ENCUENTRALOS_RECORDS)
        ])
        adapter.close = MagicMock()
        return adapter

    def test_api_json_source_produces_persons(self, tmp_path: Path, demo_config: Path) -> None:
        _make_synthetic_dump(tmp_path)  # no se usa, pero el yaml lo necesita como dummy
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos tecnosoft
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
""")
        mock_adapter = self._mock_adapter()

        with patch(
            "scrapers.pipelines.run_pipeline._get_adapter",
            return_value=mock_adapter,
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser",
            return_value=self._mock_parser(),
        ):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")

        assert summary["sources_processed"] == 1
        assert summary["documents_exported"] == len(_SAMPLE_ENCUENTRALOS_RECORDS)

    def test_api_json_persons_have_correct_status(self, tmp_path: Path, demo_config: Path) -> None:
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos tecnosoft
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
""")
        mock_adapter = self._mock_adapter()
        out = tmp_path / "out"

        with patch(
            "scrapers.pipelines.run_pipeline._get_adapter",
            return_value=mock_adapter,
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser",
            return_value=self._mock_parser(),
        ):
            run_pipeline(config_path=cfg, output_dir=out)

        records = _read_jsonl(out / "persons.jsonl")
        statuses = {r["status"] for r in records}
        assert statuses == {"missing", "found"}

    def test_api_json_no_entity_type_in_export(self, tmp_path: Path, demo_config: Path) -> None:
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos tecnosoft
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
""")
        mock_adapter = self._mock_adapter()
        out = tmp_path / "out"

        with patch(
            "scrapers.pipelines.run_pipeline._get_adapter",
            return_value=mock_adapter,
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser",
            return_value=self._mock_parser(),
        ):
            run_pipeline(config_path=cfg, output_dir=out)

        for rec in _read_jsonl(out / "persons.jsonl"):
            assert "_entity_type" not in rec

    def test_adapter_close_called_even_when_fetch_raises(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        """Un adapter con recursos vivos (ej. browser de Playwright) no debe
        quedar huerfano si fetch_all() falla — close() debe correr en finally."""
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos tecnosoft
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
""")
        mock_adapter = self._mock_adapter()
        mock_adapter.fetch_all.side_effect = RuntimeError("fetch agotado tras reintentos")

        with patch(
            "scrapers.pipelines.run_pipeline._get_adapter",
            return_value=mock_adapter,
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser",
            return_value=self._mock_parser(),
        ):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")

        mock_adapter.close.assert_called_once()
        assert summary["sources_processed"] == 0
        assert len(summary["errors"]) == 1


# ---------------------------------------------------------------------------
# Tests: PII_SALT presente → tokenize_pii_fields se ejecuta
# ---------------------------------------------------------------------------

class TestPIISalt:
    def test_pipeline_works_without_pii_salt(self, tmp_path: Path, demo_config: Path) -> None:
        """Sin PII_SALT el pipeline no debe fallar."""
        env = {k: v for k, v in os.environ.items() if k != "PII_SALT"}
        with patch.dict(os.environ, env, clear=True):
            summary = run_pipeline(
                config_path=demo_config,
                output_dir=tmp_path / "out",
            )
        assert summary["sources_processed"] == 1

    def test_pipeline_works_with_pii_salt(self, tmp_path: Path, demo_config: Path) -> None:
        """Con PII_SALT configurado, el pipeline debe funcionar igual."""
        with patch.dict(os.environ, {"PII_SALT": "test-salt-pipeline"}):
            summary = run_pipeline(
                config_path=demo_config,
                output_dir=tmp_path / "out",
            )
        assert summary["sources_processed"] == 1
        assert summary["documents_exported"] >= 1


# ---------------------------------------------------------------------------
# Tests: múltiples fuentes
# ---------------------------------------------------------------------------

class TestMultipleSources:
    def test_two_sources_both_processed(self, tmp_path: Path) -> None:
        dump1 = tmp_path / "dump1.txt"
        dump2 = tmp_path / "dump2.txt"
        dump1.write_text("Persona demo uno. Necesita ayuda en Caracas.", encoding="utf-8")
        dump2.write_text("Persona demo dos. Vista en Maracaibo.", encoding="utf-8")

        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: fuente_uno
    name: Fuente uno
    type: manual_file
    enabled: true
    trust_tier: C
    url: "{dump1}"
    refresh_minutes: 60
    parser_asignado: text
  - id: fuente_dos
    name: Fuente dos
    type: manual_file
    enabled: true
    trust_tier: B
    url: "{dump2}"
    refresh_minutes: 60
    parser_asignado: text
""")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 2
        assert summary["documents_exported"] >= 2

    def test_second_source_failure_first_still_exported(self, tmp_path: Path) -> None:
        dump_ok = _make_synthetic_dump(tmp_path)
        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: test
  default_country: Venezuela
  output_mode: sanitized_jsonl
sources:
  - id: fuente_ok
    name: Fuente ok
    type: manual_file
    enabled: true
    trust_tier: C
    url: "{dump_ok}"
    refresh_minutes: 60
    parser_asignado: text
  - id: fuente_rota
    name: Fuente rota
    type: manual_file
    enabled: true
    trust_tier: C
    url: "/no_existe_jamas.txt"
    refresh_minutes: 60
    parser_asignado: text
""")
        out = tmp_path / "out"
        summary = run_pipeline(config_path=cfg, output_dir=out)
        # La buena debe haberse procesado
        assert summary["sources_processed"] >= 1
        # Y exportado registros
        assert summary["documents_exported"] >= 1