# Backend Schema Contract Fixture

`scrapers/tests/fixtures/backend_schema_contract.sql` is the frozen contract
fixture used by offline tests for ingest and consolidation. It is derived from
the real backend repository:

- Source repo: `DataVenezuela/dataVenezuela`
- Source path: `supabase/migrations/*.sql`
- Current included migrations:
  - `0001_init.sql`
  - `0004_dedup_schema.sql`
  - `0008_ingesta_staging_dedup.sql`
  - `0009_dedup_consolidation.sql`
  - `0016_aportes_trust_tier.sql`
  - `0017_aportes_unique_source_external.sql`

The fixture is not a migration and must not be executed against Supabase. It is
a small DDL excerpt used to catch false-green tests when scraper code assumes
columns, unique indexes, or conflict targets that do not exist in the backend.

## How To Update

1. Confirm the backend schema changed in `DataVenezuela/dataVenezuela`.
2. Fetch the relevant migration from the backend repo:

   ```bash
   gh api "repos/DataVenezuela/dataVenezuela/contents/supabase/migrations/<file>" \
     --jq '.content' | base64 -d
   ```

3. Update only the relevant DDL excerpt in
   `scrapers/tests/fixtures/backend_schema_contract.sql`.
4. Update the fixture header with the migration filename and extraction date.
5. Run:

   ```bash
   pytest scrapers/tests/test_backend_schema_contract.py
   pytest scrapers/tests
   ruff check .
   python -m mypy --strict --follow-imports=silent scrapers/adapters scrapers/parsers
   ```

6. In the PR, cite the backend migration or backend PR that justifies the
   fixture change.

## Required vs Optional Fields

If scraper code requires a database column or PostgREST `on_conflict` target,
the contract test must require that column or unique target from the fixture.

If scraper code merely carries an optional value inside `raw_json` and degrades
safely when a real column does not exist, the contract test may document it as
optional. Do not promote optional JSON fields to required table columns without
a backend migration.
