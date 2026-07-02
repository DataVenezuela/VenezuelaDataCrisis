-- Issue #200: Grants mínimos para que el rol anon (via publishable key de Supabase)
-- pueda escribir directo a Supabase desde el scraper en GitHub Actions.
--
-- ADR 0001 define Supabase como plano interno (nunca recibe tráfico público).
-- El público se sirve desde Cloudflare Worker + D1, asi que no hay riesgo
-- de exponer permisos de escritura al modificar el rol anon.
--
-- Ejecutar en el SQL Editor de Supabase antes del primer deploy del PR.
-- Se puede verificar con:
--   SELECT * FROM information_schema.role_table_grants
--   WHERE grantee = 'anon' AND table_name IN ('aportes','source_watermarks','sources');

GRANT INSERT, UPDATE ON public.aportes TO anon;
GRANT INSERT, UPDATE ON public.source_watermarks TO anon;
GRANT SELECT ON public.sources TO anon;
