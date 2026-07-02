"""
scrapers/tests/test_quarantine_exporter.py
============================================
Tests del QuarantineExporter, 100% offline.

Ningun test hace red real: el httpx.Client se construye con un
``_RecordingTransport`` (subclase de httpx.BaseTransport) inyectado via el
parametro ``client`` del constructor. El transport responde a /api/v1/quarantine
y registra los bodies para los asserts.

Incluye un fixture por cada ``reason_code`` (criterio de aceptacion #88).
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from scrapers.exporters.quarantine_exporter import (
    _PREVIEW_MAX_CHARS,
    REASON_CODES,
    RISK_LEVELS,
    QuarantineConfig,
    QuarantineExporter,
    QuarantineRecord,
    QuarantineResult,
    quarantine_payload_hash,
)

_QUARANTINE_PATH = "/api/v1/quarantine"


class _RecordingTransport(httpx.BaseTransport):
    """Captura POSTs a /api/v1/quarantine y devuelve un status fijo."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.posts: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == _QUARANTINE_PATH:
            self.posts.append(json.loads(request.content))
            return httpx.Response(self.status, json={"ok": True})
        return httpx.Response(404)


class _FlakyTransport(httpx.BaseTransport):
    """Devuelve los status de ``sequence`` en orden para /api/v1/quarantine."""

    def __init__(self, sequence: list[int]) -> None:
        self.sequence = sequence
        self.attempts = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == _QUARANTINE_PATH:
            idx = min(self.attempts, len(self.sequence) - 1)
            status = self.sequence[idx]
            self.attempts += 1
            return httpx.Response(status, json={"ok": True})
        return httpx.Response(404)


def _exporter(transport: httpx.BaseTransport) -> QuarantineExporter:
    cfg = QuarantineConfig(api_key="k", base_url="https://backend.test")
    client = httpx.Client(base_url="https://backend.test", transport=transport)
    return QuarantineExporter(cfg, client=client, run_id="run-1")


class TestAuthHeader:
    def test_uses_x_api_key_not_bearer(self) -> None:
        # El backend dataVenezuela autentica con x-api-key (authenticatePartner),
        # no con Authorization: Bearer. El exporter debe enviarlo cuando construye
        # su propio cliente (config presente, sin client inyectado).
        cfg = QuarantineConfig(api_key="secret-key", base_url="https://backend.test")
        exp = QuarantineExporter(cfg, run_id="run-1")
        try:
            assert exp._client is not None
            assert exp._client.headers.get("x-api-key") == "secret-key"
            assert "authorization" not in exp._client.headers
        finally:
            exp.close()


def _record(
    reason_code: str = "invalid_schema",
    risk_level: str = "medium",
    **kw: Any,
) -> QuarantineRecord:
    base: dict[str, Any] = {
        "source_slug": "demo",
        "reason_code": reason_code,
        "risk_level": risk_level,
        "source_url": "https://fuente.demo/registro/1",
        "reason_detail": "detalle de prueba",
        "payload_preview_redacted": "fragmento [REDACTED]",
        "payload_hash": quarantine_payload_hash("payload-original"),
        "pii_findings_summary": {"cedulas": 1, "telefonos": 0},
    }
    base.update(kw)
    return QuarantineRecord(**base)


# --- payload ----------------------------------------------------------------

class TestPayload:
    def test_payload_has_all_columns(self) -> None:
        t = _RecordingTransport()
        _exporter(t).quarantine(_record())
        body = t.posts[0]
        required = {
            "runId", "sourceSlug", "sourceUrl", "reasonCode", "reasonDetail",
            "riskLevel", "payloadPreviewRedacted", "payloadHash",
            "piiFindingsSummary",
        }
        assert required.issubset(body.keys())

    def test_run_id_propagated(self) -> None:
        t = _RecordingTransport()
        _exporter(t).quarantine(_record())
        assert t.posts[0]["runId"] == "run-1"

    def test_pii_summary_is_object(self) -> None:
        t = _RecordingTransport()
        _exporter(t).quarantine(_record())
        assert t.posts[0]["piiFindingsSummary"] == {"cedulas": 1, "telefonos": 0}

    def test_preview_truncated_to_max(self) -> None:
        t = _RecordingTransport()
        long_preview = "x" * (_PREVIEW_MAX_CHARS + 50)
        _exporter(t).quarantine(_record(payload_preview_redacted=long_preview))
        assert len(t.posts[0]["payloadPreviewRedacted"]) == _PREVIEW_MAX_CHARS

    def test_short_preview_not_truncated(self) -> None:
        t = _RecordingTransport()
        _exporter(t).quarantine(_record(payload_preview_redacted="corto"))
        assert t.posts[0]["payloadPreviewRedacted"] == "corto"


# --- hash -------------------------------------------------------------------

class TestPayloadHash:
    def test_hash_is_bare_64_hex(self) -> None:
        h = quarantine_payload_hash("payload-original")
        assert len(h) == 64
        assert not h.startswith("sha256:")
        int(h, 16)  # es hex valido

    def test_hash_is_deterministic(self) -> None:
        assert quarantine_payload_hash("abc") == quarantine_payload_hash("abc")

    def test_bytes_and_str_match(self) -> None:
        assert quarantine_payload_hash("abc") == quarantine_payload_hash(b"abc")


# --- un fixture por cada reason_code (criterio de aceptacion #88) ------------

