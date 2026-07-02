-- Issue #200: Rol dedicado scraper_ingest + grants mínimos.
--
-- Requisitos previos en dataVenezuela (backend):
--   - Migración: RLS policies FOR INSERT/UPDATE TO scraper_ingest
--     sobre public.aportes y public.source_watermarks
--   - Migración: UNIQUE(source_id, external_id) en public.aportes
--
-- Generar el JWT:
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
--
-- Ejecutar en el SQL Editor de Supabase ANTES del primer deploy del PR.

CREATE ROLE scraper_ingest WITH NOLOGIN NOINHERIT NOBYPASSRLS;

-- PostgREST autentica como authenticator y hace SET ROLE al claim del JWT.
GRANT scraper_ingest TO authenticator;

GRANT USAGE ON SCHEMA public TO scraper_ingest;
GRANT INSERT, UPDATE ON public.aportes TO scraper_ingest;
GRANT INSERT, UPDATE ON public.source_watermarks TO scraper_ingest;
GRANT SELECT ON public.sources TO scraper_ingest;
