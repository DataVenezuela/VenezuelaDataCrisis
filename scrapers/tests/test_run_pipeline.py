"""
scrapers/tests/test_run_pipeline.py
=====================================
Tests de integracion offline del orquestador ``run_pipeline``.

Estrategia
----------
Todos los tests son 100% offline: ninguno hace llamadas de red.
- Las fuentes de red (api_json) se mockean inyectando adapters/parsers
  falsos en el registry del pipeline via monkeypatch.
- La fuente demo (manual_file) se construye en ``tmp_path``.
- El destino staging (/api/aportes) se intercepta con un
  ``_StagingTransport`` (httpx.BaseTransport) inyectado en el StagingExporter
  que construye run_pipeline, parcheando ``StagingExporter`` por una factory
  de test y exportando las STAGING_* via patch.dict(os.environ).

El JSONL en disco desaparecio: ya no se leen persons.jsonl ni se asserta
documents_exported. Se asserta sobre los POSTs capturados y sobre
summary['staging_sent'].
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from scrapers.adapters.base import RawContent
from scrapers.exporters.staging_exporter import StagingConfig, StagingExporter
from scrapers.models import Person
from scrapers.models.source import SourceConfig
from scrapers.pipelines import run_pipeline as rp
from scrapers.pipelines.run_pipeline import _get_adapter, run_pipeline

# ---------------------------------------------------------------------------
# Constantes y helpers
# ---------------------------------------------------------------------------

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"

_STAGING_ENV = {
    "STAGING_API_KEY": "test-key",
    "STAGING_BASE_URL": "https://staging.test",
    "STAGING_SOURCE_SLUG": "demo-source",
}


class _StagingTransport(httpx.BaseTransport):
    """Intercepta POSTs a /api/aportes y el watermark, idempotente por external_id."""

    def __init__(self, aportes_status: int = 201) -> None:
        self.aportes_status = aportes_status
        self.posts: list[dict[str, Any]] = []
        self._seen_external_ids: set[str] = set()
        self.watermark_puts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/aportes":
            body = json.loads(request.content)
            self.posts.append(body)
            external_id = body.get("external_id")
            if external_id in self._seen_external_ids:
                return httpx.Response(409, json={"duplicate": True})
            # Solo se marca como visto si el envio fue exitoso: un status de
            # error (p.ej. 500) puede reintentarse y debe seguir fallando.
            if self.aportes_status in (200, 201):
                self._seen_external_ids.add(external_id)
            return httpx.Response(self.aportes_status, json={"ok": True})
        if path.startswith("/api/source_watermarks"):
            if request.method == "GET":
                return httpx.Response(404)
            self.watermark_puts.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)


class _NoRealNetworkTransport(httpx.BaseTransport):
    """Guard: cualquier request real falla el test."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"red real prohibida en tests: {request.url}")


def _patch_exporter(transport: httpx.BaseTransport) -> Any:
    """Factory que reemplaza StagingExporter por uno con client mockeado.

    run_pipeline llama ``StagingExporter(StagingConfig.from_env(), run_id=...)``.
    La factory ignora el config recibido (que viene de las STAGING_* del
    entorno) y construye un exporter con un httpx.Client(transport=...) para
    que ningun POST salga a la red.
    """
    def _factory(config: StagingConfig | None, *, run_id: str | None = None) -> StagingExporter:
        if config is None:
            return StagingExporter(None, run_id=run_id)
        client = httpx.Client(base_url=config.base_url, transport=transport)
        return StagingExporter(config, client=client, run_id=run_id)

    return patch.object(rp, "StagingExporter", side_effect=_factory)


def _make_demo_config(tmp_path: Path, sources_yaml: str) -> Path:
    cfg = tmp_path / "test_sources.yaml"
    cfg.write_text(sources_yaml, encoding="utf-8")
    return cfg


def _yaml_url(path: Path) -> str:
    """Path como URL segura para YAML en Windows (evita escapes \\U)."""
    return path.as_posix()


def _make_synthetic_dump(tmp_path: Path) -> Path:
    dump = tmp_path / "synthetic_dump.txt"
    dump.write_text(
        "Datos sinteticos de prueba.\n"
        "Se necesita ayuda en Barquisimeto tras el terremoto.\n"
        "Familia Demo busca a Juan Demo, 35 anios, Lara.\n",
        encoding="utf-8",
    )
    return dump


