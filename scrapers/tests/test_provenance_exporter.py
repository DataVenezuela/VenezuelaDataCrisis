"""
scrapers/tests/test_provenance_exporter.py
============================================
Tests del ProvenanceExporter (Bronze: scrape_runs + raw_artifacts), 100% offline.

Ningun test hace red real: el httpx.Client se construye con un
``_RecordingTransport`` (subclase de httpx.BaseTransport) inyectado via el
parametro ``client`` del constructor. El transport responde a
/rest/v1/scrape_runs y /rest/v1/raw_artifacts (start_run recibe el source_id
UUID ya resuelto, sin GET a /rest/v1/sources).

El invariante de seguridad mas importante (issue #256 / ADR 0008):
``raw_artifacts.raw_text`` es el UNICO PII en claro en reposo del sistema. El
exporter NUNCA lo loguea: solo body_hash / page / http_status. Ver
``TestNoRawTextInLogs``.
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import httpx

from scrapers.exporters.provenance_exporter import ProvenanceExporter
from scrapers.exporters.staging_exporter import StagingConfig

_SOURCE_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_TEST_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2NyYXBlcl9pbmdlc3QifQ.test"

# Marcador de PII en claro usado por los tests de logging: si aparece en un log
# el exporter esta filtrando raw_text.
_PII_MARKER = "CEDULA-V-12345678-PII-EN-CLARO"


class _RecordingTransport(httpx.BaseTransport):
    """Captura POSTs a /rest/v1/scrape_runs y /rest/v1/raw_artifacts.

    Devuelve return=representation con un id secuencial por tabla, para que el
    exporter pueda leer run_id / artifact_id de la respuesta.
    """

    def __init__(self, *, runs_status: int = 201, artifacts_status: int = 201) -> None:
        self.runs_status = runs_status
        self.artifacts_status = artifacts_status
        self.run_posts: list[dict[str, Any]] = []
        self.run_patches: list[tuple[str, dict[str, Any]]] = []
        self.artifact_posts: list[dict[str, Any]] = []
        self.artifact_queries: list[str] = []
        self._run_seq = 0
        self._artifact_seq = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/v1/scrape_runs":
            if request.method == "PATCH":
                self.run_patches.append((str(request.url), json.loads(request.content)))
                return httpx.Response(200, json=[])
            self.run_posts.append(json.loads(request.content))
            self._run_seq += 1
            return httpx.Response(self.runs_status, json=[{"run_id": f"run-{self._run_seq}"}])
        if path == "/rest/v1/raw_artifacts":
            self.artifact_posts.append(json.loads(request.content))
            self.artifact_queries.append(str(request.url.query))
            self._artifact_seq += 1
            return httpx.Response(
                self.artifacts_status, json=[{"artifact_id": f"art-{self._artifact_seq}"}]
            )
        return httpx.Response(404)


def _exporter(transport: httpx.BaseTransport) -> ProvenanceExporter:
    cfg = StagingConfig(
        supabase_url="https://project.supabase.co",
        publishable_key="k",
        ingest_jwt=_TEST_JWT,
    )
    client = httpx.Client(base_url="https://project.supabase.co", transport=transport)
    return ProvenanceExporter(cfg, client=client)


def _page(
    *,
    page: int = 1,
    raw_content: Any = _PII_MARKER,
    content_hash: str = "a" * 64,
    http_status: int = 200,
    source_url: str = "https://demo.example/registro/1",
    fetched_at: str = "2026-06-24T15:30:00Z",
) -> dict[str, Any]:
    return {
        "source_key": "demo",
        "source_url": source_url,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "content_type": "text/plain",
        "content_hash": content_hash,
        "raw_content": raw_content,
        "page": page,
    }


# --- scrape_runs ------------------------------------------------------------


class TestStartRun:
    def test_posts_source_id_directly(self) -> None:
        # source_id ya viene resuelto (source.id = UUID de sources): start_run lo
        # POSTea tal cual a scrape_runs, sin un GET slug -> id previo.
        t = _RecordingTransport()
        _exporter(t).start_run(_SOURCE_UUID)
        assert t.run_posts
        assert t.run_posts[0]["source_id"] == _SOURCE_UUID

    def test_returns_run_id_from_representation(self) -> None:
        t = _RecordingTransport()
        assert _exporter(t).start_run(_SOURCE_UUID) == "run-1"

    def test_uses_return_representation(self) -> None:
        captured: dict[str, Any] = {}

        class _T(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/scrape_runs":
                    captured["prefer"] = request.headers.get("prefer", "")
                    return httpx.Response(201, json=[{"run_id": "run-x"}])
                return httpx.Response(404)

        _exporter(_T()).start_run(_SOURCE_UUID)
        assert "return=representation" in captured["prefer"]

    def test_returns_none_on_scrape_run_insert_error(self) -> None:
        # source_id inexistente / FK violation: el INSERT a scrape_runs falla (400)
        # y start_run degrada fail-closed (None), sin run_id no hay artifact ni aporte.
        t = _RecordingTransport(runs_status=400)
        assert _exporter(t).start_run(_SOURCE_UUID) is None

    def test_returns_none_on_persistent_500(self) -> None:
        t = _RecordingTransport(runs_status=500)
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            assert _exporter(t).start_run(_SOURCE_UUID) is None

    def test_retries_transient_then_succeeds(self) -> None:
        class _Flaky(httpx.BaseTransport):
            def __init__(self) -> None:
                self.calls = 0

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/scrape_runs":
                    self.calls += 1
                    if self.calls == 1:
                        return httpx.Response(503, json={})
                    return httpx.Response(201, json=[{"run_id": "run-ok"}])
                return httpx.Response(404)

        t = _Flaky()
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            assert _exporter(t).start_run(_SOURCE_UUID) == "run-ok"
        assert t.calls == 2


class TestFinishRun:
    def test_patches_finished_at_and_stats(self) -> None:
        t = _RecordingTransport()
        exp = _exporter(t)
        exp.finish_run("run-1", {"pages": 2, "artifacts": 2})
        assert t.run_patches
        url, body = t.run_patches[-1]
        assert "finished_at" in body
        assert body["stats"] == {"pages": 2, "artifacts": 2}
        assert "run_id=eq.run-1" in url

    def test_none_run_id_is_noop(self) -> None:
        t = _RecordingTransport()
        _exporter(t).finish_run(None, {"pages": 0})
        assert t.run_patches == []

    def test_patch_error_does_not_raise(self) -> None:
        class _T(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if request.url.path == "/rest/v1/scrape_runs" and request.method == "PATCH":
                    return httpx.Response(500, json={})
                return httpx.Response(404)

        # No debe propagar: finish_run es best-effort.
        _exporter(_T()).finish_run("run-1", {"pages": 1})


# --- raw_artifacts ----------------------------------------------------------


class TestRecordArtifact:
    def test_posts_expected_fields(self) -> None:
        t = _RecordingTransport()
        _exporter(t).record_artifact("run-1", _page(page=3, content_hash="b" * 64))
        body = t.artifact_posts[0]
        assert body["run_id"] == "run-1"
        assert body["source_url"] == "https://demo.example/registro/1"
        assert body["http_status"] == 200
        assert body["fetched_at"] == "2026-06-24T15:30:00Z"
        assert body["body_hash"] == "b" * 64
        assert body["page"] == 3
        assert body["raw_text"] == _PII_MARKER

    def test_returns_artifact_id_from_representation(self) -> None:
        t = _RecordingTransport()
        assert _exporter(t).record_artifact("run-1", _page()) == "art-1"

    def test_append_only_two_calls_two_rows(self) -> None:
        t = _RecordingTransport()
        exp = _exporter(t)
        a1 = exp.record_artifact("run-1", _page(page=1))
        a2 = exp.record_artifact("run-1", _page(page=1))  # misma pagina, re-run
        assert len(t.artifact_posts) == 2
        assert a1 != a2
        # Append-only: nunca upsert / on_conflict sobre raw_artifacts.
        assert all("on_conflict" not in q for q in t.artifact_queries)

    def test_coerces_dict_raw_content_to_text(self) -> None:
        t = _RecordingTransport()
        _exporter(t).record_artifact("run-1", _page(raw_content={"rawJson": [{"id": 1}]}))
        raw_text = t.artifact_posts[0]["raw_text"]
        assert isinstance(raw_text, str)
        assert json.loads(raw_text) == {"rawJson": [{"id": 1}]}

    def test_returns_none_without_run_id(self) -> None:
        t = _RecordingTransport()
        assert _exporter(t).record_artifact(None, _page()) is None
        assert t.artifact_posts == []

    def test_returns_none_on_persistent_500(self) -> None:
        t = _RecordingTransport(artifacts_status=500)
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            assert _exporter(t).record_artifact("run-1", _page()) is None


# --- PII: raw_text NUNCA se loguea ------------------------------------------


class TestNoRawTextInLogs:
    def test_record_artifact_never_logs_raw_text(self, caplog: Any) -> None:
        t = _RecordingTransport()
        with caplog.at_level("DEBUG", logger="scrapers.exporters.provenance_exporter"):
            _exporter(t).record_artifact("run-1", _page(raw_content=_PII_MARKER))
        for rec in caplog.records:
            assert _PII_MARKER not in rec.getMessage()

    def test_failed_artifact_post_does_not_log_raw_text(self, caplog: Any) -> None:
        t = _RecordingTransport(artifacts_status=500)
        with caplog.at_level("DEBUG", logger="scrapers.exporters.provenance_exporter"):
            with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
                _exporter(t).record_artifact("run-1", _page(raw_content=_PII_MARKER))
        for rec in caplog.records:
            assert _PII_MARKER not in rec.getMessage()


# --- auth -------------------------------------------------------------------


class TestAuth:
    def test_uses_apikey_and_bearer_headers(self) -> None:
        cfg = StagingConfig(
            supabase_url="https://project.supabase.co",
            publishable_key="sb_publishable_test",
            ingest_jwt=_TEST_JWT,
        )
        exp = ProvenanceExporter(cfg)
        assert exp._client is not None
        assert exp._client.headers["apikey"] == "sb_publishable_test"
        assert exp._client.headers["Authorization"] == f"Bearer {_TEST_JWT}"
        exp.close()


# --- dry-run ----------------------------------------------------------------


class TestDryRun:
    def test_start_run_returns_placeholder_without_network(self) -> None:
        exp = ProvenanceExporter(None)
        assert exp.enabled is False
        run_id = exp.start_run("demo")
        assert run_id  # placeholder truthy para que el pipeline siga

    def test_record_artifact_returns_placeholder_without_network(self) -> None:
        exp = ProvenanceExporter(None)
        artifact_id = exp.record_artifact(exp.start_run("demo"), _page())
        assert artifact_id  # placeholder truthy

    def test_finish_run_noop_when_disabled(self) -> None:
        exp = ProvenanceExporter(None)
        exp.finish_run("whatever", {"pages": 1})  # no debe lanzar

    def test_from_env_none_enters_dry_run(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            exp = ProvenanceExporter(StagingConfig.from_env())
        assert exp.enabled is False


# --- ciclo de vida ----------------------------------------------------------


class TestLifecycle:
    def test_does_not_close_injected_client(self) -> None:
        t = _RecordingTransport()
        client = httpx.Client(base_url="https://project.supabase.co", transport=t)
        cfg = StagingConfig(
            supabase_url="https://project.supabase.co", publishable_key="k", ingest_jwt=_TEST_JWT
        )
        exp = ProvenanceExporter(cfg, client=client)
        exp.close()
        resp = client.post("/rest/v1/raw_artifacts", json={})
        assert resp.status_code in (201, 200)
        client.close()
