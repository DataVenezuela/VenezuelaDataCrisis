# Dump contract v0.1

Scrapers should generate dumps that DB/API can import later without knowing the internal details of each source.

This repo should contain code, docs, schemas, and fake examples only. Real dumps and raw data should stay outside GitHub.

## Output folder

Each source run should write files under a local ignored folder:

```text
outputs/<source_id>/<run_id>/
  manifest.json
  records.normalized.jsonl
  dedupe_candidates.jsonl
```

`dedupe_candidates.jsonl` is optional.

## Manifest

`manifest.json` describes the run:

```json
{
  "schema_version": "0.1",
  "run_id": "2026-06-26T22-30-00Z",
  "source_id": "fake_hospital_source",
  "source_name": "Fake Hospital Source",
  "source_url": "https://example.invalid/fake-source",
  "source_type": "HTML",
  "captured_at": "2026-06-26T22:30:00Z",
  "scraper_name": "fake_hospital_source",
  "records_total": 2,
  "records_output": 2,
  "records_needing_review": 1,
  "warnings": []
}
```

## Normalized records

`records.normalized.jsonl` is JSON Lines: one JSON object per line.

Every record should use this common envelope:

```json
{
  "schema_version": "0.1",
  "record_id": "fake_hospital_source:row-1",
  "record_type": "PERSON",
  "source": {
    "source_id": "fake_hospital_source",
    "source_name": "Fake Hospital Source",
    "source_url": "https://example.invalid/fake-source",
    "source_type": "HTML",
    "captured_at": "2026-06-26T22:30:00Z"
  },
  "raw": {
    "raw_ref": "private://fake_hospital_source/raw/snapshot.html",
    "raw_content_hash": "sha256:fakehash",
    "row_number": 1
  },
  "normalized": {
    "full_name": "FAKE PERSON",
    "national_id_hash": "hmac-sha256:fakehash",
    "age": 40,
    "status": "HOSPITALIZED",
    "facility_name": "Fake Hospital",
    "location_text": "Fake City"
  },
  "privacy": {
    "contains_sensitive_data": true,
    "hashed_fields": ["national_id"],
    "redacted_fields": []
  },
  "quality": {
    "confidence": 0.75,
    "validation_status": "UNVERIFIED",
    "needs_review": true,
    "warnings": ["FAKE_FIXTURE"]
  },
  "dedupe": {
    "dedupe_key": "person:fakehash",
    "dedupe_strategy": "national_id_hash"
  }
}
```

## Record types

Use one of these values:

- `PERSON`
- `FACILITY`
- `NEED`
- `INCIDENT`
- `DONATION`
- `PET`
- `RESOURCE_POINT`
- `ALERT`
- `UNKNOWN`

The `normalized` object changes by `record_type`. The envelope stays the same.

## Validation statuses

Use one of:

- `UNVERIFIED`
- `NEEDS_REVIEW`
- `VALIDATED`
- `REJECTED`
- `SUPERSEDED`
- `CONTRADICTED`

When unsure, use `NEEDS_REVIEW`.

## Raw data

Do not commit real raw data. The dump should keep:

- `raw_ref`: private storage path or local path outside git.
- `raw_content_hash`: hash of the raw snapshot for traceability.
- `row_number` or equivalent locator when available.

