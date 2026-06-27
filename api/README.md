# VZLA_DEDUP Public API

This folder contains the first FastAPI scaffold for the public read API.

The API is a facade over the normalized VZLA_DEDUP model documented in
`docs/schema.md`: `events`, `persons`, `person_notes`, `person_sources`,
`person_photos`, and `acopio_centers`.

## Run Locally

```bash
cd api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

From the repository root:

```bash
uvicorn app.main:app --app-dir api
```

## Endpoints

```text
GET /health
GET /v1/persons
GET /v1/persons/{person_record_id}
GET /v1/events
GET /v1/acopio
GET /v1/stats
GET /openapi.json
```

List endpoints return an empty `items` array until the next PR connects the
repository layer to Postgres/Supabase.

## Public DTOs

The current response models are:

```text
PersonPublic
PersonNotePublic
PersonSourcePublic
PersonPhotoPublic
EventPublic
AcopioCenterPublic
StatsResponse
```

They mirror the normalized schema where safe for public read use.

## Privacy Exclusions

The public API does not expose sensitive or internal fields:

```text
cedula_hmac
contact_hmac
raw_json
raw_text
scraper_id
partner_api_keys
internal API key fields
```

## Out Of Scope For This PR

```text
PostgreSQL/Supabase connection
scraping or batch ingestion
deduplication implementation
PFIF export
auth
rate limiting
Cloudflare-specific config
database migrations
```

## Next PR

The next API PR should replace the in-memory repository with read-only
Postgres/Supabase queries against the normalized schema.
