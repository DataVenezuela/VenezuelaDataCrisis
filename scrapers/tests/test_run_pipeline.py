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
from scrapers.adapters.pdf_adapter import PdfTextExtractionError
from scrapers.exporters.provenance_exporter import ProvenanceExporter
from scrapers.exporters.quarantine_exporter import QuarantineConfig, QuarantineExporter
from scrapers.exporters.staging_exporter import StagingConfig, StagingExporter
from scrapers.models import Person
from scrapers.models.source import SourceConfig
from scrapers.pipelines import run_pipeline as rp
from scrapers.pipelines.run_pipeline import _get_adapter, run_pipeline
from scrapers.exporters.quarantine_exporter import (
    REASON_CODES as QUARANTINE_REASON_CODES,
    QuarantineRecord,
)

# ---------------------------------------------------------------------------
# Constantes y helpers
# ---------------------------------------------------------------------------

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"

_SUPABASE_ENV = {
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
    "SUPABASE_INGEST_JWT": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2NyYXBlcl9pbmdlc3QifQ.test",
}


def _source_id_from_url(url: httpx.URL) -> str | None:
    """Extrae X de un filtro PostgREST ``source_id=eq.X`` en la query."""
    value = url.params.get("source_id")
    if value and value.startswith("eq."):
        return value[len("eq.") :]
    return None


class _StagingTransport(httpx.BaseTransport):
    """Intercepta POSTs a /rest/v1/aportes y el watermark via PostgREST.

    PostgREST batch devuelve 201 con body vacio (return=minimal). No hay
    409 porque resolution=merge-duplicates absorbe duplicados. El watermark vive
    en sources.watermark_at: se lee con GET /sources?source_id=eq.X&select=watermark_at
    y se escribe con PATCH /sources?source_id=eq.X {"watermark_at": ...}.
    """

    def __init__(self, aportes_status: int = 201) -> None:
        self.aportes_status = aportes_status
        self.batch_posts: list[list[dict[str, Any]]] = []
        self.watermark_posts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/aportes":
            body = json.loads(request.content)
            if isinstance(body, list):
                self.batch_posts.append(body)
            else:
                self.batch_posts.append([body])
            return httpx.Response(self.aportes_status, json={})
        if path == "/rest/v1/sources":
            if request.method == "GET":
                return httpx.Response(200, json=[{"watermark_at": None}])
            if request.method == "PATCH":
                body = json.loads(request.content)
                self.watermark_posts.append(
                    {"source_id": _source_id_from_url(request.url), **body}
                )
                return httpx.Response(200, json=[body])
            return httpx.Response(404)
        return httpx.Response(404)


class _NoRealNetworkTransport(httpx.BaseTransport):
    """Guard: cualquier request real falla el test."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"red real prohibida en tests: {request.url}")


def _patch_exporter(transport: httpx.BaseTransport) -> Any:
    """Factory que reemplaza StagingExporter por uno con client mockeado.

    run_pipeline llama ``StagingExporter(StagingConfig.from_env(), run_id=...)``.
    La factory ignora el config recibido (que viene de las SUPABASE_* del
    entorno) y construye un exporter con un httpx.Client(transport=...) para
    que ningun POST salga a la red.
    """
    def _factory(config: StagingConfig | None, *, run_id: str | None = None) -> StagingExporter:
        if config is None:
            return StagingExporter(None, run_id=run_id)
        client = httpx.Client(base_url=config.supabase_url, transport=transport)
        return StagingExporter(config, client=client, run_id=run_id)

    return patch.object(rp, "StagingExporter", side_effect=_factory)


# Quarantine ahora usa las mismas SUPABASE_* que staging; _QUARANTINE_ENV
# se conserva vacio para no romper los test que hacen {**_SUPABASE_ENV, **_QUARANTINE_ENV}.
_QUARANTINE_ENV: dict[str, str] = {}


class _QuarantineTransport(httpx.BaseTransport):
    """Intercepta POSTs a /rest/v1/quarantined_records y captura los bodies."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.posts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/v1/quarantined_records":
            self.posts.append(json.loads(request.content))
            return httpx.Response(self.status, json={"ok": True})
        return httpx.Response(404)


def _patch_quarantine_exporter(transport: httpx.BaseTransport) -> Any:
    """Factory que reemplaza QuarantineExporter por uno con client mockeado.

    Espeja ``_patch_exporter``: run_pipeline llama
    ``QuarantineExporter(QuarantineConfig.from_env())``; la factory
    inyecta un httpx.Client(transport=...) para que nada salga a la red.
    """
    def _factory(
        config: QuarantineConfig | None,
    ) -> QuarantineExporter:
        if config is None:
            return QuarantineExporter(None)
        client = httpx.Client(base_url=config.supabase_url, transport=transport)
        return QuarantineExporter(config, client=client)

    return patch.object(rp, "QuarantineExporter", side_effect=_factory)


# ---------------------------------------------------------------------------
# Bronze provenance (issue #256): scrape_runs + raw_artifacts
# ---------------------------------------------------------------------------


class _ProvenanceTransport(httpx.BaseTransport):
    """Intercepta POSTs a /rest/v1/scrape_runs y /rest/v1/raw_artifacts.

    Devuelve return=representation con ids secuenciales (run-N / art-N) para que
    ProvenanceExporter pueda leer run_id/artifact_id. Captura los bodies para los
    asserts de append-only / body_hash / raw_text.
    """

    def __init__(self, *, runs_status: int = 201, artifacts_status: int = 201) -> None:
        self.runs_status = runs_status
        self.artifacts_status = artifacts_status
        self.run_posts: list[dict[str, Any]] = []
        self.artifact_posts: list[dict[str, Any]] = []
        self.artifact_queries: list[str] = []
        self._run_seq = 0
        self._art_seq = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/scrape_runs":
            if request.method == "PATCH":
                return httpx.Response(200, json=[])
            self.run_posts.append(json.loads(request.content))
            self._run_seq += 1
            return httpx.Response(self.runs_status, json=[{"run_id": f"run-{self._run_seq}"}])
        if path == "/rest/v1/raw_artifacts":
            self.artifact_posts.append(json.loads(request.content))
            self.artifact_queries.append(str(request.url.query))
            self._art_seq += 1
            return httpx.Response(
                self.artifacts_status, json=[{"artifact_id": f"art-{self._art_seq}"}]
            )
        return httpx.Response(404)


