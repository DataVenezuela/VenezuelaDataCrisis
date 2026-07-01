-- Issue #90 - Paso 1: preparar consolidation/dedup en schema existente.
--
-- Scope intencional:
-- - agrega dedup_hash a public.events y public.acopio_centers;
-- - crea public.dedup_candidates para candidatos de Person, incluyendo
--   blocking_key informativa emitida por el pipeline de dedup;
-- - no toca aportes;
-- - no crea dedup_decisions.
--
-- PostgreSQL permite multiples NULL en indices UNIQUE, por eso las filas
-- historicas sin dedup_hash no bloquean la creacion de estos indices.
-- PostgreSQL 14+ soporta gen_random_uuid(); si staging lo deshabilita,
-- habilitar pgcrypto debe hacerse segun la politica de extensiones de la DB.

BEGIN;

ALTER TABLE public.events
    ADD COLUMN IF NOT EXISTS dedup_hash varchar(64);

CREATE UNIQUE INDEX IF NOT EXISTS events_dedup_uniq
    ON public.events (dedup_hash);

ALTER TABLE public.acopio_centers
    ADD COLUMN IF NOT EXISTS dedup_hash varchar(64);

CREATE UNIQUE INDEX IF NOT EXISTS acopio_centers_dedup_uniq
    ON public.acopio_centers (dedup_hash);

CREATE TABLE IF NOT EXISTS public.dedup_candidates (
    candidate_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id varchar(36) NOT NULL REFERENCES public.events(event_id),
    left_person_record_id varchar(36) NOT NULL REFERENCES public.persons(person_record_id),
    right_person_record_id varchar(36) NOT NULL REFERENCES public.persons(person_record_id),
    blocking_key text,
    score numeric(4,3) NOT NULL CHECK (score >= 0 AND score <= 1),
    reasons jsonb,
    priority text NOT NULL,
    decision text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dedup_candidates_no_self_match
        CHECK (left_person_record_id <> right_person_record_id)
);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'dedup_candidates'
          AND column_name = 'left_person'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'dedup_candidates'
          AND column_name = 'left_person_record_id'
    ) THEN
        ALTER TABLE public.dedup_candidates
            RENAME COLUMN left_person TO left_person_record_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'dedup_candidates'
          AND column_name = 'right_person'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'dedup_candidates'
          AND column_name = 'right_person_record_id'
    ) THEN
        ALTER TABLE public.dedup_candidates
            RENAME COLUMN right_person TO right_person_record_id;
    END IF;
END $$;

ALTER TABLE public.dedup_candidates
    ADD COLUMN IF NOT EXISTS blocking_key text;

DROP INDEX IF EXISTS public.dedup_candidates_pair_uniq;

CREATE UNIQUE INDEX IF NOT EXISTS dedup_candidates_pair_blocking_uniq
    ON public.dedup_candidates (
        LEAST(left_person_record_id, right_person_record_id),
        GREATEST(left_person_record_id, right_person_record_id),
        blocking_key
    );

COMMIT;
