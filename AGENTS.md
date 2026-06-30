# AGENTS.md — Contexto operacional para agentes de IA

Este archivo existe para que cualquier agente (Claude Code, Cursor, u otro)
tenga el estado real del proyecto antes de tocar código. La documentación en
`docs/` describe el diseño; este archivo describe **lo que es verdad hoy**,
incluyendo lo que el diseño dice que debería existir pero no existe todavía.

Última actualización: 30 de junio de 2026, tras el primer dump real a
producción.

---

## 0. Antes de tocar nada

- Lee `CONTRIBUTING.MD` para el flujo de PR y las reglas de seguridad.
- Si vas a resolver un issue, usa el skill `.claude/skills/resolve-issue/`.
- Este repo maneja datos de personas desaparecidas en una crisis activa.
  Nunca asumas que un campo "no importa" — si tocás PII, protección de
  menores, o dedup, para y pregunta si no hay un issue explícito que lo
  cubra.

---

## 1. Estado real de producción (no confundir con el diseño en docs/)

El pipeline corre en producción desde el 30 de junio de 2026. Esto es lo que
**ya funciona**, verificado en producción, no solo en tests:

- `encuentralos_tecnosoft` está conectado de punta a punta: fetch → parse →
  PII → normalización → POST a `dataVenezuela` → tabla `aportes` en Supabase.
- El watermark filtering (`updated_after`) está activo — confirmado en logs
  de producción (`#57`/`#130`/`#131` mergeados).
- Autenticación con `x-api-key` funcionando contra `dataVenezuela`.
- `ingest.yml` ya invoca `python -m scrapers.cli --verbose ingest` — el
  progreso del fetch (páginas descargadas, entidades parseadas) sí se ve en
  los logs de GitHub Actions.

## 2. Brechas operacionales activas — leer antes de tocar estos módulos

### 2.1 `page_size` está hardcodeado, el YAML lo ignora silenciosamente

`docs/source_config.md` documenta un bloque `pagination.page_size` en el
YAML de fuentes. **Ese campo no existe en el código.** `SourceConfig`
(`scrapers/models/source.py`) no tiene el campo, y `_get_adapter` en
`scrapers/pipelines/run_pipeline.py` instancia `ApiAdapter` sin pasar
`page_size`, así que siempre usa el default de `api_adapter.py`
(`_DEFAULT_PAGE_SIZE = 20`).

Si agregás `pagination:` a un YAML de fuente esperando que cambie el
comportamiento, no va a pasar nada — el loader lo ignora sin error.

**Impacto real medido:** `encuentralos_tecnosoft` tiene ~98.830 registros,
no los ~290 que dice la nota del YAML (esa nota quedó desactualizada cuando
la fuente escaló). Con `page_size=20` eso son ~4.941 páginas. El job de
`ingest.yml` tiene `timeout-minutes: 15` — insuficiente para ese volumen.

**Si te piden resolver esto:** el fix real son dos cosas separadas, no
confundirlas:
1. Agregar `page_size` a `SourceConfig` y pasarlo en `_get_adapter` (reduce
   el número de fetches HTTP).
2. El cuello de botella más grande es el **POST**, no el fetch — el
   exporter manda un POST individual por registro a `/api/aportes`. Subir
   `page_size` no resuelve eso. Cualquier solución de paralelismo en el
   exporter necesita revisión cuidadosa porque toca el watermark
   (`export_source` solo avanza el watermark si *todos* los POST de la
   fuente terminaron en 200/201 — paralelizar sin tocar esa garantía rompe
   la semántica de "at-least-once" documentada en `docs/pipeline.md`).

### 2.2 Variables de entorno reales — no confiar en README.md viejo

El README raíz puede tener referencias desactualizadas a
`DATAVZLA_API_KEY`/`DATAVZLA_BASE_URL`. **Las variables reales que lee
`StagingConfig.from_env()` son:**

