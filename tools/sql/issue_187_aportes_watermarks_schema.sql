-- Issue #187 - Referencia de schema para el upsert directo del staging
-- exporter contra Supabase (PostgREST), sin pasar por Vercel/dataVenezuela.
--
-- Scope intencional:
-- - documenta/crea (IF NOT EXISTS, idempotente) las tablas `aportes` y
--   `source_watermarks` que `scrapers/exporters/staging_exporter.py` espera
--   encontrar via /rest/v1/aportes y /rest/v1/source_watermarks;
-- - no toca el consolidation job ni las tablas canonicas
--   (persons/events/acopio_centers), fuera de alcance de #187;
-- - si estas tablas ya existen en el proyecto de Supabase (creadas por
--   dataVenezuela/Vercel antes de #187), este script es un no-op seguro:
--   CREATE TABLE IF NOT EXISTS no falla ni sobreescribe columnas existentes.
--
-- external_id es la columna sobre la que PostgREST hace el upsert
-- (ON CONFLICT (external_id) DO UPDATE via header
-- "Prefer: resolution=merge-duplicates"), por eso es UNIQUE NOT NULL.

BEGIN;

CREATE TABLE IF NOT EXISTS public.aportes (
    id bigserial PRIMARY KEY,
    external_id text UNIQUE NOT NULL,
    run_id text NOT NULL,
    entity_type text NOT NULL,
    dedup_hash text,
    dedup_version text NOT NULL,
    block_keys jsonb,
    content_hash text NOT NULL,
    source_slug text NOT NULL,
    source_record_id text,
    source_url text,
    parser_version text,
    normalizer_version text,
    raw_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_aportes_source_slug
    ON public.aportes (source_slug);

CREATE INDEX IF NOT EXISTS idx_aportes_external_id
    ON public.aportes (external_id);

CREATE TABLE IF NOT EXISTS public.source_watermarks (
    slug text PRIMARY KEY,
    watermark_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMIT;