def _patch_provenance_exporter(transport: httpx.BaseTransport) -> Any:
    """Factory que reemplaza ProvenanceExporter por uno con client mockeado.

    run_pipeline llama ``ProvenanceExporter(StagingConfig.from_env())`` (sin
    run_id). En dry-run (config None) devuelve un exporter deshabilitado.
    """

    def _factory(config: StagingConfig | None) -> ProvenanceExporter:
        if config is None:
            return ProvenanceExporter(None)
        client = httpx.Client(base_url=config.supabase_url, transport=transport)
        return ProvenanceExporter(config, client=client)

    return patch.object(rp, "ProvenanceExporter", side_effect=_factory)


@pytest.fixture(autouse=True)
def _provenance_offline() -> Any:
    """Autouse: mantiene ProvenanceExporter 100% offline en TODO el modulo.

    run_pipeline construye ``ProvenanceExporter(StagingConfig.from_env())``. Con
    las SUPABASE_* seteadas (la mayoria de los tests) el exporter quedaria enabled
    y abriria red real; este autouse lo redirige a un transport mock generico.
    Los tests que quieran inspeccionar los POSTs de Bronze anidan su propio
    ``_patch_provenance_exporter(transport)`` (el patch interno gana en su bloque).
    """
    with _patch_provenance_exporter(_ProvenanceTransport()):
        yield


