"""Tests para los subcomandos CLI: list-enabled, ingest, consolidate."""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

import pytest

_DEMO_CONFIG = Path("scrapers/config/sources.demo.yaml")
# Synthetic full-format fixture (no real source identity, ADR 0009).
_SAMPLE_CONFIG = Path("scrapers/tests/fixtures/sources.sample.yaml")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "scrapers.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ── list-enabled ──────────────────────────────────────────────────


class TestListEnabled:
    def test_lists_enabled_sources(self) -> None:
        result = _run_cli("list-enabled", "--config", str(_DEMO_CONFIG))
        assert result.returncode == 0
        assert "demo_manual_synthetic" in result.stdout

    def test_json_output_is_valid_array(self) -> None:
        result = _run_cli("list-enabled", "--config", str(_DEMO_CONFIG), "--json")
        assert result.returncode == 0
        ids = json.loads(result.stdout)
        assert isinstance(ids, list)
        assert "demo_manual_synthetic" in ids

    def test_sample_config_lists_enabled_only(self) -> None:
        result = _run_cli("list-enabled", "--config", str(_SAMPLE_CONFIG), "--json")
        assert result.returncode == 0
        ids = json.loads(result.stdout)
        # Only enabled sources appear
        for sid in ids:
            assert isinstance(sid, str)
        assert "sample_enabled_api" in ids
        # Disabled sources must not appear
        assert "sample_disabled_rss" not in ids
        assert "sample_disabled_html" not in ids


# ── ingest ────────────────────────────────────────────────────────


