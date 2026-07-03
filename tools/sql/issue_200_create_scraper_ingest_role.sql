-- Issue #200: Rol dedicado scraper_ingest + grants mínimos.
--
-- PRERREQUISITO BLOQUEANTE: DataVenezuela PR #40 (issue #39) debe estar
-- mergeado y migrado en Supabase ANTES de ejecutar este script.
-- PR #40 agrega las columnas scraper_id y source_id a public.aportes;
-- sin ellas el CREATE UNIQUE INDEX de abajo falla y deja la DB a medio estado.
--
-- Requisitos previos en dataVenezuela (backend):
--   - RLS policies FOR INSERT/UPDATE TO scraper_ingest
--     sobre public.aportes y public.source_watermarks
--   - Columnas trust_tier, fetched_at, confidence_score en public.aportes
--     (para el consolidation job — issue separado)
--
-- Este script crea:
--   1. El rol scraper_ingest (NOLOGIN, el SET ROLE lo hace PostgREST)
--   2. La membresía a authenticator (obligatorio para SET ROLE)
--   3. Grants mínimos sobre aportes, source_watermarks y sources
--   4. Índice único compuesto (source_id, external_id) en public.aportes,
--      requerido por on_conflict=source_id,external_id en el upsert PostgREST
--   5. Fila de bot en auth.users + public.profiles para aportes.scraper_id
--      (aportes.scraper_id es FK a public.profiles(id) → auth.users(id))
--
-- Generar el JWT (una sola vez, offline):
--   import jwt, os
--   from datetime import datetime, timedelta, timezone
--   token = jwt.encode(
--       {"role": "scraper_ingest",
--        "iat": datetime.now(timezone.utc),
--        "exp": datetime.now(timezone.utc) + timedelta(days=365)},
--       os.environ["SUPABASE_JWT_SECRET"],
--       algorithm="HS256"
--   )
--   print(token)
--
-- Guardar como SUPABASE_INGEST_JWT en GitHub Secrets.
-- Guardar SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY también.
--
-- Ejecutar en el SQL Editor de Supabase DESPUÉS de que PR #40 aterrice
-- y ANTES del primer deploy de este PR.
-- El script es transaccional: si cualquier paso falla, nada queda aplicado.

BEGIN;

-- 1. Rol dedicado
CREATE ROLE IF NOT EXISTS scraper_ingest WITH NOLOGIN NOINHERIT NOBYPASSRLS;

-- PostgREST autentica como authenticator y hace SET ROLE al claim del JWT.
GRANT scraper_ingest TO authenticator;

GRANT USAGE ON SCHEMA public TO scraper_ingest;
GRANT INSERT, UPDATE ON public.aportes TO scraper_ingest;
GRANT INSERT, UPDATE ON public.source_watermarks TO scraper_ingest;
GRANT SELECT ON public.source_watermarks TO scraper_ingest;
GRANT SELECT ON public.sources TO scraper_ingest;

-- 2. Índice único compuesto requerido por on_conflict=source_id,external_id.
--    external_id ya tiene UNIQUE individual; este índice cubre el par.
CREATE UNIQUE INDEX IF NOT EXISTS aportes_source_id_external_id_key
    ON public.aportes (source_id, external_id);

-- 3. Fila de bot en auth.users + public.profiles para aportes.scraper_id.
--    La cadena de FKs es: aportes.scraper_id → profiles(id) → auth.users(id).
--    El UUID debe coincidir con _SCRAPER_ID en staging_exporter.py.
INSERT INTO auth.users (
    id,
    email,
    role,
    aud,
    created_at,
    updated_at,
    encrypted_password,
    email_confirmed_at,
    raw_app_meta_data,
    raw_user_meta_data
)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'seed-scraper@internal.local',
    'authenticated',
    'authenticated',
    now(),
    now(),
    '',
    now(),
    '{"provider":"email","providers":["email"]}',
    '{}'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.profiles (id, role)
VALUES ('00000000-0000-0000-0000-000000000001', 'public_submitter')
ON CONFLICT (id) DO NOTHING;

COMMIT;