def _dry_provenance() -> ProvenanceExporter:
    """ProvenanceExporter en dry-run para llamadas directas a _run_source."""
    return ProvenanceExporter(None)


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
        raw_content={"rawJson": records, "total": len(records)},
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
            status="deceased",
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
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=None
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        adapter.close.assert_called()

    def test_adapter_closed_when_get_watermark_raises(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        """Si exporter.get_watermark() lanza (ej. error inesperado leyendo la
        respuesta), el adapter ya creado (browser/conexiones) debe cerrarse
        igual; el error de la fuente queda en el summary, no crashea el run.
        """
        adapter = _mock_adapter()
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ), patch.object(
            StagingExporter, "get_watermark", side_effect=RuntimeError("boom")
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        adapter.close.assert_called()
        assert summary["sources_processed"] == 0
        assert any("boom" in e for e in summary["errors"])


# ---------------------------------------------------------------------------
# Tests: summary y wiring basico
# ---------------------------------------------------------------------------

class TestSummaryShape:
    def test_returns_summary_dict(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert isinstance(summary, dict)

    def test_summary_has_required_keys(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
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
            "quarantined",
            "quarantine_errors",
            "errors",
        }
        assert required.issubset(summary.keys())

    def test_errors_is_list(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert isinstance(summary["errors"], list)

    def test_output_dir_created(self, tmp_path: Path, demo_config: Path) -> None:
        out = tmp_path / "nested" / "output"
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
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
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 1

    def test_staging_sent_matches_records(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["staging_sent"] == 2
        assert len(transport.batch_posts) >= 1
        total_records = sum(len(b) for b in transport.batch_posts)
        assert total_records == 2

    def test_no_entity_type_in_payload_data(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        for batch in transport.batch_posts:
            for post in batch:
                assert "_entity_type" not in post["raw_json"]

    def test_confidence_score_in_range(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        for batch in transport.batch_posts:
            for post in batch:
                score = post["raw_json"].get("confidence_score", -1)
                assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Tests: trazabilidad (issue #236)
# ---------------------------------------------------------------------------

class TestTrazabilidadMetaFields:
    """Trazabilidad tras el cutover Bronze (issue #256).

    La URL de origen y la corrida ya NO viajan en el aporte: viven en
    raw_artifacts (source_url) referenciado por aportes.artifact_id. El aporte
    conserva normalizer_version (columna canonica). run_id/scraper_id/source_url/
    parser_version desaparecieron del payload de aportes.
    """

    _PAGE_URL = "https://encuentralos.tecnosoft.dev/api/personas?limit=20&offset=0"

    def _run(
        self, tmp_path: Path, demo_config: Path
    ) -> tuple[_StagingTransport, _ProvenanceTransport]:
        transport = _StagingTransport()
        prov = _ProvenanceTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(
            transport
        ), _patch_provenance_exporter(prov), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        return transport, prov

    def test_source_url_lives_in_raw_artifacts_not_aporte(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        transport, prov = self._run(tmp_path, demo_config)
        posts = [p for batch in transport.batch_posts for p in batch]
        assert posts
        # El aporte ya no lleva source_url.
        for post in posts:
            assert "source_url" not in post
        # La URL de origen quedo en el raw_artifact.
        assert prov.artifact_posts
        for art in prov.artifact_posts:
            assert art["source_url"] == self._PAGE_URL

    def test_normalizer_version_kept_legacy_dropped(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        transport, _ = self._run(tmp_path, demo_config)
        posts = [p for batch in transport.batch_posts for p in batch]
        assert posts
        for post in posts:
            assert post["normalizer_version"] == rp._PIPELINE_VERSION
            for legacy in ("run_id", "scraper_id", "source_url", "parser_version"):
                assert legacy not in post

    def test_meta_fields_not_leaked_into_raw_json(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        transport, _ = self._run(tmp_path, demo_config)
        for batch in transport.batch_posts:
            for post in batch:
                for key in (
                    "_source_url", "_parser_version", "_normalizer_version", "_artifact_id",
                ):
                    assert key not in post["raw_json"]


class TestApplyPiiSourceUrl:
    """Unit: meta-campos de _apply_pii / _enrich_records tras el cutover #256.

    `_source_url`/`_parser_version` dejaron de poblarse (cableado muerto: el
    exporter ya no los emite; la URL vive en raw_artifacts). `_normalizer_version`
    sigue vivo (columna canónica del aporte).
    """

    def _person(self) -> Person:
        return Person(
            full_name="JUAN DEMO",
            event_id=_EVENT_ID,
            status="missing",
            fuente="demo",
        )

    def _source(self) -> SourceConfig:
        return SourceConfig(
            id="demo_src",
            name="Demo",
            type="api_json",
            enabled=True,
            trust_tier="C",
            refresh_minutes=30,
            url="https://demo.example/api",
            parser_asignado="encuentralos",
        )

    def test_legacy_meta_fields_no_longer_set(self) -> None:
        # #256: _apply_pii ya no propaga _source_url ni _parser_version.
        recs = rp._apply_pii([self._person()], [], self._source(), [])
        assert "_source_url" not in recs[0]
        assert "_parser_version" not in recs[0]

    def test_enrich_sets_normalizer_version(self) -> None:
        recs = rp._apply_pii([self._person()], [], self._source(), [])
        enriched = rp._enrich_records(recs, [])
        assert enriched[0]["_normalizer_version"] == rp._PIPELINE_VERSION

    def test_normalizer_version_set_even_if_location_raises(self) -> None:
        # Si normalize_location lanza, el registro cae al except pero debe
        # conservar _normalizer_version (trazabilidad, issue #236).
        rec = {"full_name": "JUAN DEMO", "last_known_location": "Caracas"}
        errors: list[str] = []
        with patch.object(
            rp, "normalize_location", side_effect=ValueError("boom")
        ):
            enriched = rp._enrich_records([rec], errors)
        assert enriched[0]["_normalizer_version"] == rp._PIPELINE_VERSION
        assert errors  # el error se registro, no se descarto en silencio


# ---------------------------------------------------------------------------
# Tests: idempotencia
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_same_external_ids(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        for _ in range(2):
            with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
                "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
            ), patch(
                "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
            ):
                run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        # PostgREST merge-duplicates absorbe re-envios sin 409.
        # Idempotencia garantizada por ON CONFLICT en external_id.
        total = sum(len(b) for b in transport.batch_posts)
        assert total == 4


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
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser(persons)
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        all_posts = [p for batch in transport.batch_posts for p in batch]
        by_name = {p["raw_json"]["full_name"]: p for p in all_posts}
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
               if k not in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_INGEST_JWT")}
        with patch.dict(os.environ, env, clear=True), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert summary["staging_sent"] == 0
        assert summary["errors"] == []
        assert transport.batch_posts == []


# ---------------------------------------------------------------------------
# Tests: watermark
# ---------------------------------------------------------------------------

class TestWatermarkEndToEnd:
    def test_watermark_advances_on_success(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport(aportes_status=201)
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert transport.watermark_posts
        # fetched_at del mock menos el margen de seguridad de 5 minutos.
        assert transport.watermark_posts[-1]["watermark_at"] == "2026-06-24T15:25:00Z"

    def test_watermark_not_advanced_on_failure(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport(aportes_status=500)
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.adapters._shared.time.sleep", lambda *_: None
        ), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert transport.watermark_posts == []
        assert summary["staging_errors"] >= 1

    def test_watermark_passed_as_updated_after_to_adapter_fetch(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        """El watermark persistido se lee ANTES del fetch y llega al adapter."""

        class _PersistedWatermarkTransport(_StagingTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/sources" and request.method == "GET":
                    return httpx.Response(200, json=[{"watermark_at": "2026-06-01T00:00:00Z"}])
                return super().handle_request(request)

        transport = _PersistedWatermarkTransport()
        adapter = _mock_adapter()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        _, kwargs = adapter.fetch_all.call_args
        assert kwargs["params"] == {"updated_after": "2026-06-01T00:00:00Z"}

    def test_two_sources_get_independent_watermarks(self, tmp_path: Path) -> None:
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: fuente_a
    name: Fuente A
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://fuente-a.test/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
  - id: fuente_b
    name: Fuente B
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://fuente-b.test/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
""")
        transport = _StagingTransport(aportes_status=201)
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", side_effect=lambda *_: _mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", side_effect=lambda *_: _mock_parser()
        ):
            run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        source_ids = {p["source_id"] for p in transport.watermark_posts}
        assert source_ids == {"fuente_a", "fuente_b"}

    def test_full_scan_omits_updated_after(self, tmp_path: Path) -> None:
        """full_scan=True → updated_after no llega al adapter."""
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos full scan
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
    full_scan: true
""")
        transport = _StagingTransport()
        adapter = _mock_adapter()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        _, kwargs = adapter.fetch_all.call_args
        assert "updated_after" not in kwargs.get("params", {})

    def test_full_scan_false_still_passes_updated_after(self, tmp_path: Path) -> None:
        """full_scan=False (explícito) → updated_after llega al adapter."""
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos incremental
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
    full_scan: false
""")
        transport = _StagingTransport()
        adapter = _mock_adapter()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        _, kwargs = adapter.fetch_all.call_args
        assert "updated_after" in kwargs.get("params", {})


# ---------------------------------------------------------------------------
# Tests: paralelismo (max_workers)
# ---------------------------------------------------------------------------

class TestMaxWorkers:
    def _make_n_sources_config(self, tmp_path: Path, n: int) -> Path:
        sources_yaml = "\n".join(
            f"""  - id: fuente_{i}
    name: Fuente {i}
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://fuente-{i}.test/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos"""
            for i in range(n)
        )
        return _make_demo_config(tmp_path, f"""project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
{sources_yaml}
""")

    @staticmethod
    def _unique_persons(suffix: str) -> list[Person]:
        """Personas con nombre/ubicacion distintos por fuente para que el
        external_id (por-registro-de-fuente: source_slug + content_hash) no
        coincida entre fuentes — asi cada fuente aporta filas propias y el
        test mide throughput agregado de fuentes realmente distintas, sin que
        el upsert por (source_id, external_id) las colapse.
        """
        return [
            Person(
                full_name=f"PERSONA UNO {suffix}",
                event_id=_EVENT_ID,
                status="missing",
                fuente=f"fuente_{suffix}",
                last_known_location=f"Lara{suffix}, Venezuela",
            ),
            Person(
                full_name=f"PERSONA DOS {suffix}",
                event_id=_EVENT_ID,
                status="deceased",
                fuente=f"fuente_{suffix}",
                last_known_location=f"Zulia{suffix}, Venezuela",
            ),
        ]

    def _parser_for(self, source: Any, *_: Any) -> Any:
        suffix = source.id.rsplit("_", 1)[-1]
        return _mock_parser(self._unique_persons(suffix))

    def test_five_sources_parallel_same_result_as_sequential(self, tmp_path: Path) -> None:
        cfg = self._make_n_sources_config(tmp_path, 5)
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", side_effect=lambda *_: _mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", side_effect=self._parser_for
        ):
            summary = run_pipeline(
                config_path=cfg, output_dir=tmp_path / "out", max_workers=5
            )
        assert summary["sources_processed"] == 5
        assert summary["staging_sent"] == 10  # 2 personas x 5 fuentes
        assert summary["errors"] == []
        source_ids = {p["source_id"] for p in transport.watermark_posts}
        assert source_ids == {f"fuente_{i}" for i in range(5)}

    def test_max_workers_one_is_sequential_default(self, tmp_path: Path) -> None:
        cfg = self._make_n_sources_config(tmp_path, 5)
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", side_effect=lambda *_: _mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", side_effect=self._parser_for
        ):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 5
        assert summary["staging_sent"] == 10

    def test_one_source_fatal_error_does_not_block_others_in_parallel(
        self, tmp_path: Path
    ) -> None:
        cfg = self._make_n_sources_config(tmp_path, 5)
        transport = _StagingTransport()

        def _flaky_adapter(source: Any, *_: Any) -> Any:
            if source.id == "fuente_2":
                raise RuntimeError("adapter explota")
            return _mock_adapter()

        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", side_effect=_flaky_adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", side_effect=self._parser_for
        ):
            summary = run_pipeline(
                config_path=cfg, output_dir=tmp_path / "out", max_workers=5
            )
        assert summary["sources_processed"] == 4
        assert any("fuente_2" in e for e in summary["errors"])
        assert summary["staging_sent"] == 8  # 2 personas x 4 fuentes ok


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
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport):
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
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        # La fuente se procesa sin error fatal pero no envia nada (parser None).
        assert summary["sources_processed"] == 1
        assert summary["staging_sent"] == 0
        assert transport.batch_posts == []

    def test_repo_demo_config_processes_synthetic_record(self, tmp_path: Path) -> None:
        """El quickstart debe procesar el fixture sintético, no solo validar YAML."""
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport):
            summary = run_pipeline(
                config_path=Path("scrapers/config/sources.demo.yaml"),
                output_dir=tmp_path / "out",
            )
        assert summary["errors"] == []
        assert summary["sources_processed"] == 1
        assert summary["staging_sent"] == 1
        all_posts = [p for batch in transport.batch_posts for p in batch]
        assert len(all_posts) == 1
        assert all_posts[0]["raw_json"]["full_name"] == "Juan Demo"

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
        qtransport = _QuarantineTransport()
        with patch.dict(
            os.environ, {**_SUPABASE_ENV, **_QUARANTINE_ENV}, clear=False
        ), _patch_exporter(transport), _patch_quarantine_exporter(qtransport):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        # Visible en el resumen.
        omissions = [
            e for e in summary["errors"]
            if "parser no implementado" in e and "cuarentena" in e
        ]
        assert len(omissions) == 1
        assert "parser_inexistente" in omissions[0]
        assert "fuente_sin_parser" in omissions[0]
        assert summary["staging_errors"] >= 1

    def test_fetch_error_does_not_crash_pipeline(self, tmp_path: Path, demo_config: Path) -> None:
        adapter = _mock_adapter()
        adapter.fetch_all.side_effect = RuntimeError("fetch agotado tras reintentos")
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        adapter.close.assert_called_once()
        assert summary["sources_processed"] == 0
        assert len(summary["errors"]) == 1


# ---------------------------------------------------------------------------
# Tests: page_size por fuente (api_json)
# ---------------------------------------------------------------------------

class TestApiAdapterPageSize:
    def test_custom_page_size_is_passed_to_adapter(self) -> None:
        source = SourceConfig(
            id="api_custom_page_size",
            name="API con page_size custom",
            type="api_json",
            enabled=True,
            trust_tier="C",
            url="https://example.org/api/personas",
            refresh_minutes=30,
            parser_asignado="encuentralos",
            page_size=500,
        )
        adapter = _get_adapter(source)
        try:
            assert adapter.page_size == 500
        finally:
            adapter.close()

    def test_no_page_size_uses_adapter_default(self) -> None:
        source = SourceConfig(
            id="api_sin_page_size",
            name="API sin page_size declarado",
            type="api_json",
            enabled=True,
            trust_tier="C",
            url="https://example.org/api/personas",
            refresh_minutes=30,
            parser_asignado="encuentralos",
        )
        adapter = _get_adapter(source)
        try:
            from scrapers.adapters.api_adapter import _DEFAULT_PAGE_SIZE
            assert adapter.page_size == _DEFAULT_PAGE_SIZE
        finally:
            adapter.close()


# ---------------------------------------------------------------------------
# Tests: limite por fuente
# ---------------------------------------------------------------------------

class TestLimit:
    def test_limit_zero_sends_nothing(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out", limit=0)
        assert summary["staging_sent"] == 0

    def test_limit_one_sends_at_most_one(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out", limit=1)
        all_posts = [p for batch in transport.batch_posts for p in batch]
        assert len(all_posts) <= 1


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
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser(persons)
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        all_posts = [p for batch in transport.batch_posts for p in batch]
        assert len(all_posts) == 1
        data = all_posts[0]["raw_json"]
        assert data["foto"] is None
        assert data["cedula_masked"] is None
        assert data["last_known_location"] == "Lara"

    def test_minor_record_quarantined_when_protection_raises(self, tmp_path: Path, demo_config: Path) -> None:
        """Si la proteccion de menores falla, el registro NO se exporta a staging
        (fail-closed) pero TAMPOCO se descarta: va a cuarentena con riesgo alto
        para redaccion manual (Issue #88)."""
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
        qtransport = _QuarantineTransport()
        with patch.dict(
            os.environ, {**_SUPABASE_ENV, **_QUARANTINE_ENV}, clear=False
        ), _patch_exporter(transport), _patch_quarantine_exporter(qtransport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser(persons)
        ), patch(
            "scrapers.pipelines.run_pipeline.protect_minor_fields", side_effect=ValueError("boom")
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        # Fail-closed: nada del menor llega a staging.
        assert transport.batch_posts == []
        assert any("registro omitido" in e for e in summary["errors"])
        assert len(qtransport.posts) >= 1
        assert qtransport.posts[0]["risk_level"] == "high"


# ---------------------------------------------------------------------------
# Tests: PII_SALT
# ---------------------------------------------------------------------------

class TestPIISalt:
    def test_pipeline_works_with_pii_salt(self, tmp_path: Path, demo_config: Path) -> None:
        transport = _StagingTransport()
        env = {**_SUPABASE_ENV, "PII_SALT": "test-salt-pipeline"}
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
            client = httpx.Client(base_url=config.supabase_url, transport=transport)
            return StagingExporter(config, client=client, run_id=run_id)

        # Fuente deshabilitada -> dry-run efectivo -> no debe tocar el transport.
        cfg = _make_demo_config(tmp_path, """
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources: []
""")
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), patch.object(
            rp, "StagingExporter", side_effect=_factory
        ):
            summary = run_pipeline(config_path=cfg, output_dir=tmp_path / "out")
        assert summary["sources_processed"] == 0


# ---------------------------------------------------------------------------
# Domain allowlist en _run_source (issue #132)
# ---------------------------------------------------------------------------

def _api_source(url: str, **kw: Any) -> SourceConfig:
    return SourceConfig(
        id="test_src",
        name="Test",
        type="api_json",
        enabled=True,
        trust_tier="C",
        url=url,
        refresh_minutes=30,
        parser_asignado="encuentralos",
        **kw,
    )


class TestDomainAllowlist:
    def test_blocks_disallowed_domain_without_fetching(self, monkeypatch):
        built: list[str] = []
        quarantine_batch: list[QuarantineRecord] = []
        monkeypatch.setattr(rp, "_get_adapter", lambda s: built.append(s.id))
        source = _api_source(
            "https://evil.example.com/api",
            allowed_domains=["encuentralos.tecnosoft.dev"],
        )
        all_errors: list[str] = []


        result = rp._run_source(
            source, None, all_errors, _EVENT_ID, MagicMock(), quarantine_batch, _dry_provenance()
        )

        # Nunca se intentó construir el adapter → ningún request.
        assert built == []
        assert result.sent == 0
        assert any("dominio no permitido" in e for e in result.errors)
        # El error queda visible en el summary global.
        assert any("evil.example.com" in e for e in all_errors)

    def test_allows_matching_domain_case_insensitive(self, monkeypatch):
        built: list[str] = []
        quarantine_batch: list[QuarantineRecord] = []
        def fake_adapter(s):
            built.append(s.id)
            return None  # corta limpio tras pasar el gate de dominio

        monkeypatch.setattr(rp, "_get_adapter", fake_adapter)
        source = _api_source(
            "https://encuentralos.tecnosoft.dev/api/personas",
            allowed_domains=["Encuentralos.Tecnosoft.Dev"],  # mayúsculas
        )

        result = rp._run_source(
            source, None, [], _EVENT_ID, MagicMock(), quarantine_batch, _dry_provenance()
        )

        assert built == ["test_src"]  # pasó el gate, intentó construir adapter
        assert not any("dominio no permitido" in e for e in result.errors)

    def test_no_allowed_domains_is_unrestricted(self, monkeypatch):
        built: list[str] = []
        monkeypatch.setattr(rp, "_get_adapter", lambda s: built.append(s.id))
        source = _api_source("https://anything.example.org/api")  # sin allowlist
        quarantine_batch: list[QuarantineRecord] = []
        rp._run_source(
            source, None, [], _EVENT_ID, MagicMock(), quarantine_batch, _dry_provenance()
        )

        # Comportamiento retrocompatible: pasa el gate como hoy.
        assert built == ["test_src"]


# ---------------------------------------------------------------------------
# Tests: cursor_field early-stop incremental
# ---------------------------------------------------------------------------

def _raw_page(items: list[dict], *, page_num: int, fetched_at: str = "2026-07-03T12:00:00Z") -> RawContent:
    return RawContent(
        source_key="encuentralos_tecnosoft",
        source_url="https://encuentralos.tecnosoft.dev/api/personas",
        fetched_at=fetched_at,
        http_status=200,
        content_type="application/json",
        content_hash=f"sha256:page{page_num}",
        raw_content={"items": items, "total": 100},
        page=page_num,
        total_pages=None,
        offset=(page_num - 1) * len(items),
        limit=len(items),
        records_in_page=len(items),
    )


def _item(rec_id: str, creado: str) -> dict:
    return {"id": rec_id, "nombre": f"Persona {rec_id}", "estado": "desaparecido", "creado": creado}


class _WatermarkTransport(_StagingTransport):
    """StagingTransport que devuelve un watermark inicial concreto en GET."""

    def __init__(self, watermark_at: str) -> None:
        super().__init__()
        self._initial_watermark = watermark_at

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/v1/sources" and request.method == "GET":
            return httpx.Response(200, json=[{"watermark_at": self._initial_watermark}])
        return super().handle_request(request)


_CURSOR_CONFIG_YAML = """
project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: encuentralos_tecnosoft
    name: Encuentralos cursor
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
    full_scan: true
    cursor_field: "creado"
"""


class TestCursorFieldEarlyStop:
    """cursor_field habilita early-stop client-side y avanza watermark con max(cursor_field)."""

    def test_early_stop_halts_paging_when_page_is_stale(self, tmp_path: Path) -> None:
        """Cuando min(creado de la página) ≤ watermark, no se consumen más páginas."""
        watermark = "2026-07-03T10:00:00Z"
        page1 = _raw_page([
            _item("a", "2026-07-03T12:00:00Z"),  # nuevo
            _item("b", "2026-07-03T09:00:00Z"),  # viejo — min(creado) ≤ watermark → stop
        ], page_num=1)
        page2 = _raw_page([_item("c", "2026-07-03T08:00:00Z")], page_num=2)

        pages_yielded: list[int] = []

        class _TrackingAdapter:
            default_path = "/api/personas"
            def fetch_all(self, *a, **kw):
                for p in [page1, page2]:
                    pages_yielded.append(p.get("page"))
                    yield p
            def close(self): pass

        cfg = _make_demo_config(tmp_path, _CURSOR_CONFIG_YAML)
        transport = _WatermarkTransport(watermark)
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_TrackingAdapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=cfg, output_dir=tmp_path / "out")

        assert pages_yielded == [1], f"esperado [1], obtenido {pages_yielded}"

    def test_no_early_stop_when_watermark_is_epoch(self, tmp_path: Path) -> None:
        """Con watermark=epoch (primer run), no hay early-stop: se consumen todas las páginas."""
        pages_yielded: list[int] = []

        class _TwoPagesAdapter:
            default_path = "/api/personas"
            def fetch_all(self, *a, **kw):
                for p in [
                    _raw_page([_item("a", "2026-07-03T12:00:00Z")], page_num=1),
                    _raw_page([_item("b", "2026-07-03T11:00:00Z")], page_num=2),
                ]:
                    pages_yielded.append(p.get("page"))
                    yield p
            def close(self): pass

        cfg = _make_demo_config(tmp_path, _CURSOR_CONFIG_YAML)
        transport = _WatermarkTransport("1970-01-01T00:00:00Z")
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_TwoPagesAdapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=cfg, output_dir=tmp_path / "out")

        assert pages_yielded == [1, 2], f"esperado [1, 2], obtenido {pages_yielded}"

    def test_cursor_watermark_is_max_creado_minus_safety_margin(self, tmp_path: Path) -> None:
        """El watermark almacenado es max(creado) - 5min, no fetched_at."""
        page = _raw_page(
            [_item("x", "2026-07-03T12:00:00Z"), _item("y", "2026-07-03T11:00:00Z")],
            page_num=1,
            fetched_at="2026-07-03T15:00:00Z",  # fetched_at mucho más tarde
        )

        class _OnePageAdapter:
            default_path = "/api/personas"
            def fetch_all(self, *a, **kw): yield page
            def close(self): pass

        cfg = _make_demo_config(tmp_path, _CURSOR_CONFIG_YAML)
        transport = _WatermarkTransport("1970-01-01T00:00:00Z")
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(transport), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_OnePageAdapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=cfg, output_dir=tmp_path / "out")

        assert transport.watermark_posts, "watermark no avanzó"
        new_wm = transport.watermark_posts[-1]["watermark_at"]
        # max(creado) = "2026-07-03T12:00:00Z" → menos 5 min = "2026-07-03T11:55:00Z"
        # NO "2026-07-03T14:55:00Z" (fetched_at - 5min)
        assert new_wm == "2026-07-03T11:55:00Z", f"watermark incorrecto: {new_wm}"


class TestExporterBatchingWiring:
    def test_run_source_passes_batch_size_to_exporter(self, monkeypatch):
        source = _api_source(
            "https://encuentralos.tecnosoft.dev/api/personas",
            bulk_size=32,
        )
        adapter = _mock_adapter()
        parser = _mock_parser()
        exporter = MagicMock()
        exporter.get_watermark.return_value = "1970-01-01T00:00:00Z"
        exporter.export_batch.return_value = rp.ExportResult(sent=2)
        exporter.advance_watermark.return_value = None

        monkeypatch.setattr(rp, "_get_adapter", lambda s: adapter)
        monkeypatch.setattr(rp, "_get_parser", lambda s, event_id: parser)

        result = rp._run_source(source, None, [], _EVENT_ID, exporter, [], _dry_provenance())

        assert result.sent == 2
        exporter.export_batch.assert_called_once()
        assert exporter.export_batch.call_args.kwargs["batch_size"] == 32


# ---------------------------------------------------------------------------
# Tests: log de cuarentena en finally (#228)
# ---------------------------------------------------------------------------


class TestQuarantineLogUsesSourceSlug:
    """El log del finally debe usar source_slug del batch, no la ultima fuente del loop."""

    def test_finally_log_uses_quarantine_source_slug_not_last_loop_source(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg = _make_demo_config(
            tmp_path,
            f"""project:
  event_id: {_EVENT_ID}
  default_country: Venezuela
sources:
  - id: src_a
    name: Source A
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/a"
    refresh_minutes: 30
    parser_asignado: encuentralos
  - id: src_b
    name: Source B
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://example.org/b"
    refresh_minutes: 30
    parser_asignado: encuentralos
""",
        )
        qrec = QuarantineRecord(
            source_slug="src_a",
            reason_code="invalid_schema",
            risk_level="medium",
        )

        def mock_process(
            source: SourceConfig,
            limit: int | None,
            all_errors: list[str],
            event_id: str,
            exporter: StagingExporter,
            provenance: ProvenanceExporter,
        ) -> tuple[rp.ExportResult, list[QuarantineRecord], bool]:
            if source.id == "src_a":
                return rp.ExportResult(), [qrec], True
            return rp.ExportResult(), [], True

        transport = _StagingTransport()
        qtransport = _QuarantineTransport()
        with patch.dict(
            os.environ, {**_SUPABASE_ENV, **_QUARANTINE_ENV}, clear=False
        ), _patch_exporter(transport), _patch_quarantine_exporter(qtransport), patch.object(
            rp, "_process_source_safe", side_effect=mock_process
        ):
            with caplog.at_level("INFO", logger="scrapers.pipelines.run_pipeline"):
                run_pipeline(config_path=cfg, output_dir=tmp_path / "out")

        quarantine_logs = [
            r.getMessage()
            for r in caplog.records
            if "cuarentena" in r.getMessage().lower()
        ]
        assert any("src_a" in msg for msg in quarantine_logs)
        assert not any("src_b" in msg for msg in quarantine_logs)


# ---------------------------------------------------------------------------
# Tests: Bronze provenance end-to-end (issue #256)
# ---------------------------------------------------------------------------


class TestBronzeProvenance:
    """El pipeline escribe scrape_runs + raw_artifacts y linkea cada aporte.

    Verifica los criterios de aceptacion de #256: una corrida por fuente, un
    raw_artifact APPEND-ONLY por pagina, artifact_id en cada aporte, y el
    fail-closed que retiene el watermark si la procedencia falla.
    """

    def _run(
        self, tmp_path: Path, demo_config: Path, prov: _ProvenanceTransport
    ) -> _StagingTransport:
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(
            transport
        ), _patch_provenance_exporter(prov), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        return transport

    def test_one_run_and_one_artifact_per_page(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        prov = _ProvenanceTransport()
        self._run(tmp_path, demo_config, prov)
        assert len(prov.run_posts) == 1  # una scrape_run por fuente
        assert len(prov.artifact_posts) == 1  # una pagina => un raw_artifact

    def test_artifact_carries_body_hash_and_raw_text(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        prov = _ProvenanceTransport()
        self._run(tmp_path, demo_config, prov)
        art = prov.artifact_posts[0]
        # body_hash es el content_hash de la pagina (pass-through del adapter).
        assert art["body_hash"] == "sha256:abc"
        assert art["run_id"] == "run-1"
        assert art["raw_text"]  # el contenido crudo va como texto (PII en reposo)
        assert art["http_status"] == 200

    def test_aporte_artifact_id_matches_returned_id(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        prov = _ProvenanceTransport()
        transport = self._run(tmp_path, demo_config, prov)
        posts = [p for batch in transport.batch_posts for p in batch]
        assert posts
        for post in posts:
            assert post["artifact_id"] == "art-1"

    def test_append_only_two_invocations_two_runs_two_artifacts(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        prov = _ProvenanceTransport()
        # Dos corridas contra el mismo transport: append-only => 2 runs, 2 artifacts
        # para la misma pagina (mismo body_hash), sin upsert/on_conflict.
        self._run(tmp_path, demo_config, prov)
        self._run(tmp_path, demo_config, prov)
        assert len(prov.run_posts) == 2
        assert len(prov.artifact_posts) == 2
        assert prov.artifact_posts[0]["body_hash"] == prov.artifact_posts[1]["body_hash"]
        assert all("on_conflict" not in q for q in prov.artifact_queries)

    def test_scrape_run_posts_source_id_directly(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        # source.id (config) ES el source_id que va a scrape_runs, sin resolver slug.
        prov = _ProvenanceTransport()
        self._run(tmp_path, demo_config, prov)
        assert prov.run_posts[0]["source_id"] == "encuentralos_tecnosoft"

    def test_scrape_run_posts_source_id_thin_format(self, tmp_path: Path) -> None:
        # Cuando source.id ya tiene formato UUID (producción real), se usa
        # directamente como scrape_runs.source_id sin ninguna resolución.
        # Cubre el path thin que demo_config (slug) no ejercita.
        uuid_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        cfg = _make_demo_config(
            tmp_path,
            f"""project:
  event_id: 8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a
  default_country: Venezuela
sources:
  - id: {uuid_id}
    name: Encuentralos tecnosoft
    type: api_json
    enabled: true
    trust_tier: C
    url: "https://encuentralos.tecnosoft.dev/api/personas"
    refresh_minutes: 30
    parser_asignado: encuentralos
""",
        )
        prov = _ProvenanceTransport()
        self._run(tmp_path, cfg, prov)
        assert prov.run_posts[0]["source_id"] == uuid_id

    def test_provenance_failure_blocks_export_and_watermark(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        # raw_artifacts falla (500): fail-closed. No se exporta ningun aporte de esa
        # pagina (no puede existir sin su raw_artifact) y el watermark NO avanza
        # (re-fetch la proxima corrida). Esto evita perdida silenciosa (#256 B1).
        prov = _ProvenanceTransport(artifacts_status=500)
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(
            transport
        ), _patch_provenance_exporter(prov), patch(
            "scrapers.adapters._shared.time.sleep", lambda *_: None
        ), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_mock_adapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        assert transport.batch_posts == []  # ningun aporte exportado
        assert transport.watermark_posts == []  # watermark retenido
        assert summary["staging_sent"] == 0
        assert summary["staging_errors"] >= 1

    def test_multipage_artifact_failure_retains_whole_source_watermark(
        self, tmp_path: Path, demo_config: Path
    ) -> None:
        # B1 (#256): página 1 registra su artifact y EXPORTA (staging_sent>0);
        # página 2 falla su artifact (500). El watermark de la FUENTE ENTERA no
        # avanza, vía el gate de source_errors en advance_watermark, NO vía el
        # early-return de entities==0 (que aquí no aplica porque sí hubo export).
        # Un solo timestamp de watermark no puede expresar "todas menos la 2".
        class _TwoPageAdapter:
            default_path = "/api/personas"

            def fetch_all(self, *a: Any, **kw: Any) -> Any:
                yield _encuentralos_raw([{"id": 1}])
                yield _encuentralos_raw([{"id": 2}])

            def close(self) -> None:
                pass

        class _Prov1Ok2Fail(_ProvenanceTransport):
            """raw_artifacts: 201 para la 1a página, 500 para la 2a en adelante."""

            def __init__(self) -> None:
                super().__init__()
                self._art_calls = 0

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/raw_artifacts":
                    self._art_calls += 1
                    if self._art_calls == 1:
                        return super().handle_request(request)
                    self.artifact_posts.append(json.loads(request.content))
                    return httpx.Response(500, json={})
                return super().handle_request(request)

        prov = _Prov1Ok2Fail()
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(
            transport
        ), _patch_provenance_exporter(prov), patch(
            "scrapers.adapters._shared.time.sleep", lambda *_: None
        ), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=_TwoPageAdapter()
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            summary = run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        # La página 1 SÍ exportó: prueba que llegamos al gate de source_errors, no
        # al early-return de entities==0.
        assert summary["staging_sent"] == 2
        # Watermark de la fuente entera retenido pese a sent>0 (fail-closed B1).
        assert transport.watermark_posts == []
        assert summary["staging_errors"] >= 1

    def test_raw_text_never_logged_on_failure(
        self, tmp_path: Path, demo_config: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # El adapter falso inyecta un marcador PII en el contenido crudo; ante un
        # fallo del INSERT de raw_artifacts, el exporter NUNCA debe loguearlo.
        marker = "PII-MARKER-NO-DEBE-APARECER-EN-LOGS"
        adapter = _mock_adapter([{"id": 1, "nota": marker}])
        prov = _ProvenanceTransport(artifacts_status=500)
        transport = _StagingTransport()
        with patch.dict(os.environ, _SUPABASE_ENV, clear=False), _patch_exporter(
            transport
        ), _patch_provenance_exporter(prov), patch(
            "scrapers.adapters._shared.time.sleep", lambda *_: None
        ), patch(
            "scrapers.pipelines.run_pipeline._get_adapter", return_value=adapter
        ), patch(
            "scrapers.pipelines.run_pipeline._get_parser", return_value=_mock_parser()
        ):
            with caplog.at_level("DEBUG"):
                run_pipeline(config_path=demo_config, output_dir=tmp_path / "out")
        for rec in caplog.records:
            assert marker not in rec.getMessage()


# ---------------------------------------------------------------------------
# PII cruda en `unmapped` -> cuarentena (regresion: reason_code valido)
# ---------------------------------------------------------------------------


class TestUnmappedPiiQuarantine:
    """`_check_unmapped_pii` debe enrutar a cuarentena con un reason_code que el
    exporter acepte. Un reason_code fuera de REASON_CODES haria que
    ``QuarantineRecord.validate()`` lanzara y el registro se perdiera en
    silencio (viola "nada se descarta en silencio")."""

    def test_raw_pii_in_unmapped_is_quarantined_with_valid_reason_code(self) -> None:
        source = _api_source("https://example.org/x")
        errors: list[str] = []
        quarantine_batch: list[QuarantineRecord] = []
        rec = {"nombre": "REDACTED", "unmapped": {"tel": "0414-1234567"}}

        clean = rp._check_unmapped_pii([rec], errors, source, quarantine_batch)

        # El registro con PII cruda NO pasa a staging.
        assert clean == []
        assert len(quarantine_batch) == 1
        qrec = quarantine_batch[0]
        assert qrec.reason_code == "pii_untreatable"
        assert qrec.risk_level == "high"
        # Guarda de regresion: el registro debe ser aceptable para el exporter,
        # de lo contrario quarantine_many lo convertiria en error y se perderia.
        qrec.validate()  # no debe lanzar
        assert qrec.reason_code in QUARANTINE_REASON_CODES
        # La traza del campo `unmapped` se conserva en el detalle.
        assert "unmapped" in (qrec.reason_detail or "")
        # El preview no filtra el telefono en claro.
        assert "0414-1234567" not in (qrec.payload_preview_redacted or "")

    def test_clean_unmapped_passes_through(self) -> None:
        source = _api_source("https://example.org/x")
        errors: list[str] = []
        quarantine_batch: list[QuarantineRecord] = []
        rec = {"nombre": "REDACTED", "unmapped": {"color_ojos": "cafe"}}

        clean = rp._check_unmapped_pii([rec], errors, source, quarantine_batch)

        assert clean == [rec]
        assert quarantine_batch == []


# ---------------------------------------------------------------------------
# _cmd_materialize desacoplado de la resolucion de fuentes (thin -> DB)
# ---------------------------------------------------------------------------


class TestMaterializeDecoupledFromSources:
    """El materializer solo necesita ``project.event_id`` (constante del YAML),
    nunca la definicion de las fuentes. Debe correr aunque la resolucion de las
    fuentes thin fallaria (config thin sin env SUPABASE_*): antes, un solo grant
    faltante en ``sources`` tumbaba toda la proyeccion via ``load_sources``."""

    _THIN_CONFIG = f"""project:
  event_id: {_EVENT_ID}
  default_country: Venezuela
sources:
  - id: a1b2c3d4-e5f6-7890-abcd-ef1234567890
    parser_asignado: encuentralos
    enabled: true
"""

    def test_materialize_runs_when_sources_resolution_would_fail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        import argparse

        from scrapers import cli

        # Sin env SUPABASE_*: load_sources fallaria cerrado al resolver la fuente
        # thin contra la DB. Confirmamos primero esa premisa.
        for key in _SUPABASE_ENV:
            monkeypatch.delenv(key, raising=False)
        cfg = _make_demo_config(tmp_path, self._THIN_CONFIG)
        with pytest.raises(ValueError):
            cli.load_sources(cfg)

        mock_result = MagicMock()
        mock_result.persons_projected = 0
        mock_result.acopio_projected = 0
        mock_result.events_seeded = 0
        mock_result.events_skipped = 0
        mock_result.errors = []
        mock_cls = MagicMock()
        instance = mock_cls.return_value.__enter__.return_value
        instance.materialize.return_value = mock_result

        args = argparse.Namespace(config=str(cfg))
        with patch("scrapers.jobs.materializer.SilverMaterializer", mock_cls):
            cli._cmd_materialize(args)

        # El materializer se ejecuto con el event_id del YAML: no hubo return
        # temprano por fallo de resolucion de fuentes.
        instance.materialize.assert_called_once_with(event_id=_EVENT_ID)
        assert "no se pudo leer project.event_id" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# PDF sin texto extraible -> cuarentena `pdf_no_text` (no se descarta en silencio)
# ---------------------------------------------------------------------------


class _RaisingAdapter:
    """Adapter cuyo `fetch_all` lanza durante la iteracion (streaming lazy)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def fetch_all(self, path: str, **kwargs: Any):
        yield from ()  # no emite ninguna pagina...
        raise self._exc  # ...y falla en el primer next(), como el PdfAdapter real


class _OkAdapter:
    def fetch_all(self, path: str, **kwargs: Any):
        yield {"page": 1, "raw_content": "x"}
        yield {"page": 2, "raw_content": "y"}


def _pdf_source() -> SourceConfig:
    return SourceConfig(
        id="pdf_src",
        name="PDF",
        type="pdf",
        enabled=True,
        trust_tier="C",
        url="https://example.org/scan.pdf",
        refresh_minutes=30,
        parser_asignado="encuentralos",
    )


class TestPdfNoTextQuarantine:
    """Un PDF escaneado/imagen (sin texto extraible) no se pierde: va a cuarentena
    con reason_code `pdf_no_text` para OCR/revision humana. Antes se descartaba en
    silencio (la excepcion solo quedaba en un log)."""

    def test_pdf_without_text_is_quarantined(self) -> None:
        source = _pdf_source()
        exc = PdfTextExtractionError(
            "PDF has no extractable text; OCR is required: https://example.org/scan.pdf",
            source_url="https://example.org/scan.pdf",
            content_hash="a" * 64,
        )
        quarantine_batch: list[QuarantineRecord] = []
        errors: list[str] = []

        pages = list(
            rp._fetch_pages_or_quarantine(
                _RaisingAdapter(exc), source, "2026-01-01T00:00:00Z",
                quarantine_batch, errors,
            )
        )

        assert pages == []  # no se emitio ninguna pagina
        assert len(quarantine_batch) == 1
        qrec = quarantine_batch[0]
        assert qrec.reason_code == "pdf_no_text"
        assert qrec.reason_code in QUARANTINE_REASON_CODES
        assert qrec.risk_level == "medium"
        assert qrec.source_slug == "pdf_src"
        assert qrec.source_url == "https://example.org/scan.pdf"
        # payload_hash = SHA-256 de los bytes del PDF: prueba que ESE archivo se vio.
        assert qrec.payload_hash == "a" * 64
        assert qrec.payload_preview_redacted is None  # no hay texto que previsualizar
        # Regresion: el exporter debe aceptarlo, si no quarantine_many lo perderia.
        qrec.validate()  # no debe lanzar
        assert errors and any("pdf_no_text" in e for e in errors)

    def test_non_pdf_error_is_reraised(self) -> None:
        source = _pdf_source()
        quarantine_batch: list[QuarantineRecord] = []
        with pytest.raises(RuntimeError, match="boom"):
            list(
                rp._fetch_pages_or_quarantine(
                    _RaisingAdapter(RuntimeError("boom")), source,
                    "2026-01-01T00:00:00Z", quarantine_batch, [],
                )
            )
        # Otras fallas (descarga, red) NO se cuarentenan aqui: las maneja
        # _process_source_safe reteniendo el watermark para re-fetch.
        assert quarantine_batch == []

    def test_pages_pass_through_when_no_error(self) -> None:
        source = _pdf_source()
        quarantine_batch: list[QuarantineRecord] = []
        pages = list(
            rp._fetch_pages_or_quarantine(
                _OkAdapter(), source, "2026-01-01T00:00:00Z", quarantine_batch, [],
            )
        )
        assert [p["page"] for p in pages] == [1, 2]
        assert quarantine_batch == []
