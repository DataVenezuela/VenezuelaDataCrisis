-- Issue #90 - Paso 1: preparar consolidation/dedup en schema existente.
--
-- Scope intencional:
-- - agrega dedup_hash a public.events y public.acopio_centers;
-- - crea public.dedup_candidates para candidatos de Person;
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
    event_id uuid NOT NULL REFERENCES public.events(event_id),
    left_person uuid NOT NULL REFERENCES public.persons(person_record_id),
    right_person uuid NOT NULL REFERENCES public.persons(person_record_id),
    score numeric(4,3) NOT NULL,
    reasons jsonb,
    priority text NOT NULL,
    decision text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dedup_candidates_pair_uniq UNIQUE (left_person, right_person)
);

COMMIT;
