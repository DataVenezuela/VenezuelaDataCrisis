-- Issue #200: Rol dedicado scraper_ingest + grants mínimos.
-- Usar SUPABASE_JWT_SECRET del proyecto para firmar un JWT con
-- {"role": "scraper_ingest"} y guardarlo como SUPABASE_INGEST_JWT
-- en GitHub Secrets. El JWT se genera una sola vez offline y PostgREST
-- valida la firma localmente sin requests extra de auth.
--
-- ADR 0001 define Supabase como plano interno (nunca recibe tráfico público).
-- El rol NOBYPASSRLS permite que las políticas RLS existentes sigan vigentes.
--
-- Ejecutar en el SQL Editor de Supabase antes del primer deploy del PR.
--
-- Generar el JWT (Python):
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

CREATE ROLE scraper_ingest WITH LOGIN NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO scraper_ingest;
GRANT INSERT, UPDATE ON public.aportes TO scraper_ingest;
GRANT INSERT, UPDATE ON public.source_watermarks TO scraper_ingest;
GRANT SELECT ON public.sources TO scraper_ingest;
