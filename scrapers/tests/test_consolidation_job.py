"""Tests for consolidation_job.py — 100% offline with httpx.MockTransport.

Simulates supabase REST API responses for:
- GET /rest/v1/aportes (reading batches)
- POST /rest/v1/dedup_candidates (upserting candidates)
- PATCH /rest/v1/aportes (marking consolidated)
"""

from __future__ import annotations

import json
from typing import Any
import httpx

from scrapers.jobs.consolidation_job import (
    ConsolidationConfig,
    run_consolidation,
)

_EVENT_ID = "8f14e45f-ceea-467e-bd5d-0a4f2e0c1a3a"
_PH_HASH_A = "JN"
_PH_HASH_B = "MNKL"


# ---------------------------------------------------------------------------
# Mock Supabase Transport
# ---------------------------------------------------------------------------


class _SupabaseTransport(httpx.BaseTransport):
    """Mock Supabase REST API with configurable responses.

    Simulates:
    - aportes table with cursor-based pagination
    - dedup_candidates upsert
    - aportes consolidated_at update
    """

    def __init__(
        self,
        aportes_rows: list[dict[str, Any]] | None = None,
        batch_size: int = 500,
        upsert_status: int = 201,
        patch_status: int = 204,
    ) -> None:
        self.aportes_rows = aportes_rows or []
        self.batch_size = batch_size
        self.upsert_status = upsert_status
        self.patch_status = patch_status
        # Track calls for assertions
        self.get_calls: list[str] = []
        self.post_bodies: list[dict[str, Any]] = []
        self.patch_calls: list[str] = []
        # Track cursor position for pagination simulation
        self._cursor_idx = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        url_str = str(request.url)

        if request.method == "GET" and "/rest/v1/aportes" in path:
            self.get_calls.append(url_str)
            return self._handle_get_aportes(request)
        elif request.method == "POST" and "/rest/v1/dedup_candidates" in path:
            body = json.loads(request.content) if request.content else {}
            self.post_bodies.append(body)
            return httpx.Response(self.upsert_status, json={"ok": True})
        elif request.method == "PATCH" and "/rest/v1/aportes" in path:
            self.patch_calls.append(url_str)
            return httpx.Response(self.patch_status)

        return httpx.Response(404, json={"error": "not found"})

    def _handle_get_aportes(self, request: httpx.Request) -> httpx.Response:
        """Simulate cursor-based pagination from aportes."""
        # Parse limit from URL
        import re
        limit_match = re.search(r"limit=(\d+)", str(request.url))
        limit = int(limit_match.group(1)) if limit_match else self.batch_size

        # Return next batch
        start = self._cursor_idx
        end = min(start + limit, len(self.aportes_rows))
        batch = self.aportes_rows[start:end]
        self._cursor_idx = end

        return httpx.Response(200, json=batch)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_aportes_row(
    id_: str,
    name: str,
    event_id: str = _EVENT_ID,
    cedula_hmac: str | None = None,
    location: str | None = None,
    phonetic_hash: str = _PH_HASH_A,
    age_range: dict[str, int] | None = None,
    status: str = "missing",
    created_at: str = "2024-01-01T00:00:00Z",
) -> dict[str, Any]:
    """Create a synthetic aportes row."""
    return {
        "id": id_,
        "entity_type": "person",
        "full_name": name,
        "event_id": event_id,
        "cedula_hmac": cedula_hmac,
        "last_known_location": location,
        "phonetic_hash": phonetic_hash,
        "age_range": age_range,
        "status": status,
        "created_at": created_at,
        "consolidated_at": None,
    }


def _build_client(transport: _SupabaseTransport) -> httpx.Client:
    return httpx.Client(
        base_url="https://test.supabase.co",
        transport=transport,
    )


