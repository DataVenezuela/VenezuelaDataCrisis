# Análisis: límites y timeout del pipeline de ingest

Fecha: 2026-07-01

---

## Problema

El `ingest.yml` tiene timeout de 15 minutos. Para `encuentralos_tecnosoft` (~107k registros, ~1,070 páginas a page_size=100), el job muere antes de completar. Se observan 422 y timeouts.

## Cadena completa del pipeline

```
1. _fetch_pages()    → 1,070 páginas descargadas (4 workers)  ~4 min
2. _parse_pages()    → 107k entidades parseadas               ~segundos
3. _apply_pii()      → 107k registros                         ~segundos
4. _enrich_records() → 107k registros                         ~segundos
5. export_source()   → 107k POSTs SECUENCIALES                ~178 min ← MUERE ACÁ
```

## Cuello de botella #1: POSTs secuenciales

**Archivo:** `scrapers/exporters/staging_exporter.py:391`

```python
workers = max(1, max_concurrent_posts or 0)  # None or 0 = 0 → max(1,0) = 1
```

El staging exporter usa **1 worker por defecto**. Son 107k POSTs individuales a Vercel, secuenciales. A ~100ms por POST = 10,700 segundos = **178 minutos**.

**Solución parcial:** El YAML ya tiene `max_concurrent_posts: 8`, pero 107k/8 = 13,400 POSTs = ~22 min. Seguimos sobre el timeout de 15 min.

**Solución real para producción:** El watermark filtra — si ya avanzó, solo se exportan registros nuevos. El problema solo ocurre en el **primer run** (backfill) o cuando el watermark se resetea.

## Cuello de botella #2: fetch completo antes de limit

**Archivo:** `scrapers/pipelines/run_pipeline.py:487-500`

```python
pages = _fetch_pages(adapter, source, watermark_at)  # descarga TODO
entities, parse_errors = _parse_pages(parser, pages, limit)  # limit aplicado muy tarde
```

El `--limit` **ya existe** en el CLI (`cli.py:168,192`) pero `_fetch_pages` descarga TODAS las páginas antes de que `_parse_pages` aplique el límite. Incluso con `--limit 100`, se descargan 1,070 páginas.

**Solución:** Early termination en `_fetch_pages` — dejar de descargar cuando se estima que hay suficientes registros.

## Cuello de botella #3: ingest.yml no pasa --limit

**Archivo:** `.github/workflows/ingest.yml:92-96`

```yaml
run: |
  python -m scrapers.cli --verbose ingest \
    --config "$CONFIG" \
    --source "${{ matrix.source_id }}" \
    --output-dir scrapers/runtime_output
    # ← no hay --limit
```

## Mecanismos existentes (no usados en CI)

| Mecanismo | Dónde | Granularidad | En CI |
|-----------|-------|-------------|-------|
| `--limit` CLI flag | `cli.py:168,192` | Por fuente, trunca entidades parseadas | **No** — `ingest.yml` no lo pasa |
| `_parse_pages` break | `run_pipeline.py:275-277` | Trunca lista después de parsear | Solo con `--limit` |
| `page_size` en SourceConfig | `source.py:20` | HTTP page size, no record count | Sí (default 100) |
| Dry-run implícito | `staging_exporter.py:355-363` | Sin POST cuando faltan env vars | N/A |
| Job timeout | `ingest.yml:60` | 15 min hard wall | Sí |

## Plan de corrección

### Cambio 1: Early termination en `_fetch_pages` (importante)

```python
def _fetch_pages(adapter, source, updated_after, limit=None):
    pages = []
    estimated_records = 0
    for page in adapter.fetch_all(path, params=params):
        pages.append(page)
        estimated_records += page.get("records_in_page", 0)
        if limit is not None and estimated_records >= limit + page_size:
            break  # buffer extra por si el parser descarta registros
    return pages
```

`adapter.fetch_all()` es un iterator (usa `yield`), así que `break` corta la descarga.

**Riesgo:** Si el parser descarta muchos registros (sin nombre, etc.), podríamos quedarnos cortos. Mitigación: buffer de `limit + page_size`.

### Cambio 2: Input `--limit` en `ingest.yml`

```yaml
inputs:
  limit:
    description: 'Max records per source (empty = all)'
    required: false
    default: ''
```

```yaml
run: |
  LIMIT_FLAG=""
  if [ -n "${{ inputs.limit }}" ]; then
    LIMIT_FLAG="--limit ${{ inputs.limit }}"
  fi
  python -m scrapers.cli --verbose ingest \
    --config "$CONFIG" \
    --source "${{ matrix.source_id }}" \
    --output-dir scrapers/runtime_output \
    $LIMIT_FLAG
```

### Cambio 3: `max_pages` en SourceConfig (futuro, opcional)

Campo YAML para limitar por fuente:

```yaml
- id: encuentralos_tecnosoft
  max_pages: 5  # solo primeras 5 páginas para testing
```

Requiere: `SourceConfig` + `loader.py` + `api_adapter.py` + docs.

## Nota: el problema de producción se resuelve solo

En producción, el watermark filtra la mayoría de registros. Después del primer backfill, cada run solo procesa registros nuevos desde el último watermark. Con `refresh_minutes: 30`, eso son ~30 min de datos = ~几百 registros, no 107k.

El problema de timeout **solo ocurre en:**
1. Primer run (backfill completo)
2. Cuando el watermark se resetea manualmente
3. Cuando la fuente crece mucho entre runs

Para testing local, `--limit N` con el early termination es suficiente.