class TestIngest:
    def test_ingest_demo_source_succeeds_in_dry_run(self, tmp_path: Path) -> None:
        result = _run_cli(
            "ingest",
            "--config", str(_DEMO_CONFIG),
            "--source", "demo_manual_synthetic",
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["source_id"] == "demo_manual_synthetic"
        assert output["status"] == "ok"
        assert output["records_exported"] == 0
        assert output["errors"] == []

    def test_ingest_unknown_source_fails(self) -> None:
        result = _run_cli(
            "ingest",
            "--config", str(_DEMO_CONFIG),
            "--source", "nonexistent_source",
        )
        assert result.returncode != 0
        assert "no encontrada" in result.stderr

    def test_ingest_preserves_optional_source_fields(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """_cmd_ingest debe preservar opcionales en su YAML temporal real."""
        import scrapers.pipelines.run_pipeline as pipeline_module

        from scrapers.cli import _cmd_ingest
        from scrapers.sources.loader import load_sources

        config_path = tmp_path / "sources.yaml"
        config_path.write_text(
            """
project:
  event_id: test-event
  default_country: Venezuela
sources:
  - id: optional_api
    name: Optional API
    type: api_json
    enabled: false
    trust_tier: C
    url: https://example.org/api/items
    refresh_minutes: 60
    parser_asignado: encuentralos
    required_keywords:
      - agua
      - refugio
    notes: "Fuente sintetica para probar CLI ingest."
    timeout_seconds: 12.5
    max_retries: 3
    page_size: 50
    probe_limit: 1000
    max_concurrent_pages: 7
    max_concurrent_posts: 8
    allowed_domains:
      - example.org
    rate_limit_per_minute: 60
""",
            encoding="utf-8",
        )
        captured: dict[str, Any] = {}

        def fake_run_pipeline(
            config_path: Path,
            output_dir: Path,
            limit: int | None = None,
            max_workers: int = 1,
        ) -> dict[str, Any]:
            assert config_path.exists()
            _project, sources = load_sources(config_path)
            captured["source"] = sources[0]
            captured["config_path"] = config_path
            captured["output_dir"] = output_dir
            captured["limit"] = limit
            captured["max_workers"] = max_workers
            return {
                "sources_processed": 1,
                "staging_sent": 0,
                "staging_duplicates": 0,
                "staging_errors": 0,
                "errors": [],
            }

        monkeypatch.setattr(pipeline_module, "run_pipeline", fake_run_pipeline)

        _cmd_ingest(
            argparse.Namespace(
                config=str(config_path),
                source="optional_api",
                output_dir=str(tmp_path / "out"),
                limit=5,
            )
        )

        command_output = json.loads(capsys.readouterr().out)
        assert command_output["source_id"] == "optional_api"
        assert command_output["status"] == "ok"
        assert command_output["records_exported"] == 0
        assert command_output["errors"] == []
        captured_source = captured["source"]
        assert captured_source.id == "optional_api"
        assert captured_source.enabled is True
        assert captured_source.required_keywords == ["agua", "refugio"]
        assert captured_source.notes == "Fuente sintetica para probar CLI ingest."
        assert captured_source.timeout_seconds == 12.5
        assert captured_source.max_retries == 3
        assert captured_source.page_size == 50
        assert captured_source.probe_limit == 1000
        assert captured_source.max_concurrent_pages == 7
        assert captured_source.max_concurrent_posts == 8
        assert captured_source.allowed_domains == ["example.org"]
        assert captured_source.rate_limit_per_minute == 60
        assert captured["limit"] == 5
        assert not captured["config_path"].exists()

    def test_ingest_output_is_valid_json(self, tmp_path: Path) -> None:
        result = _run_cli(
            "ingest",
            "--config", str(_DEMO_CONFIG),
            "--source", "demo_manual_synthetic",
            "--output-dir", str(tmp_path),
        )
        output = json.loads(result.stdout)
        assert "source_id" in output
        assert "status" in output
        assert "records_exported" in output
        assert "errors" in output


# ── consolidate ───────────────────────────────────────────────────


class TestConsolidate:
    # Anclado a un fixture full-format propio (_SAMPLE_CONFIG) en vez del config
    # por defecto: el materializer solo necesita project.event_id, y una entrada
    # completa evita que un futuro cambio a formato thin haga que load_sources
    # lance ValueError (SUPABASE_* ausentes en CI), que _cmd_materialize traga
    # antes de imprimir "Materializer:", rompiendo el test sin mensaje obvio.
    #
    # Ninguno de estos tests setea SUPABASE_*, asi que las 3 etapas (materializer,
    # auto-merge Event/Acopio, candidatos Person) corren en su modo no-op/dry-run
    # implicito (FakeInMemoryAdapter / PersonConsolidationConfig.from_env() -> None):
    # cero red, cero writes, deterministico.
    def test_materializer_runs_as_first_stage(self, tmp_path: Path) -> None:
        # El materializer (etapa 1) corre siempre, antes de las etapas de
        # consolidacion; en dry-run (sin SUPABASE_*) es un no-op silencioso.
        result = _run_cli(
            "consolidate", "--config", str(_SAMPLE_CONFIG), "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        assert "Materializer:" in result.stdout

    def test_automerge_stages_run_for_event_and_acopio(self, tmp_path: Path) -> None:
        # Etapa 2: sin SUPABASE_*, build_port() cae a FakeInMemoryAdapter (vacio),
        # asi que cada entity_type reporta un summary en cero, sin tocar la red.
        result = _run_cli(
            "consolidate", "--config", str(_SAMPLE_CONFIG), "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        assert "Consolidation[Event]:" in result.stdout
        assert "Consolidation[AcopioCenter]:" in result.stdout

    def test_person_stage_skipped_without_credentials(self, tmp_path: Path) -> None:
        # Etapa 3: sin SUPABASE_*, PersonConsolidationConfig.from_env() es None,
        # asi que la etapa se omite explicitamente en vez de intentar un write.
        result = _run_cli(
            "consolidate", "--config", str(_SAMPLE_CONFIG), "--output-dir", str(tmp_path)
        )
        assert result.returncode == 0
        assert "Consolidation[Person]: sin credenciales Supabase, omitido" in result.stdout

    def test_dry_run_skips_person_stage_entirely(self, tmp_path: Path) -> None:
        # run_person_consolidation no soporta dry-run propio, asi que --dry-run
        # omite la etapa Person por completo (nunca llega a preguntar por
        # credenciales) en vez de arriesgar un write real pese al flag.
        result = _run_cli(
            "consolidate", "--dry-run",
            "--config", str(_SAMPLE_CONFIG),
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 0
        assert "Consolidation[Person]: omitido en --dry-run" in result.stdout
        assert "sin credenciales Supabase" not in result.stdout

    def test_dry_run_flag_accepted(self, tmp_path: Path) -> None:
        result = _run_cli(
            "consolidate", "--dry-run",
            "--config", str(_SAMPLE_CONFIG),
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 0

    def test_batch_size_flag_accepted(self, tmp_path: Path) -> None:
        result = _run_cli(
            "consolidate", "--batch-size", "10",
            "--config", str(_SAMPLE_CONFIG),
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 0


# ── existing commands still work ──────────────────────────────────


class TestBackwardCompat:
    def test_validate_command(self) -> None:
        result = _run_cli("validate", "--config", str(_DEMO_CONFIG))
        assert result.returncode == 0
        assert "OK" in result.stdout

    def test_run_command(self, tmp_path: Path) -> None:
        result = _run_cli(
            "run",
            "--config", str(_DEMO_CONFIG),
            "--output-dir", str(tmp_path),
        )
        assert result.returncode == 0
        assert "Pipeline finalizado" in result.stdout
