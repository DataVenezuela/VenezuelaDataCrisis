-- FROZEN copy of the REAL dataVenezuela backend schema (DDL) relevant to the
-- consolidation adapter (#91). Source of truth:
--   DataVenezuela/dataVenezuela  supabase/migrations/
--     0001_init.sql                (aportes base columns + PK id)
--     0004_dedup_schema.sql        (events / acopio_centers canonical columns)
--     0008_ingesta_staging_dedup.sql (aportes staging/dedup columns)
--     0009_dedup_consolidation.sql   (dedup_hash + UNIQUE on events/acopio_centers)
--
-- Fetched verbatim on 2026-07-02 via:
--   gh api "repos/DataVenezuela/dataVenezuela/contents/supabase/migrations/<file>" \
--     --jq '.content' | base64 -d
--
-- This file is a TEST FIXTURE, not a runnable migration. The contract test
-- test_supabase_adapter_contract.py parses it to assert the exact column names
-- and slugs the adapter uses actually exist in the backend, so a schema drift
-- fails a test instead of producing a false-green (see #90/#104/#187).
-- If the backend schema changes, re-fetch and update this file + the adapter.

-- ---------------------------------------------------------------------------
-- 0001_init.sql : aportes base
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 0008_ingesta_staging_dedup.sql : aportes staging/dedup columns
-- ---------------------------------------------------------------------------
alter table public.aportes
  add column run_id             uuid,
  add column entity_type        text,         -- event | acopio | person
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

-- ---------------------------------------------------------------------------
-- 0004_dedup_schema.sql : events / acopio_centers canonical columns
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
-- 0009_dedup_consolidation.sql : dedup_hash + UNIQUE for atomic auto-merge
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
-- dedup_candidates: schema real desplegado en Supabase
-- NOTA: la migración pública 0009 usaba left_person/right_person (FK a persons).
-- El schema desplegado usa left_aporte_id/right_aporte_id (FK a aportes.id),
-- blocking_key, priority integer y touches_gold. No tiene event_id.
-- Si el backend publica la migración equivalente, actualizar este bloque.
-- ---------------------------------------------------------------------------
create table public.dedup_candidates (
  candidate_id    uuid primary key default gen_random_uuid(),
  left_aporte_id  uuid not null,
  right_aporte_id uuid not null,
  blocking_key    text not null,
  score           numeric not null,
  reasons         jsonb,
  priority        integer not null,
  touches_gold    boolean not null,
  decision        text not null default 'pending',
  resolved_by     uuid,
  second_reviewer uuid,
  created_at      timestamptz not null default now(),
  resolved_at     timestamptz,
  check (left_aporte_id <> right_aporte_id)
);
