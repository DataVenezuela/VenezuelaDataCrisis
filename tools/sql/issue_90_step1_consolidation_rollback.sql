-- Rollback de Issue #90 - Paso 1.
--
-- Revierte solamente los objetos creados por
-- issue_90_step1_consolidation.sql. No toca aportes ni dedup_decisions.

BEGIN;

DROP TABLE IF EXISTS public.dedup_candidates;

DROP INDEX IF EXISTS public.events_dedup_uniq;
DROP INDEX IF EXISTS public.acopio_centers_dedup_uniq;

ALTER TABLE public.events
    DROP COLUMN IF EXISTS dedup_hash;

ALTER TABLE public.acopio_centers
    DROP COLUMN IF EXISTS dedup_hash;

COMMIT;