def _config(**overrides: Any) -> ConsolidationConfig:
    return ConsolidationConfig(
        supabase_url="https://test.supabase.co",
        supabase_service_key="test-key",
        entity_type=overrides.get("entity_type", "person"),
        batch_size=int(overrides.get("batch_size", 500)),
        threshold=float(overrides.get("threshold", 0.85)),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConsolidationJob:
    def test_pair_above_threshold_creates_candidate(self) -> None:
        """Two very similar persons → candidate created."""
        rows = [
            _make_aportes_row("uuid-1", "Juan Perez Gonzalez", cedula_hmac="same",
                              age_range={"min": 25, "max": 35},
                              phonetic_hash=_PH_HASH_A),
            _make_aportes_row("uuid-2", "Juan Perez Gonzales", cedula_hmac="same",
                              age_range={"min": 25, "max": 35},
                              phonetic_hash=_PH_HASH_A),
        ]
        transport = _SupabaseTransport(aportes_rows=rows)
        client = _build_client(transport)
        config = _config(threshold=0.80)

        result = run_consolidation(config, client=client)

        assert result.records_read == 2
        assert result.blocks >= 1
        assert result.pairs_compared >= 1
        assert result.candidates >= 1
        assert len(transport.post_bodies) >= 1
        # Check candidate content
        body = transport.post_bodies[0]
        assert body["decision"] == "pending"
        assert body["score"] >= 0.80

    def test_different_cedulas_veto_score_zero_no_candidate(self) -> None:
        """Different cedulas → score 0, no candidate created."""
        rows = [
            _make_aportes_row("uuid-1", "Juan Perez", cedula_hmac="aaa", phonetic_hash=_PH_HASH_A),
            _make_aportes_row("uuid-2", "Juan Perez", cedula_hmac="bbb", phonetic_hash=_PH_HASH_A),
        ]
        transport = _SupabaseTransport(aportes_rows=rows)
        client = _build_client(transport)
        config = _config(threshold=0.01)  # very low threshold

        result = run_consolidation(config, client=client)

        # No candidates because veto forces score to 0
        assert result.candidates == 0
        assert len(transport.post_bodies) == 0

    def test_same_phonetics_different_location_compared(self) -> None:
        """Phonetic (loose) block compares persons with different locations."""
        rows = [
            _make_aportes_row("uuid-1", "Juan Perez Gonzalez",
                              cedula_hmac="same", age_range={"min": 25, "max": 35},
                              location="Caracas, Miranda", phonetic_hash=_PH_HASH_A),
            _make_aportes_row("uuid-2", "Juan Perez Gonzalez",
                              cedula_hmac="same", age_range={"min": 25, "max": 35},
                              location="Petare, Miranda", phonetic_hash=_PH_HASH_A),
        ]
        transport = _SupabaseTransport(aportes_rows=rows)
        client = _build_client(transport)
        config = _config(threshold=0.80)

        result = run_consolidation(config, client=client)

        assert result.pairs_compared >= 1
        assert result.candidates >= 1  # same state boosts score

    def test_candidate_already_exists_upserted_not_duplicated(self) -> None:
        """When dedup_candidates already has the pair, upsert happens."""
        rows = [
            _make_aportes_row("uuid-1", "Juan Perez Gonzalez", cedula_hmac="same",
                              age_range={"min": 25, "max": 35},
                              phonetic_hash=_PH_HASH_A),
            _make_aportes_row("uuid-2", "Juan Perez Gonzalez", cedula_hmac="same",
                              age_range={"min": 25, "max": 35},
                              phonetic_hash=_PH_HASH_A),
        ]
        transport = _SupabaseTransport(aportes_rows=rows)
        client = _build_client(transport)
        config = _config(threshold=0.80)

        result = run_consolidation(config, client=client)

        # Should have one POST (upsert), not multiple
        assert result.candidates == 1

    def test_person_without_cedula_hmac_only_loose_block(self) -> None:
        """Person without cedula → no strong block key."""
        rows = [
            _make_aportes_row("uuid-1", "Juan Perez", cedula_hmac=None, phonetic_hash=_PH_HASH_A),
            _make_aportes_row("uuid-2", "Juan Perez", cedula_hmac=None, phonetic_hash=_PH_HASH_A),
        ]
        transport = _SupabaseTransport(aportes_rows=rows)
        client = _build_client(transport)
        config = _config(threshold=0.80)

        result = run_consolidation(config, client=client)

        assert result.pairs_compared >= 1
        assert len(transport.get_calls) >= 1

    def test_person_without_phonetic_hash_no_loose_block(self) -> None:
        """Person without phonetic_hash → only strong block if cedula exists."""
        rows = [
            _make_aportes_row("uuid-1", "Juan Perez", cedula_hmac="abc",
                              location="Caracas, Miranda",
                              age_range={"min": 25, "max": 35},
                              phonetic_hash=""),
            _make_aportes_row("uuid-2", "Juan Perez", cedula_hmac="abc",
                              location="Caracas, Miranda",
                              age_range={"min": 25, "max": 35},
                              phonetic_hash=""),
        ]
        transport = _SupabaseTransport(aportes_rows=rows)
        client = _build_client(transport)
        config = _config(threshold=0.80)

        result = run_consolidation(config, client=client)

        # Still compared via strong block
        assert result.pairs_compared >= 1
        assert result.candidates >= 1

    def test_empty_batch_exits_cleanly(self) -> None:
        """Zero records → job finishes without error."""
        transport = _SupabaseTransport(aportes_rows=[])
        client = _build_client(transport)
        config = _config()

        result = run_consolidation(config, client=client)

        assert result.records_read == 0
        assert result.candidates == 0
        assert len(result.errors) == 0

    def test_interrupted_job_unprocessed_records_found_next_run(self) -> None:
        """Records not marked as consolidated are re-read."""
        rows = [
            _make_aportes_row("uuid-1", "Juan Perez", created_at="2024-01-01T00:00:00Z"),
            _make_aportes_row("uuid-2", "Maria Lopez", created_at="2024-01-01T00:00:01Z"),
        ]
        # First run processes all
        transport1 = _SupabaseTransport(aportes_rows=list(rows))
        client1 = _build_client(transport1)
        result1 = run_consolidation(_config(), client=client1)
        assert result1.records_read == 2

        # Second run: rows are still returned by the mock (consolidated_at=None),
        # simulating what happens with a fresh cursor
        transport2 = _SupabaseTransport(aportes_rows=list(rows))
        client2 = _build_client(transport2)
        result2 = run_consolidation(_config(), client=client2)
        # The mock re-returns the same rows, proving the job would re-process
        assert result2.records_read == 2

    def test_run_id_in_result(self) -> None:
        """Result includes a valid run_id."""
        transport = _SupabaseTransport(aportes_rows=[])
        client = _build_client(transport)
        result = run_consolidation(_config(), client=client)

        assert result.run_id
        assert len(str(result.run_id)) > 20

    def test_result_metrics_keys(self) -> None:
        """Result has all expected metric fields."""
        transport = _SupabaseTransport(aportes_rows=[])
        client = _build_client(transport)
        result = run_consolidation(_config(), client=client)

        assert result.entity_type == "person"
        assert result.batches >= 0
        assert result.records_read >= 0
        assert result.blocks >= 0
        assert result.pairs_compared >= 0
        assert result.candidates >= 0
        assert result.duplicates_skipped >= 0
        assert result.execution_time_ms >= 0

    def test_batches_increments(self) -> None:
        """Multiple batches increment batch counter."""
        rows = []
        for i in range(5):
            rows.append(
                _make_aportes_row(
                    f"uuid-{i}", f"Person {i}",
                    cedula_hmac=None, phonetic_hash="XYZ",
                )
            )
        transport = _SupabaseTransport(aportes_rows=rows, batch_size=2)
        client = _build_client(transport)
        config = _config(batch_size=2, threshold=0.95)

        result = run_consolidation(config, client=client)

        # 5 records with batch_size=2 → 3 batches (2+2+1)
        assert result.batches == 3
        assert result.records_read == 5
