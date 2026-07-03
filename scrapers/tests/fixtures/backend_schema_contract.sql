-- FROZEN backend schema contract fixture for VenezuelaDataCrisis tests.
--
-- Source repository:
--   DataVenezuela/dataVenezuela
-- Source directory:
--   supabase/migrations/
-- Extracted on:
--   2026-07-03
-- Extraction command used for each migration:
--   gh api "repos/DataVenezuela/dataVenezuela/contents/supabase/migrations/<file>" \
--     --jq '.content' | base64 -d
--
-- Included migrations and why:
--   0001_init.sql
--     sources and base aportes columns.
--   0004_dedup_schema.sql
--     canonical events/persons/acopio_centers tables.
--   0008_ingesta_staging_dedup.sql
--     staging/dedup aportes columns and source_watermarks.
--   0009_dedup_consolidation.sql
--     dedup_hash unique indexes, dedup_candidates, dedup_decisions.
--   0016_aportes_trust_tier.sql
--     trust_tier, fetched_at, confidence_score for consolidation winner-selection.
--   0017_aportes_unique_source_external.sql
--     non-partial unique index for PostgREST on_conflict=source_id,external_id.
--
-- WARNING:
--   This is a TEST FIXTURE, not a runnable migration. Update it only by
--   re-reading the real DataVenezuela/dataVenezuela migrations and cite the
--   backend migration or PR that changed the contract.

-- ---------------------------------------------------------------------------
-- 0001_init.sql: sources and base aportes
-- ---------------------------------------------------------------------------
create table public.sources (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  slug       text not null unique,
  website    text,
  owner_id   uuid references public.profiles(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.aportes (
  id          uuid primary key default gen_random_uuid(),
  external_id text,
  raw_json    jsonb,
  raw_text    text,
  source_id   uuid references public.sources(id)  on delete set null,
  scraper_id  uuid references public.profiles(id) on delete set null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint aportes_has_payload check (raw_json is not null or raw_text is not null)
);

create unique index aportes_scraper_external_unique_idx
  on public.aportes (scraper_id, external_id)
  where scraper_id is not null and external_id is not null;

-- ---------------------------------------------------------------------------
-- 0008_ingesta_staging_dedup.sql: staging/dedup columns and watermarks
-- ---------------------------------------------------------------------------
alter table public.aportes
  add column run_id             uuid,
  add column entity_type        text,
  add column dedup_hash         varchar(64),
  add column dedup_version      text,
  add column block_keys         text[],
  add column content_hash       varchar(64),
  add column consolidated_at    timestamptz,
  add column source_record_id   text,
  add column source_url         text,
  add column parser_version     text,
  add column normalizer_version text,
  add column raw_artifact_id    uuid;

create table public.source_watermarks (
  source_slug  text primary key
                 references public.sources(slug) on delete cascade,
  watermark_at timestamptz not null default '1970-01-01T00:00:00Z',
  updated_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 0016_aportes_trust_tier.sql: quality columns for winner-selection
-- ---------------------------------------------------------------------------
alter table public.aportes
  add column trust_tier       smallint,
  add column fetched_at       timestamptz,
  add column confidence_score numeric(4, 3);

-- ---------------------------------------------------------------------------
-- 0004_dedup_schema.sql: canonical tables
-- ---------------------------------------------------------------------------
create table public.events (
  event_id        uuid primary key default gen_random_uuid(),
  name            varchar(255) not null,
  event_type      varchar(50)  not null,
  occurred_at     timestamptz  not null,
  affected_states jsonb,
  magnitude       numeric(4,2),
  depth_km        numeric(6,2),
  status          varchar(30)  not null,
  external_ids    jsonb
);

create table public.persons (
  person_record_id    uuid primary key default gen_random_uuid(),
  event_id            uuid not null references public.events(event_id) on delete cascade,
  full_name           varchar(300),
  alternate_names     jsonb,
  cedula_hmac         varchar(64),
  cedula_masked       varchar(15),
  age_range           jsonb,
  sex                 varchar(10) check (sex is null or sex in ('M', 'F', 'unknown')),
  is_minor            boolean,
  last_known_location jsonb,
  status              varchar(30) not null,
  verification_status varchar(30) not null,
  confidence_score    numeric(4,3) not null default 0.000,
  source_url          text
);

create table public.acopio_centers (
  acopio_id        uuid primary key default gen_random_uuid(),
  event_id         uuid not null references public.events(event_id) on delete cascade,
  name             varchar(300) not null,
  location         jsonb,
  confidence_score numeric(4,3) not null default 0.000,
  status           varchar(30) not null,
  needs            jsonb,
  last_verified_at timestamptz,
  managing_org     varchar(255),
  contact_hmac     varchar(64),
  contact_masked   varchar(30),
  capacity         integer,
  current_load     integer
);

-- ---------------------------------------------------------------------------
-- 0009_dedup_consolidation.sql: dedup_hash and review queues
-- ---------------------------------------------------------------------------
alter table public.events
  add column dedup_hash varchar(64);
alter table public.acopio_centers
  add column dedup_hash varchar(64);

create unique index events_dedup_uniq
  on public.events (dedup_hash);
create unique index acopio_centers_dedup_uniq
  on public.acopio_centers (dedup_hash);

-- ---------------------------------------------------------------------------
-- 0017_aportes_unique_source_external.sql: PostgREST ingest upsert target
-- ---------------------------------------------------------------------------
create unique index aportes_source_external_uniq
  on public.aportes (source_id, external_id);

create table public.dedup_candidates (
  candidate_id  uuid primary key default gen_random_uuid(),
  event_id      uuid not null references public.events(event_id) on delete cascade,
  left_person   uuid not null references public.persons(person_record_id) on delete cascade,
  right_person  uuid not null references public.persons(person_record_id) on delete cascade,
  score         numeric(4,3) not null check (score >= 0 and score <= 1),
  reasons       jsonb,
  priority      text not null
                  check (priority in ('high', 'medium', 'low')),
  decision      text not null default 'pending'
                  check (decision in ('pending', 'merged', 'rejected', 'deferred')),
  created_at    timestamptz not null default now(),
  check (left_person <> right_person)
);

create unique index dedup_candidates_pair_uniq
  on public.dedup_candidates (least(left_person, right_person), greatest(left_person, right_person));

create table public.dedup_decisions (
  id           uuid primary key default gen_random_uuid(),
  aporte_id    uuid references public.aportes(id) on delete set null,
  entity_type  text not null,
  decision     text not null,
  reason       text,
  canonical_id uuid,
  decided_at   timestamptz not null default now()
);
