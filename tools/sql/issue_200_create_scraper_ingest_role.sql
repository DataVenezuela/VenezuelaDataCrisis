-- Issue #200: Rol dedicado scraper_ingest + grants mínimos.
--
-- Requisitos previos en dataVenezuela (backend):
--   - Migración: RLS policies FOR INSERT/UPDATE TO scraper_ingest
--     sobre public.aportes y public.source_watermarks
--   - Migración: UNIQUE(source_id, external_id) en public.aportes
--     (necesario para que on_conflict=source_id,external_id funcione)
--   - Migración que agregue las columnas trust_tier, fetched_at,
--     confidence_score a public.aportes (para el consolidation job)
--
-- Este script crea:
--   1. El rol scraper_ingest (NOLOGIN, el SET ROLE lo hace PostgREST)
--   2. La membresía a authenticator (obligatorio para SET ROLE)
--   3. Grants mínimos sobre aportes, source_watermarks y sources
--   4. La fila del scraper en public.scrapers (necesaria si
--      aportes.scraper_id es FK a scrapers)
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
-- Ejecutar en el SQL Editor de Supabase ANTES del primer deploy del PR.

-- 1. Rol dedicado
CREATE ROLE scraper_ingest WITH NOLOGIN NOINHERIT NOBYPASSRLS;

-- PostgREST autentica como authenticator y hace SET ROLE al claim del JWT.
GRANT scraper_ingest TO authenticator;

GRANT USAGE ON SCHEMA public TO scraper_ingest;
GRANT INSERT, UPDATE ON public.aportes TO scraper_ingest;
GRANT INSERT, UPDATE ON public.source_watermarks TO scraper_ingest;
GRANT SELECT ON public.source_watermarks TO scraper_ingest;
GRANT SELECT ON public.sources TO scraper_ingest;

-- 2. Fila del scraper en public.scrapers (necesaria si aportes.scraper_id
--    es FK a scrapers). El UUID debe coincidir con _SCRAPER_ID en
--    scrapers/exporters/staging_exporter.py.
INSERT INTO public.scrapers (id, name, slug)
VALUES ('00000000-0000-0000-0000-000000000001', 'VZLA_DEDUP pipeline', 'vzla_dedup')
ON CONFLICT (id) DO NOTHING;
