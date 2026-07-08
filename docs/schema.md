# VZLA_DEDUP — Esquema de base de datos

Este documento es el mirror completo y autoritativo del esquema del plano interno
(Supabase/Postgres): el modelo de datos completo (bronze, silver y gold). Es la
fuente de verdad del esquema para este repo.

El bloque de abajo documenta el `aportes` canónico (el destino de la migración en
curso) y excluye a propósito la tabla `aportes` vieja (legacy), que está siendo
migrada a esta forma. Salvo esa exclusión el mirror es completo; lo único que no
garantiza es el orden de creacion ni que todas las constraints sean ejecutables
tal cual (ver WARNING).

```sql
-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.
-- ADR 0009: la definicion operativa de la fuente vive aca (no en el repo). El
-- scraper resuelve cada fuente por source_id (UUID); el slug se elimino. El
-- watermark de ingesta incremental vive en watermark_at (retiro source_watermarks).
CREATE TABLE public.sources (
  source_id uuid NOT NULL DEFAULT gen_random_uuid(),
  display_name text NOT NULL,
  url text,
  source_type text,
  required_keywords text[] NOT NULL DEFAULT '{}',
  refresh_minutes integer,
  allowed_domains text[],
  page_size integer,
  cursor_field text,
  full_scan boolean NOT NULL DEFAULT false,
  rate_limit_per_minute integer,
  timeout_seconds real,
  max_retries integer,
  probe_limit integer,
  max_concurrent_pages integer,
  max_concurrent_posts integer,
  bulk_size integer,
  watermark_at timestamp with time zone,
  governed_tier USER-DEFINED NOT NULL DEFAULT 'D'::trust_tier,
  tier_set_by uuid,
  tier_set_at timestamp with time zone,
  active boolean NOT NULL DEFAULT true,
  created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  url text,
  source_type text,
  required_keywords ARRAY NOT NULL DEFAULT '{}'::text[],
  refresh_minutes integer,
  allowed_domains ARRAY,
  page_size integer,
  cursor_field text,
  full_scan boolean NOT NULL DEFAULT false,
  rate_limit_per_minute integer,
  timeout_seconds real,
  max_retries integer,
  probe_limit integer,
  max_concurrent_pages integer,
  max_concurrent_posts integer,
  bulk_size integer,
  watermark_at timestamp with time zone,
  CONSTRAINT sources_pkey PRIMARY KEY (source_id)
);
CREATE TABLE public.scrape_runs (
  run_id uuid NOT NULL DEFAULT gen_random_uuid(),
  source_id uuid NOT NULL,
  started_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at timestamp with time zone,
  stats jsonb,
  CONSTRAINT scrape_runs_pkey PRIMARY KEY (run_id),
  CONSTRAINT scrape_runs_source_fk FOREIGN KEY (source_id) REFERENCES public.sources(source_id)
);
CREATE TABLE public.raw_artifacts (
  artifact_id uuid NOT NULL DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL,
  source_url text,
  http_status smallint,
  fetched_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  raw_text text,
  body_hash character varying NOT NULL,
  page integer,
  pii_status USER-DEFINED NOT NULL DEFAULT 'unscanned'::pii_scan_status,
  CONSTRAINT raw_artifacts_pkey PRIMARY KEY (artifact_id),
  CONSTRAINT raw_artifacts_run_id_foreign FOREIGN KEY (run_id) REFERENCES public.scrape_runs(run_id)
);
CREATE TABLE public.aportes (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  entity_type USER-DEFINED NOT NULL,
  raw_json jsonb NOT NULL,
  artifact_id uuid NOT NULL,
  source_record_id text,
  external_id text NOT NULL,
  dedup_hash character varying NOT NULL,
  dedup_version text NOT NULL,
  block_keys jsonb NOT NULL DEFAULT '[]'::jsonb,
  content_hash character varying NOT NULL,
  normalizer_version text,
  created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  source_id uuid NOT NULL,
  CONSTRAINT aportes_pkey PRIMARY KEY (id),
  CONSTRAINT aportes_source_fk FOREIGN KEY (source_id) REFERENCES public.sources(source_id),
  CONSTRAINT aportes_artifact_id_foreign FOREIGN KEY (artifact_id) REFERENCES public.raw_artifacts(artifact_id)
);
CREATE TABLE public.events (
  event_id uuid NOT NULL DEFAULT gen_random_uuid(),
  event_type integer NOT NULL,
  description text,
  occurred_at timestamp with time zone,
  location_text text,
  affected_states jsonb,
  magnitude numeric,
  depth_km numeric,
  status USER-DEFINED NOT NULL DEFAULT 'active'::event_status,
  created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT events_pkey PRIMARY KEY (event_id)
);
CREATE TABLE public.persons (
  person_record_id uuid NOT NULL,
  entity_type USER-DEFINED NOT NULL,
  event_id uuid,
  full_name text NOT NULL,
  alternate_names jsonb,
  cedula_hmac character varying,
  cedula_masked character varying,
  cedula_partial character varying,
  cedula_partial_pattern USER-DEFINED,
  identity_kind USER-DEFINED NOT NULL DEFAULT 'none'::identity_kind,
  pii_provenance USER-DEFINED NOT NULL DEFAULT 'cleartext'::pii_provenance,
  name_truncated boolean NOT NULL DEFAULT false,
  age_range jsonb,
  sex integer,
  is_minor boolean,
  last_known_location jsonb,
  status USER-DEFINED NOT NULL DEFAULT 'missing'::person_status,
  trust_tier USER-DEFINED NOT NULL DEFAULT 'D'::trust_tier,
  dedup_confidence USER-DEFINED NOT NULL DEFAULT 'low'::dedup_confidence,
  confidence_score numeric NOT NULL DEFAULT 0.0,
  CONSTRAINT persons_pkey PRIMARY KEY (person_record_id),
  CONSTRAINT persons_event_id_foreign FOREIGN KEY (event_id) REFERENCES public.events(event_id),
  CONSTRAINT persons_person_record_id_foreign FOREIGN KEY (person_record_id) REFERENCES public.aportes(id)
);
CREATE TABLE public.acopio_centers (
  acopio_id uuid NOT NULL,
  entity_type USER-DEFINED NOT NULL,
  event_id uuid,
  name text NOT NULL,
  location_text text NOT NULL,
  coordinates jsonb,
  status USER-DEFINED NOT NULL DEFAULT 'unverified'::acopio_status,
  trust_tier USER-DEFINED NOT NULL DEFAULT 'D'::trust_tier,
  confidence_score numeric NOT NULL DEFAULT 0.0,
  managing_org text,
  contact_public text,
  current_load integer,
  CONSTRAINT acopio_centers_pkey PRIMARY KEY (acopio_id),
  CONSTRAINT acopio_centers_event_id_foreign FOREIGN KEY (event_id) REFERENCES public.events(event_id),
  CONSTRAINT acopio_centers_acopio_id_foreign FOREIGN KEY (acopio_id) REFERENCES public.aportes(id)
);
CREATE TABLE public.acopio_needs (
  need_id uuid NOT NULL DEFAULT gen_random_uuid(),
  acopio_id uuid NOT NULL,
  type text NOT NULL,
  amount jsonb,
  received numeric,
  created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT acopio_needs_pkey PRIMARY KEY (need_id),
  CONSTRAINT acopio_needs_acopio_id_foreign FOREIGN KEY (acopio_id) REFERENCES public.acopio_centers(acopio_id)
);
-- Cursor durable (una sola fila) del materializer: frontera (created_at, id) del
-- ultimo aporte proyectado a silver. Habilita el paginado keyset incremental
-- (deja de reescanear aportes desde el principio en cada corrida del consolidate).
CREATE TABLE public.silver_materialize_state (
  singleton boolean NOT NULL DEFAULT true,
  cursor_created_at timestamp with time zone,
  cursor_id uuid,
  updated_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT silver_materialize_state_pkey PRIMARY KEY (singleton),
  CONSTRAINT silver_materialize_state_singleton CHECK (singleton)
);
CREATE TABLE public.dedup_candidates (
  candidate_id uuid NOT NULL DEFAULT gen_random_uuid(),
  left_aporte_id uuid NOT NULL,
  right_aporte_id uuid NOT NULL,
  blocking_key text NOT NULL,
  score numeric NOT NULL,
  reasons jsonb,
  priority integer NOT NULL,
  touches_gold boolean NOT NULL,
  decision USER-DEFINED NOT NULL DEFAULT 'pending'::dedup_decision,
  resolved_by uuid,
  second_reviewer uuid,
  created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at timestamp with time zone,
  CONSTRAINT dedup_candidates_pkey PRIMARY KEY (candidate_id),
  CONSTRAINT dedup_candidates_left_aporte_id_foreign FOREIGN KEY (left_aporte_id) REFERENCES public.aportes(id),
  CONSTRAINT dedup_candidates_right_aporte_id_foreign FOREIGN KEY (right_aporte_id) REFERENCES public.aportes(id)
);
CREATE TABLE public.gold_entities (
  gold_id uuid NOT NULL DEFAULT gen_random_uuid(),
  entity_type USER-DEFINED NOT NULL,
  canonical_aporte_id uuid NOT NULL,
  verification_status USER-DEFINED NOT NULL DEFAULT 'unverified'::verification_status,
  verified_by uuid,
  verified_at timestamp with time zone,
  confidence_score numeric NOT NULL,
  superseded_by uuid,
  created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_deduplicated_at timestamp with time zone,
  CONSTRAINT gold_entities_pkey PRIMARY KEY (gold_id),
  CONSTRAINT gold_entities_superseded_by_foreign FOREIGN KEY (superseded_by) REFERENCES public.gold_entities(gold_id)
);
CREATE TABLE public.gold_members (
  gold_id uuid NOT NULL,
  aporte_id uuid NOT NULL,
  via_candidate uuid,
  added_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT gold_members_pkey PRIMARY KEY (gold_id, aporte_id),
  CONSTRAINT gold_members_gold_id_foreign FOREIGN KEY (gold_id) REFERENCES public.gold_entities(gold_id),
  CONSTRAINT gold_members_aporte_id_foreign FOREIGN KEY (aporte_id) REFERENCES public.aportes(id),
  CONSTRAINT gold_members_via_candidate_foreign FOREIGN KEY (via_candidate) REFERENCES public.dedup_candidates(candidate_id)
);
CREATE TABLE public.gold_history (
  history_id bigint NOT NULL DEFAULT nextval('gold_history_history_id_seq'::regclass),
  gold_id uuid NOT NULL,
  action USER-DEFINED NOT NULL,
  detail jsonb,
  actor_kind USER-DEFINED NOT NULL,
  actor_id uuid,
  via_candidate uuid,
  at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT gold_history_pkey PRIMARY KEY (history_id),
  CONSTRAINT gold_history_gold_id_foreign FOREIGN KEY (gold_id) REFERENCES public.gold_entities(gold_id),
  CONSTRAINT gold_history_via_candidate_foreign FOREIGN KEY (via_candidate) REFERENCES public.dedup_candidates(candidate_id)
);
CREATE TABLE public.quarantined_records (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  run_id uuid,
  source_slug text NOT NULL,
  source_url text,
  reason_code USER-DEFINED NOT NULL,
  reason_detail text,
  risk_level USER-DEFINED NOT NULL,
  payload_preview_redacted text,
  payload_hash character varying,
  pii_findings_summary jsonb,
  review_status USER-DEFINED NOT NULL DEFAULT 'pending'::review_status,
  review_decision text,
  retention_until timestamp with time zone,
  approved_at timestamp with time zone,
  quarantined_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT quarantined_records_pkey PRIMARY KEY (id),
  CONSTRAINT quarantined_records_run_id_foreign FOREIGN KEY (run_id) REFERENCES public.scrape_runs(run_id)
);
CREATE TABLE public.reporter_contacts (
  reporter_contact_id uuid NOT NULL DEFAULT gen_random_uuid(),
  person_record_id uuid NOT NULL,
  name_hmac character varying,
  phone_hmac character varying,
  email_hmac character varying,
  cedula_hmac character varying,
  retention_until timestamp with time zone NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT reporter_contacts_pkey PRIMARY KEY (reporter_contact_id),
  CONSTRAINT reporter_person_fk FOREIGN KEY (person_record_id) REFERENCES public.persons(person_record_id)
);
```
