"""
scrapers/tests/test_quarantine_destroyer.py
============================================
Tests del QuarantineDestroyer (Issue #273), 100% offline.

Ningun test hace red real: el httpx.Client se construye con un transport
inyectado (subclase de httpx.BaseTransport) via el parametro ``client`` del
constructor. El transport responde a PATCH/GET /rest/v1/quarantined_records y
registra requests/bodies para los asserts.

Incluye un fixture de un registro destruido (criterio de aceptacion #273):
la fila "destruida" que devuelve el PATCH conserva payload_hash + metadatos y
tiene payload_preview_redacted/pii_findings_summary a NULL y destroyed_at seteado.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from scrapers.exporters.staging_exporter import StagingConfig
from scrapers.jobs.quarantine_destroyer import (
    DestroyResult,
    QuarantineDestroyer,
)

_QUARANTINE_PATH = "/rest/v1/quarantined_records"

# Fixture de una fila YA destruida: metadatos + payload_hash preservados, campos
# sensibles a NULL y destroyed_at estampado. Es lo que PostgREST devuelve con
# return=representation tras un PATCH exitoso.
_DESTROYED_ROW: dict[str, Any] = {
    "id": "11111111-1111-4111-8111-111111111111",
    "source_slug": "encuentralos",
    "reason_code": "invalid_schema",
    "risk_level": "medium",
    "review_status": "rejected",
    "payload_hash": "a" * 64,
    "quarantined_at": "2026-01-01T00:00:00Z",
    "payload_preview_redacted": None,
    "pii_findings_summary": None,
    "destroyed_at": "2026-07-10T00:00:00Z",
}


def _cfg() -> StagingConfig:
    return StagingConfig(
        supabase_url="https://backend.test",
        publishable_key="anon-key",
        ingest_jwt="jwt-token",
    )


class _RecordingTransport(httpx.BaseTransport):
    """Responde PATCH/GET a quarantined_records y registra las requests.

    ``patch_rows`` es lo que devuelve el PATCH (return=representation).
    ``get_rows`` es lo que devuelve el GET de clasificacion.
    """

    def __init__(
        self,
        *,
        patch_rows: list[dict[str, Any]] | None = None,
        get_rows: list[dict[str, Any]] | None = None,
        patch_status: int = 200,
    ) -> None:
        self.patch_rows = patch_rows if patch_rows is not None else []
        self.get_rows = get_rows if get_rows is not None else []
        self.patch_status = patch_status
        self.requests: list[httpx.Request] = []
        self.patch_bodies: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path != _QUARANTINE_PATH:
            return httpx.Response(404)
        if request.method == "PATCH":
            self.patch_bodies.append(json.loads(request.content))
            return httpx.Response(self.patch_status, json=self.patch_rows)
        if request.method == "GET":
            return httpx.Response(200, json=self.get_rows)
        return httpx.Response(405)


class _FlakyPatchTransport(httpx.BaseTransport):
    """Devuelve los status de ``sequence`` en orden para PATCH."""

    def __init__(self, sequence: list[int], rows: list[dict[str, Any]]) -> None:
        self.sequence = sequence
        self.rows = rows
        self.attempts = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH" and request.url.path == _QUARANTINE_PATH:
            idx = min(self.attempts, len(self.sequence) - 1)
            status = self.sequence[idx]
            self.attempts += 1
            return httpx.Response(status, json=self.rows if status == 200 else [])
        return httpx.Response(404)


def _destroyer(transport: httpx.BaseTransport) -> QuarantineDestroyer:
    client = httpx.Client(base_url="https://backend.test", transport=transport)
    return QuarantineDestroyer(_cfg(), client=client)


def _patch_request(t: _RecordingTransport) -> httpx.Request:
    return next(r for r in t.requests if r.method == "PATCH")


# --- destruccion por id: elegible -------------------------------------------

class TestDestroyEligible:
    def test_rejected_row_is_destroyed(self) -> None:
        t = _RecordingTransport(patch_rows=[_DESTROYED_ROW])
        res = _destroyer(t).destroy(_DESTROYED_ROW["id"])
        assert res.destroyed == 1
        assert res.skipped == 0
        assert res.errors == []
        assert res.destroyed_ids == [(_DESTROYED_ROW["id"], "a" * 64)]

    def test_expired_row_is_destroyed(self) -> None:
        expired = {**_DESTROYED_ROW, "review_status": "pending"}
        t = _RecordingTransport(patch_rows=[expired])
        res = _destroyer(t).destroy(expired["id"])
        assert res.destroyed == 1 and res.errors == []

    def test_patch_body_nulls_sensitive_and_stamps_destroyed_at(self) -> None:
        t = _RecordingTransport(patch_rows=[_DESTROYED_ROW])
        _destroyer(t).destroy(_DESTROYED_ROW["id"])
        body = t.patch_bodies[0]
        assert body["payload_preview_redacted"] is None
        assert body["pii_findings_summary"] is None
        assert body["destroyed_at"] is not None

    def test_patch_body_only_touches_the_three_columns(self) -> None:
        # AC #3: la fila se preserva; el PATCH nunca toca payload_hash,
        # source_slug, reason_code, quarantined_at, etc.
        t = _RecordingTransport(patch_rows=[_DESTROYED_ROW])
        _destroyer(t).destroy(_DESTROYED_ROW["id"])
        assert set(t.patch_bodies[0].keys()) == {
            "payload_preview_redacted",
            "pii_findings_summary",
            "destroyed_at",
        }

    def test_query_carries_eligibility_and_idempotency_filters(self) -> None:
        t = _RecordingTransport(patch_rows=[_DESTROYED_ROW])
        _destroyer(t).destroy(_DESTROYED_ROW["id"])
        query = _patch_request(t).url.query.decode()
        assert f"id=eq.{_DESTROYED_ROW['id']}" in query
        assert "destroyed_at=is.null" in query
        assert "review_status.eq.rejected" in query
        assert "retention_until.lt." in query

    def test_destroyed_row_preserves_metadata(self) -> None:
        # Fixture de un registro destruido: conserva hash + metadatos, campos
        # sensibles a NULL, destroyed_at seteado.
        row = _DESTROYED_ROW
        assert row["payload_hash"] == "a" * 64
        assert row["source_slug"] and row["reason_code"] and row["quarantined_at"]
        assert row["payload_preview_redacted"] is None
        assert row["pii_findings_summary"] is None
        assert row["destroyed_at"] is not None


# --- destruccion por id: no elegible ----------------------------------------

class TestDestroyIneligible:
    def test_not_eligible_is_skipped_with_reason(self) -> None:
        # PATCH afecta 0 filas; el GET de clasificacion la reporta no elegible.
        t = _RecordingTransport(
            patch_rows=[],
            get_rows=[{
                "review_status": "pending",
                "destroyed_at": None,
                "retention_until": "2099-01-01T00:00:00Z",
            }],
        )
        res = _destroyer(t).destroy(_DESTROYED_ROW["id"])
        assert res.destroyed == 0
        assert res.skipped == 1
        assert res.errors and "no elegible" in res.errors[0]

    def test_already_destroyed_is_skipped_idempotent(self) -> None:
        t = _RecordingTransport(
            patch_rows=[],
            get_rows=[{
                "review_status": "rejected",
                "destroyed_at": "2026-07-01T00:00:00Z",
                "retention_until": None,
            }],
        )
        res = _destroyer(t).destroy(_DESTROYED_ROW["id"])
        assert res.destroyed == 0 and res.skipped == 1
        assert "ya destruido" in res.errors[0]

    def test_nonexistent_is_skipped(self) -> None:
        t = _RecordingTransport(patch_rows=[], get_rows=[])
        res = _destroyer(t).destroy(_DESTROYED_ROW["id"])
        assert res.skipped == 1
        assert "inexistente" in res.errors[0]


# --- barrido de retencion ---------------------------------------------------

class TestDestroyExpired:
    def test_sweep_destroys_all_eligible(self) -> None:
        rows = [
            {**_DESTROYED_ROW, "id": f"{i}1111111-1111-4111-8111-111111111111"}
            for i in range(3)
        ]
        t = _RecordingTransport(patch_rows=rows)
        res = _destroyer(t).destroy_expired()
        assert res.destroyed == 3 and res.errors == []

    def test_sweep_query_has_no_id_filter(self) -> None:
        t = _RecordingTransport(patch_rows=[_DESTROYED_ROW])
        _destroyer(t).destroy_expired()
        query = _patch_request(t).url.query.decode()
        assert "id=eq." not in query
        assert "destroyed_at=is.null" in query
        assert "review_status.eq.rejected" in query

    def test_sweep_limit_is_passed(self) -> None:
        t = _RecordingTransport(patch_rows=[_DESTROYED_ROW])
        _destroyer(t).destroy_expired(limit=50)
        query = _patch_request(t).url.query.decode()
        assert "limit=50" in query


# --- errores de servidor ----------------------------------------------------

class TestServerErrors:
    def test_500_is_error_without_raising(self) -> None:
        t = _RecordingTransport(patch_rows=[], patch_status=500)
        res = _destroyer(t).destroy(_DESTROYED_ROW["id"])
        assert res.destroyed == 0
        assert res.errors and "500" in res.errors[0]


# --- retry del PATCH --------------------------------------------------------

class TestPatchRetry:
    def test_503_then_200_ends_destroyed(self) -> None:
        t = _FlakyPatchTransport([503, 200], rows=[_DESTROYED_ROW])
        client = httpx.Client(base_url="https://backend.test", transport=t)
        exp = QuarantineDestroyer(_cfg(), client=client)
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            res = exp.destroy(_DESTROYED_ROW["id"])
        assert res.destroyed == 1
        assert res.errors == []
        assert t.attempts == 2

    def test_persistent_503_ends_error(self) -> None:
        t = _FlakyPatchTransport([503], rows=[])
        client = httpx.Client(base_url="https://backend.test", transport=t)
        exp = QuarantineDestroyer(_cfg(), client=client)
        with patch("scrapers.adapters._shared.time.sleep", lambda *_: None):
            res = exp.destroy(_DESTROYED_ROW["id"])
        assert res.destroyed == 0 and res.errors


# --- dry-run ----------------------------------------------------------------

class TestDryRun:
    def test_disabled_destroy_is_noop(self) -> None:
        exp = QuarantineDestroyer(None)
        assert exp.enabled is False
        res = exp.destroy(_DESTROYED_ROW["id"])
        assert isinstance(res, DestroyResult)
        assert res.destroyed == 0 and res.skipped == 0 and res.errors == []

    def test_disabled_sweep_is_noop(self) -> None:
        res = QuarantineDestroyer(None).destroy_expired()
        assert res.destroyed == 0 and res.errors == []

    def test_disabled_logs_info(self, caplog: Any) -> None:
        logger = "scrapers.jobs.quarantine_destroyer"
        with caplog.at_level("INFO", logger=logger):
            QuarantineDestroyer(None).destroy(_DESTROYED_ROW["id"])
        assert any(r.levelname == "INFO" for r in caplog.records)


# --- audit log --------------------------------------------------------------

class TestAuditLog:
    def test_destruction_logs_id_and_hash(self, caplog: Any) -> None:
        logger = "scrapers.jobs.quarantine_destroyer"
        t = _RecordingTransport(patch_rows=[_DESTROYED_ROW])
        with caplog.at_level("INFO", logger=logger):
            _destroyer(t).destroy(_DESTROYED_ROW["id"])
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert _DESTROYED_ROW["id"] in msgs
        assert "a" * 64 in msgs

    @pytest.mark.parametrize("status", ["rejected", "pending"])
    def test_no_pii_in_logs(self, caplog: Any, status: str) -> None:
        # El log nunca debe contener preview ni resumen PII (van a NULL, y de
        # todos modos la representacion destruida no los trae).
        logger = "scrapers.jobs.quarantine_destroyer"
        t = _RecordingTransport(patch_rows=[{**_DESTROYED_ROW, "review_status": status}])
        with caplog.at_level("INFO", logger=logger):
            _destroyer(t).destroy(_DESTROYED_ROW["id"])
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "cedula" not in msgs.lower()