@pytest.fixture()
def demo_config(tmp_path: Path) -> Path:
    """Config YAML de una fuente api_json (parser encuentralos mockeado)."""
    cfg = tmp_path / "sources.demo.yaml"
    cfg.write_text(
        """project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos tecnosoft
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
""",
        encoding="utf-8",
    )
    return cfg


def _encuentralos_raw(records: list[dict]) -> RawContent:
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


def _mock_parser(persons: list[Person] | None = None) -> MagicMock:
    parser = MagicMock()
    parser.parse.return_value = persons if persons is not None else [
        Person(
            full_name="JUAN DEMO PEREZ",
            event_id=_EVENT_ID,
            status="missing",
            fuente="encuentralos_tecnosoft",
            age_range={"min": 30, "max": 40},
            last_known_location="Lara, Venezuela",
        ),
        Person(
            full_name="ANA DEMO GARCIA",
            event_id=_EVENT_ID,
            status="found",
            fuente="encuentralos_tecnosoft",
            age_range={"min": 25, "max": 35},
            last_known_location="Zulia, Venezuela",
        ),
    ]
    return parser


def _mock_adapter(records: list[dict] | None = None) -> MagicMock:
    adapter = MagicMock()
    adapter.default_path = "/api/personas"
    adapter.fetch_all.return_value = iter([_encuentralos_raw(records or [{"id": 1}])])
    adapter.close = MagicMock()
    return adapter


# ---------------------------------------------------------------------------
# Test: limpieza de recursos del adapter
# ---------------------------------------------------------------------------

