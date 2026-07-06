# AGENTS.md — Contexto operacional para agentes de IA

La documentación en `docs/` describe el diseño; este archivo describe **lo que
es verdad hoy**, incluyendo brechas entre el diseño y el código.

Última actualización: 3 de julio de 2026, tras el fix de max_concurrent_posts
(#212).

---

## Dev commands

```bash
# venv: .venv/ en la raíz
source .venv/bin/activate
pip install -r scrapers/requirements.txt

# Tests
pytest scrapers/tests
pytest scrapers/tests/test_run_pipeline.py -v

# Lint (ruff, config en pyproject.toml)
ruff check .

# Typecheck (solo adapters/parsers exigen --strict)
python -m mypy --strict --follow-imports=silent scrapers/adapters scrapers/parsers

# Run pipeline (demo offline sin credenciales)
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml

# Validate source config
python -m scrapers.cli validate --config scrapers/config/sources.demo.yaml

# Ingest una fuente (produccion)
python -m scrapers.cli --verbose ingest --config <yaml> --source <id> --output-dir scrapers/runtime_output
```

**`--verbose` va ANTES del subcomando** (`-m scrapers.cli --verbose ingest`,
no `-m scrapers.cli ingest --verbose`). Sin él no hay logging.

---

## Commands de CI (corren en orden: pytest → ruff → mypy)

Ver `.github/workflows/ci.yml`:
1. `pytest scrapers/tests` — bloqueante
2. `ruff check .` — bloqueante
3. `mypy --strict --follow-imports=silent scrapers/adapters scrapers/parsers` — bloqueante
4. Bloqueo de archivos `.csv/.jsonl/.pdf/.db/.sqlite/.xlsx` en el diff — bloqueante
5. PII/secret keyword scan en el diff (con allowlist explícita en ci.yml) — bloqueante
6. gitleaks, pip-audit, bandit — informativos (continue-on-error)

---

## Arquitectura

**Entrypoint único:** `scrapers/cli.py` → `scrapers/pipelines/run_pipeline.py`.
Subcomandos: `run`, `ingest`, `validate`, `list-enabled`, `consolidate`.

**Pipeline stages (orden fijo):** Adapter → Parser → PII tokenization →
Enrichment (deterministic_id, location normalisation) → Confidence score →
Minor protection → Staging exporter (POST /api/aportes).

**Solo 1 parser implementado:** `encuentralos` en
`scrapers/parsers/encuentralos_parser.py`. Parser nuevo necesita: implementar
`ParserProtocol` (`scrapers/parsers/base.py`), declararse en
`_get_parser()` en `run_pipeline.py`, y registrarse en YAML como
`parser_asignado`.

**Paquetes:**
- `scrapers/` — pipeline principal. Su `requirements.txt` es la única
  dependencia runtime.
- `shared/` — `hashing.py` (HMAC), `helpers.py`. `config.py` está **vacío**.
- `api/` — esqueleto local, no usado en producción.
- `verification/` — `__init__.py` solamente, no implementado.
- `docs/` — diseño aspiracional. El código manda.

**Workflows CI/CD (`.github/workflows/`):**
- `ci.yml` — PRs a master.
- `ingest.yml` — cada 10 min, matriz de fuentes, timeout 15 min.
- `consolidate.yml` — cada 20 min (no implementado realmente).
- `build_public_index.yml` — cada 30 min (stub, `TODO` en código).

---

## Testing patterns

Tests 100% offline, sin red real:
- Staging (`/api/aportes`) se intercepta con `httpx.BaseTransport`
  inyectado en `StagingExporter` via `_patch_exporter` (ver
  `test_run_pipeline.py:_StagingTransport`).
- Adapters/parsers se mockean con `unittest.mock.patch` sobre
  `_get_adapter`/`_get_parser`.
- `patch.dict(os.environ, {"SUPABASE_URL": "...", "SUPABASE_PUBLISHABLE_KEY": "..."})` para
  credenciales.
- Fixtures sintéticos en `scrapers/tests/fixtures/`. Nunca datos reales.
- Sin `PII_SALT`/`PII_HMAC_SECRET` en CI: `cedula_hmac` queda `None`,
  campos PII crudos se eliminan.

---

## Estado real de producción

El pipeline corre en producción con `encuentralos_tecnosoft` conectado de punta
a punta: fetch → parse → PII → normalización → POST a dataVenezuela → tabla
`aportes` en Supabase. El watermark filtering (`updated_after`) está activo
— confirmado en logs de producción con `#57/#130/#131` mergeados.
`ingest.yml` ya invoca `python -m scrapers.cli --verbose ingest` y el progreso
del fetch (páginas descargadas, entidades parseadas) se ve en los logs de
GitHub Actions.

---

## Operational gotchas

### `page_size` está hardcodeado, el YAML lo ignora silenciosamente

`docs/source_config.md` documenta un bloque `pagination.page_size` en el YAML.
**Ese campo no existe en el código.** `SourceConfig` en
`scrapers/models/source.py` no tiene el campo, y `_get_adapter` en
`run_pipeline.py` instancia `ApiAdapter` sin pasar `page_size`, así que siempre
usa el default de `api_adapter.py` (`_DEFAULT_PAGE_SIZE = 20`). El loader de
YAML traga `pagination:` sin error ni efecto.

**Impacto real medido:** `encuentralos_tecnosoft` tiene ~98.830 registros (no
los ~290 que dice la nota del YAML — esa nota quedó desactualizada cuando la
fuente escaló). Con `page_size=20` son ~4.941 páginas. El job de `ingest.yml`
tiene `timeout-minutes: 15` — insuficiente para ese volumen.

**Si te piden resolver esto:** el fix son dos cosas separadas, no confundirlas:
1. Agregar `page_size` a `SourceConfig` y pasarlo en `_get_adapter` (reduce
   el número de fetches HTTP).
2. El cuello de botella más grande solía ser el **POST**, pero desde #212 el
   exporter envía batches concurrentemente si `max_concurrent_posts > 1`
   en el YAML de la fuente. Usa `ThreadPoolExecutor` internamente y preserva
   la semántica at-least-once (el watermark solo avanza si todos los batches
   terminaron en 200/201). Subir `page_size` ya no es prioritario.

### Variables de entorno reales — no confiar en README.md

El README raíz puede tener referencias desactualizadas a
`DATAVZLA_API_KEY`/`DATAVZLA_BASE_URL`/`STAGING_API_KEY`/`STAGING_BASE_URL`.
**Las variables reales que lee `StagingConfig.from_env()` son:**
- `SUPABASE_URL` — variable de GitHub Actions (URL pública, no secret)
- `SUPABASE_PUBLISHABLE_KEY` — secret de GitHub Actions (pública por diseño, para header `apikey`)
- `SUPABASE_INGEST_JWT` — secret de GitHub Actions (JWT firmado con rol `scraper_ingest`)

`STAGING_SOURCE_SLUG` **no existe como variable consumida por el código.**
El `source_slug` siempre sale de `source.id` en `run_pipeline.py`, nunca de
una env var. Si ves esa variable referenciada en algún workflow o doc viejo,
es dead code — no la recrees.

La migración desde `STAGING_*` a `SUPABASE_*` se hizo en el PR del issue #200.
Si alguna env var antigua aparece en docs o workflows, actualizarla a la nueva.

Auth via custom JWT firmado con `role: scraper_ingest` (`SUPABASE_INGEST_JWT`)
+ publishable key en header `apikey` (`SUPABASE_PUBLISHABLE_KEY`).

El rol `scraper_ingest` se crea con `NOLOGIN NOINHERIT NOBYPASSRLS` y se
concede a `authenticator` para que PostgREST pueda hacer SET ROLE.
Además necesita RLS policies en `aportes` y `source_watermarks` del lado
de dataVenezuela (issue cross-repo).

`PII_SALT` y `PII_HMAC_SECRET` se cargan del **mismo único secret** de
GitHub Actions (`secrets.PII_HMAC_SECRET`, ver `ingest.yml:82-83`). No existe
un `secrets.PII_SALT` separado. Si te piden rotar o auditar secretos, no
busques ni crees un segundo secret. Sin ellas en CI, `cedula_hmac` queda
`None` y los campos PII crudos se eliminan antes de exportar — comportamiento
esperado, el pipeline no falla.

### `shared/config.py` está vacío

No leerlo buscando configuración. La config de staging vive en
`StagingConfig.from_env()` en `scrapers/exporters/staging_exporter.py`.

### Infraestructura: Supabase es el destino directo (Issue #200)

El scraper escribe directo a Supabase via PostgREST, sin pasar por Vercel.
`dataVenezuela` (Vercel) solo sirve el API público de lectura/búsqueda.

`PARTNER_API_SALT` y `sources.owner_id` ya no afectan el path de ingest
porque el scraper usa un JWT firmado con `role: scraper_ingest`, no las
API keys de `partner_api_keys`.

### `ruff check .` exige ruff==0.15.20 (pin en ci.yml)

```bash
pip install ruff==0.15.20
```
Versiones más nuevas pueden diferir en reglas.

---

## Watermark semantics

El watermark persiste `max(fetched_at)` con margen de seguridad de 5 minutos
(`_WATERMARK_SAFETY_MARGIN`). ¿Por qué el margen? `fetched_at` es el
wall-clock local del scraper (cuando terminó de descargar la página), no el
`updated_at` del registro en el servidor de la fuente. Si un registro se
actualiza en el servidor mientras el fetch está en vuelo, la respuesta que ya
recibimos no lo refleja, pero el `fetched_at` que persistimos como watermark
es *posterior* a esa actualización. La siguiente corrida pediría
`updated_after=<ese watermark>` y el servidor excluiría ese registro — quedaría
perdido permanentemente. El margen de 5 minutos crea una ventana de overlap; la
idempotencia por `external_id` en dataVenezuela absorbe los re-envíos sin
duplicar.

El watermark solo avanza si **todos** los POST de la fuente fueron 200/201 **y**
no hubo errores previos (parse, PII, enriquecimiento, minor protection). Esto
garantiza at-least-once delivery.

---

## Convenciones verificadas (docs alineados con código)

Estas partes de `docs/` están verificadas y no hace falta cuestionarlas:
- `docs/pipeline.md` — el flujo de capas (adapters → parsers → PII →
  normalización → dedup keys → staging exporter) es preciso.
- `docs/scrapper_contract.md` — el contrato de parsers es correcto.
- La política de `cedula_hmac` (preserva el prefijo V/E, nunca usa prefijo
  `hmac_sha256:`) está implementada exactamente como se documenta.
- La protección de menores (`is_minor=true` → anula foto, cedula_masked,
  acota ubicación a estado) está implementada y testeada.
- El watermark con margen de seguridad de 5 minutos está implementado como
  se documenta.

---

## Antes de tocar código sensible

Este repo maneja datos de personas desaparecidas en una crisis activa,
incluyendo menores de edad. Si un issue te pide "deduplicar registros",
"ajustar protección de menores", o tocar PII/fotos/ubicaciones exactas,
detenete y confirmá con un issue explícito que cubra el alcance antes de
implementar. No asumas que un campo "no importa". Lee `CONTRIBUTING.MD`
completo — cubre el flujo de PR, las reglas de seguridad y el checklist
de Definition of Done.

## Campos de winner-selection en `pick_winner` (issue #214)

`pick_winner` usa tres campos para elegir el aporte ganador dentro de un grupo
de duplicados. Ninguno vive como columna en `aportes`; llegan via PostgREST
embedding al momento de la consolidación:

| Campo | Origen en el schema | Formato |
|---|---|---|
| `trust_tier` | `sources.governed_tier` (FK `aportes.source_id`) | letra A/B/C/D |
| `fetched_at` | `raw_artifacts.fetched_at` (FK `aportes.artifact_id`) | ISO-8601 UTC |
| `confidence_score` | calculado en consolidación, escrito en `persons`/`acopio_centers` | float [0..1] |

El adapter degrada de forma segura si algún campo falta: tier vacío → rango peor
(`_UNKNOWN_TIER_RANK`); `fetched_at`/`confidence_score` ausentes → desempate neutro.

---

## Non-negotiables (resumen — ver CONTRIBUTING.md para el completo)

- `Person.status` enums en inglés: `missing/found/injured/deceased/unknown`
- `cedula_hmac` = 64 hex puro, **sin prefijo** `hmac_sha256:`
- `trust_tier` en scrapers = letras `A/B/C/D`, nunca enteros
- Nunca commitees datos reales (personas, cédulas, PDFs, CSVs, JSONL)
- Nunca loguees PII (cédulas, teléfonos, direcciones, secretos)
- `--verbose` habilita logging DEBUG — revisa que no filtre PII
- Si agregás un campo a `SourceConfig`, actualizá `docs/source_config.md`
  en el mismo PR (así nació la brecha de `page_size`)
- Un PR resuelve una sola cosa

---

## Si docs/ y código discrepan

El código es la fuente de verdad de comportamiento. El doc puede reflejar
una decisión de diseño pendiente de implementar. Reportá la discrepancia
explícitamente en vez de "corregir" silenciosamente uno u otro.