class TestReasonCodeFixtures:
    @pytest.mark.parametrize("reason_code", sorted(REASON_CODES))
    def test_each_reason_code_is_sent(self, reason_code: str) -> None:
        t = _RecordingTransport(status=201)
        res = _exporter(t).quarantine(_record(reason_code=reason_code))
        assert res.sent == 1
        assert res.errors == []
        assert t.posts[0]["reasonCode"] == reason_code

    @pytest.mark.parametrize("risk_level", sorted(RISK_LEVELS))
    def test_each_risk_level_is_sent(self, risk_level: str) -> None:
        t = _RecordingTransport(status=201)
        res = _exporter(t).quarantine(_record(risk_level=risk_level))
        assert res.sent == 1
        assert t.posts[0]["riskLevel"] == risk_level


# --- validacion -------------------------------------------------------------

class TestValidation:
    def test_invalid_reason_code_is_error_not_raised(self) -> None:
        t = _RecordingTransport()
        res = _exporter(t).quarantine(_record(reason_code="no_existe"))
        assert res.sent == 0
        assert res.errors
        assert t.posts == []  # no se POSTeo nada invalido

    def test_invalid_risk_level_is_error(self) -> None:
        t = _RecordingTransport()
        res = _exporter(t).quarantine(_record(risk_level="extremo"))
        assert res.sent == 0
        assert res.errors

    def test_empty_source_slug_is_error(self) -> None:
        t = _RecordingTransport()
        res = _exporter(t).quarantine(_record(source_slug=""))
        assert res.sent == 0
        assert res.errors

    def test_one_invalid_does_not_block_others(self) -> None:
        t = _RecordingTransport(status=201)
        res = _exporter(t).quarantine_many(
            [_record(), _record(reason_code="no_existe"), _record()]
        )
        assert res.sent == 2  # los dos validos pasaron
        assert len(res.errors) == 1  # el invalido se acumulo


# --- clasificacion de respuesta ---------------------------------------------

class TestResponseClassification:
    def test_200_counts_as_sent(self) -> None:
        t = _RecordingTransport(status=200)
        res = _exporter(t).quarantine(_record())
        assert res.sent == 1 and res.errors == []

    def test_201_counts_as_sent(self) -> None:
        t = _RecordingTransport(status=201)
        res = _exporter(t).quarantine(_record())
        assert res.sent == 1

    def test_409_counts_as_duplicate(self) -> None:
        t = _RecordingTransport(status=409)
        res = _exporter(t).quarantine(_record())
        assert res.duplicates == 1 and res.sent == 0 and res.errors == []

    def test_500_counts_as_error_without_raising(self) -> None:
        t = _RecordingTransport(status=500)
        res = _exporter(t).quarantine(_record())
        assert len(res.errors) >= 1 and res.sent == 0


# --- retry del POST ---------------------------------------------------------

class TestPostRetry:
    def test_503_then_201_ends_as_sent(self) -> None:
        t = _FlakyTransport([503, 201])
        cfg = QuarantineConfig(api_key="k", base_url="https://backend.test")
        client = httpx.Client(base_url="https://backend.test", transport=t)
        exp = QuarantineExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.exporters.quarantine_exporter.time.sleep", lambda *_: None):
            res = exp.quarantine(_record())
        assert res.sent == 1
        assert res.errors == []
        assert t.attempts == 2

    def test_persistent_503_ends_as_error(self) -> None:
        t = _FlakyTransport([503])
        cfg = QuarantineConfig(api_key="k", base_url="https://backend.test")
        client = httpx.Client(base_url="https://backend.test", transport=t)
        exp = QuarantineExporter(cfg, client=client, run_id="run-1")
        with patch("scrapers.exporters.quarantine_exporter.time.sleep", lambda *_: None):
            res = exp.quarantine(_record())
        assert res.sent == 0
        assert res.errors


# --- dry-run ----------------------------------------------------------------

class TestDryRun:
    def test_dry_run_disabled_sends_nothing(self) -> None:
        exp = QuarantineExporter(None, run_id="run-1")
        assert exp.enabled is False
        res = exp.quarantine(_record())
        assert isinstance(res, QuarantineResult)
        assert res.sent == 0 and res.duplicates == 0 and res.errors == []

    def test_dry_run_still_validates(self) -> None:
        # En dry-run un registro invalido igual se reporta como error (no POST).
        exp = QuarantineExporter(None)
        res = exp.quarantine(_record(reason_code="no_existe"))
        assert res.errors

    def test_from_env_none_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert QuarantineConfig.from_env() is None

    def test_from_env_no_vars_logs_info(self, caplog: Any) -> None:
        logger = "scrapers.exporters.quarantine_exporter"
        with patch.dict(os.environ, {}, clear=True):
            with caplog.at_level("INFO", logger=logger):
                assert QuarantineConfig.from_env() is None
        assert any(r.levelname == "INFO" for r in caplog.records)
        assert not any(r.levelname == "ERROR" for r in caplog.records)

    def test_from_env_partial_config_logs_error(self, caplog: Any) -> None:
        logger = "scrapers.exporters.quarantine_exporter"
        env = {"QUARANTINE_API_KEY": "k"}  # falta QUARANTINE_BASE_URL
        with patch.dict(os.environ, env, clear=True):
            with caplog.at_level("ERROR", logger=logger):
                assert QuarantineConfig.from_env() is None
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert errors
        assert "QUARANTINE_BASE_URL" in errors[0].getMessage()

    def test_from_env_full_config(self) -> None:
        env = {
            "QUARANTINE_API_KEY": "k",
            "QUARANTINE_BASE_URL": "https://backend.test/",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = QuarantineConfig.from_env()
        assert cfg is not None
        assert cfg.api_key == "k"
        assert cfg.base_url == "https://backend.test"  # rstrip de la barra final
