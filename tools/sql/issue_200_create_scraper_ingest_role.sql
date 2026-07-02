-- Issue #200: Rol dedicado scraper_ingest + grants mínimos.
--
-- Requisitos previos en dataVenezuela (backend):
--   - Migración: RLS policies FOR INSERT/UPDATE TO scraper_ingest
--     sobre public.aportes y public.source_watermarks
--   - Migración: UNIQUE(source_id, external_id) en public.aportes
--
-- Requisitos de datos (ejecutar ANTES del primer deploy del PR):
--   - INSERT INTO scrapers (id, slug) VALUES
--     ('00000000-0000-0000-0000-000000000001', 'vzla_dedup_pipeline')
--     ON CONFLICT DO NOTHING;
--   - Cada fuente en sources debe existir antes del primer ingest.
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
GRANT SELECT ON public.source_watermarks TO scraper_ingest;
GRANT SELECT ON public.sources TO scraper_ingest;

-- Seed scraper_id usado por el pipeline. Si no existe esta fila y
-- public.aportes.scraper_id es FK a scrapers, todos los INSERT fallan.
INSERT INTO scrapers (id, slug, name)
VALUES ('00000000-0000-0000-0000-000000000001', 'vzla_dedup_pipeline', 'VZLA DEDUP Pipeline')
ON CONFLICT (id) DO NOTHING;
