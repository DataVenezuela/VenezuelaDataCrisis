"""Tests para los subcomandos CLI: list-enabled, ingest, consolidate."""

from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from pathlib import Path

_DEMO_CONFIG = Path("scrapers/config/sources.demo.yaml")
_STARTER_CONFIG = Path("scrapers/config/sources.venezuela.starter.yaml")


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

    def test_starter_config_lists_enabled_only(self) -> None:
        result = _run_cli("list-enabled", "--config", str(_STARTER_CONFIG), "--json")
        assert result.returncode == 0
        ids = json.loads(result.stdout)
        # Only enabled sources appear
        for sid in ids:
            assert isinstance(sid, str)
        # Disabled sources must not appear
        assert "gdacs_rss" not in ids
        assert "copernicus_activation_page" not in ids


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

    def test_ingest_preserves_optional_source_fields(self, tmp_path: Path) -> None:
        """El YAML temporal generado por _cmd_ingest debe preservar todos los campos
        opcionales de SourceConfig (probe_limit, max_concurrent_posts, etc.)."""
        import dataclasses

        import yaml

        from scrapers.sources.loader import load_sources

        project, sources = load_sources(_STARTER_CONFIG)
        source = next(s for s in sources if s.id == "encuentralos_tecnosoft")

        # Reconstruir el YAML temporal igual que _cmd_ingest (post-fix)
        source_dict = dataclasses.asdict(source)
        source_dict["enabled"] = True
        single_config = {"project": project, "sources": [source_dict]}

        tmp_yaml = tmp_path / "test_config.yaml"
        tmp_yaml.write_text(yaml.safe_dump(single_config))

        # Recargar y verificar que los campos opcionales sobrevivieron
        _, reloaded = load_sources(tmp_yaml)
        assert len(reloaded) == 1
        reloaded_source = reloaded[0]

        assert reloaded_source.probe_limit == 1000
        assert reloaded_source.max_concurrent_pages == 32
        assert reloaded_source.max_concurrent_posts == 8
        assert reloaded_source.id == "encuentralos_tecnosoft"

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
    def test_consolidate_without_data(self, tmp_path: Path) -> None:
        result = _run_cli("consolidate", "--output-dir", str(tmp_path))
        assert result.returncode == 0
        assert "No hay" in result.stdout

    def test_consolidate_with_empty_events(self, tmp_path: Path) -> None:
        (tmp_path / "events.jsonl").write_text("")
        result = _run_cli("consolidate", "--output-dir", str(tmp_path))
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