```
STAGING_API_KEY    → secret de GitHub Actions
STAGING_BASE_URL   → repo VARIABLE de GitHub Actions (no secret — es una URL pública)
```

`STAGING_SOURCE_SLUG` **no existe como variable consumida por el código.**
El `source_slug` siempre sale de `source.id` en `run_pipeline.py`, nunca de
una env var. Si ves esa variable referenciada en algún workflow o doc
viejo, es dead code — no la recrees.

### 2.3 Infraestructura: Supabase y Vercel son proyectos separados, y eso importa

`dataVenezuela` corre en Vercel; la BD vive en Supabase. **Son
independientes** — mover el proyecto de Supabase a otra organización no
actualiza automáticamente las env vars de Vercel. Si algo que debería
funcionar (según lo que ves en Supabase) sigue fallando con 403 o datos que
no aparecen, sospechá primero de un mismatch entre lo que Vercel tiene
configurado (`SUPABASE_URL`, `PARTNER_API_SALT`) y el proyecto de Supabase
actual.

`PARTNER_API_SALT` vive solo en las env vars de Vercel — no está en ningún
repo ni en Supabase. El hash de las API keys de scraper
(`partner_api_keys.key_hash`) se calcula como
`sha256(api_key + PARTNER_API_SALT)` (ver `dataVenezuela/src/lib/api-keys.ts`).
Si necesitás rotar o generar una key nueva, necesitás ese salt — no se puede
calcular sin acceso a Vercel.

### 2.4 Ownership de fuentes en `dataVenezuela`

La tabla `sources` tiene `owner_id` → FK a `profiles.id`. Si una fuente se
crea por SQL directo sin setear `owner_id`, **tanto
`GET /api/source-watermarks/{slug}` como `POST /api/aportes` devuelven 403**
para esa fuente, sin importar que la `STAGING_API_KEY` sea válida. Esto no
está documentado en ningún lado de `dataVenezuela` — confírmalo con un
query directo a `sources` y `partner_api_keys` antes de asumir que el
problema es del lado del pipeline.

---

## 3. Convenciones que SÍ están bien documentadas y son confiables

Estas partes de `docs/` están verificadas y alineadas con el código — no
hace falta cuestionarlas:

- `docs/pipeline.md` — el flujo de capas (adapters → parsers → PII →
  normalización → dedup keys → staging exporter) es preciso.
- `docs/scrapper_contract.md` — el contrato de parsers es correcto.
- La política de `cedula_hmac` (preserva el prefijo V/E, nunca usa prefijo
  `hmac_sha256:`) está implementada exactamente como se documenta.
- La protección de menores (`is_minor=true` → anula foto, cedula_masked,
  acota ubicación a estado) está implementada y testeada.
- El watermark con margen de seguridad de 5 minutos
  (`_WATERMARK_SAFETY_MARGIN`) está implementado como se documenta.

---

## 4. Checklist de PR review (resumen — ver CONTRIBUTING.MD para el completo)

1. `Person.status` enums en inglés (`missing/found/injured/deceased/unknown`).
2. `cedula_hmac` = 64 hex puro, nunca con prefijo.
3. `trust_tier` = letras `A/B/C/D` en scrapers, nunca enteros.
4. Si el PR toca `staging_exporter.py`, verificar que no rompe la garantía
   de "watermark solo avanza si todos los POST de la fuente fueron 2xx".
5. Si el PR agrega un campo a `SourceConfig`, actualizar
   `docs/source_config.md` en el mismo PR — no dejarlo para después (así
   nació la brecha de `page_size` que describe la sección 2.1).
6. Un PR resuelve una sola cosa.

---

## 5. Dónde preguntar si algo no cuadra

Si encontrás otra discrepancia entre lo que dice `docs/` y lo que hace el
código, no asumas cuál de los dos es correcto — el código es la fuente de
verdad de comportamiento, pero el doc puede reflejar una decisión de diseño
pendiente de implementar. Reportalo explícitamente al usuario en vez de
"corregir" silenciosamente uno u otro.