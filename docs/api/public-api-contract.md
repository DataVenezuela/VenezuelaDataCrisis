# Public API Contract

This document describes the initial public API contract for VZLA_DEDUP.

## Purpose

The API exposes normalized, deduplicated crisis data for public read consumers.
It is intentionally small: this PR defines the route structure, response DTOs,
tests, and documentation before connecting a database.

## Relationship With The Normalized Schema

The public DTOs are based on the normalized VZLA_DEDUP entities documented in
`docs/schema.md`:

```text
events
persons
person_notes
person_sources
person_photos
acopio_centers
```

The API does not expose raw scraper payloads or internal lookup hashes.

## Endpoints

### `GET /health`

Returns service status:

```json
{
  "ok": true,
  "service": "vzla-dedup-api",
  "version": "0.1.0"
}
```

### `GET /v1/persons`

Query params:

```text
q
status
event_id
verification_status
limit
offset
```

Current scaffold response:

```json
{
  "items": [],
  "limit": 50,
  "offset": 0
}
```

### `GET /v1/persons/{person_record_id}`

Returns a person with notes, sources, and photos once database access exists.
For unknown records, this scaffold returns `404`.

### `GET /v1/events`

Query params:

```text
status
limit
offset
```

Current scaffold response:

```json
{
  "items": [],
  "limit": 50,
  "offset": 0
}
```

### `GET /v1/acopio`

Query params:

```text
status
event_id
limit
offset
```

Current scaffold response:

```json
{
  "items": [],
  "limit": 50,
  "offset": 0
}
```

### `GET /v1/stats`

Returns zero counts until the repository connects to Postgres/Supabase:

```json
{
  "persons": {
    "total": 0,
    "missing": 0,
    "found": 0,
    "injured": 0,
    "deceased": 0,
    "unknown": 0
  },
  "events": {
    "total": 0,
    "active": 0,
    "monitoring": 0,
    "closed": 0
  },
  "acopio": {
    "total": 0,
    "active": 0,
    "full": 0,
    "closed": 0,
    "unverified": 0
  }
}
```

## Privacy Policy

The public API excludes internal and sensitive fields:

```text
cedula_hmac
contact_hmac
raw_json
raw_text
scraper_id
partner_api_keys
internal API key fields
```

Masked display fields such as `cedula_masked` may be returned because they are
part of the public contract and do not contain raw identifiers.

## Out Of Scope

This PR does not add:

```text
database access
schema migrations
scraper pipeline changes
deduplication logic
PFIF export
auth
rate limiting
Cloudflare deployment config
```

The next PR should connect `PublicRepository` to read-only Postgres/Supabase
queries.
