"""Contract tests against a frozen fixture from the real backend migrations.

This test exists to prevent false-greens where scraper code validates against a
local invented schema instead of DataVenezuela/dataVenezuela migrations.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scrapers.exporters import staging_exporter
from scrapers.exporters.staging_exporter import StagingExporter
from scrapers.jobs import consolidation_job
from scrapers.jobs.consolidation_job import _candidate_payload
from scrapers.tests.backend_schema_contract import load_backend_schema_contract

_FIXTURE = Path(__file__).parent / "fixtures" / "backend_schema_contract.sql"
_CONTRACT = load_backend_schema_contract(_FIXTURE)

_INGEST_PAYLOAD_TO_APORTES_COLUMN = {
    "run_id": "run_id",
    "entity_type": "entity_type",
    "external_id": "external_id",
    "dedup_hash": "dedup_hash",
    "dedup_version": "dedup_version",
    "block_keys": "block_keys",
    "content_hash": "content_hash",
    "source_id": "source_id",
    "scraper_id": "scraper_id",
    "raw_json": "raw_json",
    "source_record_id": "source_record_id",
    "source_url": "source_url",
    "parser_version": "parser_version",
    "normalizer_version": "normalizer_version",
}

_EVENT_COLUMNS_USED_BY_CONSOLIDATION = {
    "dedup_hash",
    "event_type",
    "occurred_at",
    "status",
}

_ACOPIO_COLUMNS_USED_BY_CONSOLIDATION = {
    "dedup_hash",
    "event_id",
    "name",
    "location",
    "confidence_score",
    "status",
    "needs",
}


def test_fixture_is_from_real_backend_migrations() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    assert "DataVenezuela/dataVenezuela" in text
    assert "0001_init.sql" in text
    assert "0004_dedup_schema.sql" in text
    assert "0008_ingesta_staging_dedup.sql" in text
    assert "0009_dedup_consolidation.sql" in text
    assert "0016_aportes_trust_tier.sql" in text
    assert "0017_aportes_unique_source_external.sql" in text
    assert "TEST FIXTURE, not a runnable migration" in text


def test_ingest_payload_columns_exist_in_real_aportes_schema() -> None:
    payload = StagingExporter(None, run_id="00000000-0000-4000-8000-000000000001")._build_payload(
        {
            "_entity_type": "Person",
            "_source_record_id": "synthetic-record-1",
            "_source_url": "https://example.test/synthetic",
            "_parser_version": "parser-v1",
            "_normalizer_version": "normalizer-v1",
            "event_id": "00000000-0000-4000-8000-000000000002",
            "full_name": "Persona Sintetica",
            "cedula_hmac": "a" * 64,
            "status": "missing",
        },
        "synthetic_source",
    )

    unknown_payload_keys = set(payload) - set(_INGEST_PAYLOAD_TO_APORTES_COLUMN)
    assert not unknown_payload_keys
    _CONTRACT.require_columns(
        "aportes",
        {_INGEST_PAYLOAD_TO_APORTES_COLUMN[key] for key in payload},
    )


def test_aportes_quality_columns_exist_for_consolidation_winner_selection() -> None:
    assert _CONTRACT.optional_columns(
        "aportes",
        {"trust_tier", "fetched_at", "confidence_score"},
    ) == {"trust_tier", "fetched_at", "confidence_score"}


def test_source_watermark_contract_matches_backend_schema() -> None:
    _CONTRACT.require_columns("source_watermarks", {"source_slug", "watermark_at"})
    _CONTRACT.require_unique_target("source_watermarks", ("source_slug",))


def test_postgrest_on_conflict_targets_have_real_unique_indexes() -> None:
    for table, columns in _module_on_conflict_targets(staging_exporter):
        _CONTRACT.require_columns(table, set(columns))
        _CONTRACT.require_unique_target(table, columns)
    for table, columns in _module_on_conflict_targets(consolidation_job):
        _CONTRACT.require_columns(table, set(columns))
        _CONTRACT.require_unique_target(table, columns)


def test_event_and_acopio_consolidation_targets_are_real_and_idempotent() -> None:
    _CONTRACT.require_columns("events", _EVENT_COLUMNS_USED_BY_CONSOLIDATION)
    _CONTRACT.require_unique_target("events", ("dedup_hash",))
    _CONTRACT.require_columns("acopio_centers", _ACOPIO_COLUMNS_USED_BY_CONSOLIDATION)
    _CONTRACT.require_unique_target("acopio_centers", ("dedup_hash",))


def test_person_dedup_candidate_payload_matches_real_backend_schema() -> None:
    payload = _candidate_payload({
        "event_id": "00000000-0000-4000-8000-000000000001",
        "left_person": "00000000-0000-4000-8000-000000000002",
        "right_person": "00000000-0000-4000-8000-000000000003",
        "score": 0.95,
        "reasons": {"nombre": 0.35},
        "priority": "high",
    })

    _CONTRACT.require_columns("dedup_candidates", set(payload))
    assert "left_person_record_id" not in payload
    assert "right_person_record_id" not in payload
    assert "blocking_key" not in payload


def test_person_dedup_pair_unique_expression_exists() -> None:
    _CONTRACT.require_columns(
        "dedup_candidates",
        {"event_id", "left_person", "right_person", "score", "reasons", "priority", "decision"},
    )
    target = ("least(left_person, right_person)", "greatest(left_person, right_person)")
    _CONTRACT.require_unique_target("dedup_candidates", target)


def test_negative_missing_required_column_is_detected() -> None:
    try:
        _CONTRACT.require_columns("aportes", {"trust_tier_text"})
    except AssertionError as exc:
        assert "aportes.trust_tier_text" in str(exc)
    else:  # pragma: no cover - documents the failure mode expected by #210.
        raise AssertionError("expected missing aportes.trust_tier_text to fail the contract")


def test_negative_missing_unique_constraint_is_detected() -> None:
    try:
        _CONTRACT.require_unique_target("aportes", ("external_id",))
    except AssertionError as exc:
        assert "UNIQUE public.aportes(external_id)" in str(exc)
    else:  # pragma: no cover - documents the failure mode expected by #210.
        raise AssertionError("expected missing UNIQUE(external_id) to fail")


def test_negative_non_unique_on_conflict_column_is_detected() -> None:
    try:
        _CONTRACT.require_unique_target("dedup_candidates", ("decision",))
    except AssertionError as exc:
        assert "UNIQUE public.dedup_candidates(decision)" in str(exc)
    else:  # pragma: no cover - documents the failure mode expected by #210.
        raise AssertionError("expected non-unique dedup_candidates.decision to fail")


def _module_on_conflict_targets(module: object) -> list[tuple[str, tuple[str, ...]]]:
    targets: list[tuple[str, tuple[str, ...]]] = []
    for value in vars(module).values():
        if not isinstance(value, str) or "on_conflict=" not in value:
            continue
        parsed = urlsplit(value)
        table = parsed.path.rsplit("/", 1)[-1]
        raw_target = parse_qs(parsed.query).get("on_conflict", [""])[0]
        if table and raw_target:
            targets.append((table, tuple(part.strip() for part in raw_target.split(","))))
    return targets