class TestAdapterCleanup:
    def test_adapter_closed_when_parser_missing(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        """Fuente con parser no registrado: el adapter se cierra igual, no se filtra."""
        adapter = _mock_adapter()
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=None
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        adapter.close.assert_called()


# ---------------------------------------------------------------------------
# Tests: summary y wiring basico
# ---------------------------------------------------------------------------

class TestSummaryShape:
    def test_returns_summary_dict(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert isinstance(summary, dict)

    def test_summary_has_required_keys(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        required = {
            "sources_processed",
            "staging_sent",
            "staging_duplicates",
            "staging_errors",
            "errors",
        }
        assert required.issubset(summary.keys())

    def test_errors_is_list(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert isinstance(summary["errors"], list)

    def test_output_dir_created(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "nested" / "output"
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=out)
        assert out.exists()


# ---------------------------------------------------------------------------
# Tests: staging recibe los aportes
# ---------------------------------------------------------------------------

class TestStagingSend:
    def test_sources_processed_is_one(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 1

    def test_staging_sent_matches_records(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["staging_sent"] == 2
        assert len(transport.posts) == 2

    def test_no_entity_type_in_payload_data(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        for post in transport.posts:
            assert "_entity_type" not in post["data"]

    def test_confidence_score_in_range(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        for post in transport.posts:
            score = post["data"].get("confidence_score", -1)
            assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Tests: idempotencia
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_same_external_ids(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        for _ in range(2):
            with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
                "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
            ), patch(
                "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
            ):
                run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        # Cuatro POSTs en total (2 por corrida) pero solo 2 external_id unicos.
        unique = {p["external_id"] for p in transport.posts}
        assert len(transport.posts) == 4
        assert len(unique) == 2

    def test_second_run_all_duplicates(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        # Primera corrida: todos nuevos.
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        # Segunda corrida: el transport responde 409 a los external_id ya vistos.
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["staging_sent"] == 0
        assert summary["staging_duplicates"] == 2


# ---------------------------------------------------------------------------
# Tests: block keys de Person (con / sin cedula_hmac)
# ---------------------------------------------------------------------------

class TestPersonBlockKeysEndToEnd:
    def test_person_with_and_without_hmac(self, tmp_path: Path, demo_config: Path) -> None:
        persons = [
            Person(
                full_name="JUAN DEMO PEREZ",
                event_id=_EVENT_ID,
                cedula_hmac="hmac-abc",
                status="missing",
                fuente="encuentralos_tecnosoft",
                last_known_location="Lara",
            ),
            Person(
                full_name="ANA DEMO GARCIA",
                event_id=_EVENT_ID,
                status="missing",
                fuente="encuentralos_tecnosoft",
                last_known_location="Zulia",
            ),
        ]
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser(persons)
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        by_name = {p["data"]["full_name"]: p for p in transport.posts}
        juan_keys = by_name["JUAN DEMO PEREZ"]["block_keys"]
        ana_keys = by_name["ANA DEMO GARCIA"]["block_keys"]
        assert any(k.startswith(f"ced:{_EVENT_ID}:hmac-abc") for k in juan_keys)
        assert all(not k.startswith("ced:") for k in ana_keys)


# ---------------------------------------------------------------------------
# Tests: dry-run sin env vars de staging
# ---------------------------------------------------------------------------

class TestStagingDisabled:
    def test_no_env_vars_dry_run(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        # Sin STAGING_*: el exporter entra en dry-run; el transport no debe
        # recibir ningun POST aunque la factory este parcheada.
        env = {k: v for k, v in os.environ.items()
               if k not in ("STAGING_API_KEY", "STAGING_BASE_URL", "STAGING_SOURCE_SLUG")}
        with patch.dict(os.environ, env, clear=True), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["staging_sent"] == 0
        assert summary["errors"] == []
        assert transport.posts == []


# ---------------------------------------------------------------------------
# Tests: watermark
# ---------------------------------------------------------------------------

class TestWatermarkEndToEnd:
    def test_watermark_advances_on_success(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport(aportes_status=201)
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert transport.watermark_puts
        assert transport.watermark_puts[-1]["watermark_at"] == "2026-06-24T15:30:00Z"

    def test_watermark_not_advanced_on_failure(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport(aportes_status=500)
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.exporters.staging_exporter.time.sleep", lambda *_: None
        ), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert transport.watermark_puts == []
        assert summary["staging_errors"] >= 1


# ---------------------------------------------------------------------------
# Tests: fuente deshabilitada
# ---------------------------------------------------------------------------

class TestDisabledSource:
    def test_disabled_source_not_processed(self, tmp_path: Path) -> None:
        dump = _make_synthetic_dump(tmp_path)
        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: fuente_deshabilitada
    name: Fuente deshabilitada
    type: manual_file
    enabled: false
    trust_tier: C
    url: "{_yaml_url(dump)}"
    refresh_minutes: 60
    parser_asignado: encuentralos
""")
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 0
        assert summary["staging_sent"] == 0


# ---------------------------------------------------------------------------
# Tests: resiliencia
# ---------------------------------------------------------------------------

class TestResilience:
    def test_invalid_config_returns_error_summary(self, tmp_path: Path) -> None:
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("esto no es un yaml valido: [\n", encoding="utf-8")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 0
        assert len(summary["errors"]) >= 1
        assert summary["staging_sent"] == 0

    def test_invalid_event_id_returns_error_summary(self, tmp_path: Path) -> None:
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: no-es-un-uuid
  default_country: Venezuela
sources: []
""")
        summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 0
        assert len(summary["errors"]) >= 1

    def test_unimplemented_adapter_type_skipped(self) -> None:
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

    def test_unimplemented_parser_source_omitted(self, tmp_path: Path) -> None:
        dump = _make_synthetic_dump(tmp_path)
        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: fuente_sin_parser
    name: Fuente sin parser concreto
    type: manual_file
    enabled: true
    trust_tier: C
    url: "{_yaml_url(dump)}"
    refresh_minutes: 60
    parser_asignado: text
""")
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        # La fuente se procesa sin error fatal pero no envia nada (parser None).
        assert summary["sources_processed"] == 1
        assert summary["staging_sent"] == 0
        assert transport.posts == []

    def test_unimplemented_parser_visible_in_summary(self, tmp_path: Path) -> None:
        """Una fuente con parser no registrado aparece VISIBLE en el resumen.

        No basta con el log.warning silencioso: la omision se contabiliza en
        summary["errors"] (con el slug, el parser_asignado y la palabra
        omitida) para que el operador la vea en el resumen del run.
        """
        dump = _make_synthetic_dump(tmp_path)
        cfg = _make_demo_config(tmp_path, f"""
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: fuente_sin_parser
    name: Fuente sin parser concreto
    type: manual_file
    enabled: true
    trust_tier: C
    url: "{_yaml_url(dump)}"
    refresh_minutes: 60
    parser_asignado: parser_inexistente
""")
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        omissions = [
            e for e in summary["errors"]
            if "parser no implementado" in e and "omitida" in e
        ]
        assert len(omissions) == 1
        assert "parser_inexistente" in omissions[0]
        assert "fuente_sin_parser" in omissions[0]
        # La omision tambien cuenta como error de staging (visibilidad numerica).
        assert summary["staging_errors"] >= 1

    def test_fetch_error_does_not_crash_pipeline(self, tmp_path: Path, demo_config: Path) -> None:
        adapter = _mock_adapter()
        adapter.fetch_all.side_effect = RuntimeError("fetch agotado tras reintentos")
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        adapter.close.assert_called_once()
        assert summary["sources_processed"] == 0
        assert len(summary["errors"]) == 1


# ---------------------------------------------------------------------------
# Tests: limite por fuente
# ---------------------------------------------------------------------------

class TestLimit:
    def test_limit_zero_sends_nothing(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out", limit=0)
        assert summary["staging_sent"] == 0

    def test_limit_one_sends_at_most_one(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out", limit=1)
        assert len(transport.posts) <= 1


# ---------------------------------------------------------------------------
# Tests: proteccion de menores end-to-end
# ---------------------------------------------------------------------------

class TestMinorProtectionEndToEnd:
    def test_minor_fields_redacted_in_payload(self, tmp_path: Path, demo_config: Path) -> None:
        persons = [
            Person(
                full_name="NINIO DEMO PEREZ",
                event_id=_EVENT_ID,
                is_minor=True,
                foto="https://example.org/foto.jpg",
                cedula_masked="V-****1234",
                last_known_location="Iribarren, Lara",
                fuente="encuentralos_tecnosoft",
            )
        ]
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser(persons)
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert len(transport.posts) == 1
        data = transport.posts[0]["data"]
        assert data["foto"] is None
        assert data["cedula_masked"] is None
        assert data["last_known_location"] == "Lara"

    def test_minor_record_omitted_when_protection_raises(self, tmp_path: Path, demo_config: Path) -> None:
        persons = [
            Person(
                full_name="NINIO DEMO PEREZ",
                event_id=_EVENT_ID,
                is_minor=True,
                foto="https://example.org/foto.jpg",
                last_known_location="Iribarren, Lara",
                fuente="encuentralos_tecnosoft",
            )
        ]
        transport = _StagingTransport()
        with patch.dict(os.environ, _STAGING_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser(persons)
        ), patch(
            "scrapers.pipelines.run_pipeline.protect_minor_fields", side_effect=ValueError("boom")
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert transport.posts == []
        assert any("registro omitido" in e for e in summary["errors"])


# ---------------------------------------------------------------------------
# Tests: PII_SALT
# ---------------------------------------------------------------------------

class TestPIISalt:
    def test_pipeline_works_with_pii_salt(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        env = {**_STAGING_ENV, "PII_SALT": "test-salt-pipeline"}
        with patch.dict(os.environ, env, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 1
        assert summary["staging_sent"] == 2


# ---------------------------------------------------------------------------
# Tests: guard de red real
# ---------------------------------------------------------------------------

class TestNoRealNetwork:
    def test_no_real_network_during_run(self, tmp_path: Path, demo_config: Path) -> None:
        """Si algun POST escapase a la red real, el guard falla el test."""
        transport = _NoRealNetworkTransport()

        def _factory(config: StagingConfig | None, *, run_id: str | None = None) -> StagingExporter:
            if config is None:
                return StagingExporter(None, run_id=run_id)
            client = httpx.Client(base_url=config.base_url, transport=transport)
            return StagingExporter(config, client=client, run_id=run_id)

        # Fuente deshabilitada -> dry-run efectivo -> no debe tocar el transport.
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources: []
""")
        with patch.dict(os.environ, _STAGING_ENV, clear=False), patch.object(
            rp, "StagingExporter", side_effect=_factory
        ):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 0
