# VZLA_DEDUP — Pipeline técnico

Este documento describe el flujo técnico del pipeline de VZLA_DEDUP.

El objetivo es recolectar registros dispersos, convertirlos en entidades tipadas, proteger datos sensibles, normalizarlos y enviarlos a staging en Supabase para que el consolidation job los deduplique y los mueva a las tablas canónicas.

---

## Flujo completo

```
Fuentes externas
      ↓
Adapters (fetch raw)
      ↓
Parsers (raw → entidad tipada)
      ↓
PII masking (HMAC antes que nada)
      ↓
Normalización (texto, fechas, ubicaciones)
      ↓
Claves de dedup pre-calculadas (dedup_hash, block_keys)
      ↓
┌─────────────────────────────┐     ┌──────────────────────┐
│  Raw DB (R2 + Supabase)     │     │  Quarantine DB        │
│  Payload enmascarado,       │ ←── │  Sin parser, PII      │
│  inmutable, trazable        │     │  no redactable, etc.  │
└─────────────────────────────┘     └──────────────────────┘
      ↓
Staging exporter → POST /api/v1/dedup/* → aportes (Supabase)
      ↓  consolidation job (cada 20 min)
Canonical: persons / events / acopio_centers
      ↓  build job (cada 30 min)
Cloudflare D1 → Worker → API pública
```

---

## Capa 1 — Adapters

Cada tipo de fuente tiene un adapter dedicado. El adapter solo hace fetch: devuelve un `RawContent` con el payload crudo y metadatos del request (status HTTP, timestamp, hash del contenido). No interpreta ni transforma nada.

| Tipo | Módulo | Estado |
|------|--------|--------|
| `api_json` | `scrapers/adapters/api_adapter.py` | ✅ httpx, paginación automática, retry |
| `html_static` | `scrapers/adapters/html_adapter.py` | ✅ BeautifulSoup |
| `webapp_js` | `scrapers/adapters/playwright_adapter.py` | ✅ Playwright headless |
| `pdf` / `manual_file` | `scrapers/adapters/pdf_adapter.py` / `local_file.py` | ✅ pdfplumber |
| `rss` | `scrapers/adapters/rss_adapter.py` | ⏳ PR #100 |

Helpers compartidos entre adapters (timestamp UTC, hash de contenido, backoff exponencial) viven en `scrapers/adapters/_shared.py`.

---

## Capa 2 — Parsers

Cada fuente tiene un parser específico que implementa `ParserProtocol`. El parser recibe el `RawContent` del adapter y devuelve `list[Person | AcopioCenter | Event]`.

El parser conoce la estructura de su fuente: qué campo es el nombre, qué campo es la cédula, qué valor de status mapea a qué enum.

**Agregar una fuente nueva = escribir un parser nuevo.** El resto del pipeline no cambia.

| Parser | Módulo | Entidad | Estado |
|--------|--------|---------|--------|
| `encuentralos` | `scrapers/parsers/encuentralos_parser.py` | `Person` | ✅ |

Si una fuente no tiene parser asignado, sus registros van a **cuarentena** — no al basura, no a un fallback genérico. El FallbackParser fue eliminado.

---

## Capa 3 — Limpieza (orden fijo e inamovible)

### 3.1 PII — va primero

Cédulas y teléfonos se HMAC antes de cualquier otro procesamiento. El campo original no se guarda en ningún lugar.

- `cedula_hmac` = `shared/hashing.identity_token(cedula, secret)` → hex puro 64 chars, sin prefijo
- `cedula_masked` = últimos 4 dígitos con máscara (`V-****5821`)
- `telefono_contacto` de terceros se descarta explícitamente (familiar que reportó)

El secreto viene de `PII_HMAC_SECRET` (env var). Sin él, el pipeline no produce HMAC — los campos quedan `None`. En CI offline esto está aceptado; en producción es obligatorio.

### 3.2 Normalización — va antes de dedup

El matching necesita texto uniforme. `"JOSE LUIS"` y `"José Luis"` deben ser el mismo registro antes de comparar.

- **Texto:** unicode, tildes, mayúsculas, espacios, abreviaciones venezolanas (`ve_abbreviations.json`)
- **Fechas:** todo a ISO 8601 UTC (`normalize_date`)
- **Ubicaciones:** nombre normalizado + coordenadas opcionales via OpenStreetMap (`normalize_location`). Si la API falla, `lat/lng = null`; el registro no se descarta
- **NLP:** para fuentes de texto libre (PDFs, HTML narrativo), `spaCy es_core_news_sm` extrae entidades antes del mapeo

### 3.3 Claves de dedup — se calculan aquí, antes de enviar a staging

- `dedup_hash` — SHA-256 del contenido normalizado. Para Event y AcopioCenter, dos registros con el mismo hash son duplicados exactos.
- `block_keys` — para Person: fonética del nombre (Double Metaphone / NYSIIS) + primeras letras + estado. Permite agrupar candidatos sin comparar todos contra todos.

---

## Capa 4 — Staging exporter (Issue #81)

Lee las entidades procesadas y hace `POST /api/v1/dedup/*` a `dataVenezuela`.

Responsabilidades del exporter:
- Convertir `trust_tier` A/B/C/D → 1/2/3 (la BD usa enteros)
- Enviar primero el Event si la entidad es Person o AcopioCenter (FK obligatoria)
- Manejar reintentos y backoff en caso de error de red
- Avanzar el watermark por fuente solo cuando todos los registros de esa fuente llegaron exitosamente

El exporter no toma decisiones de dedup. Su única responsabilidad es persistir en staging.

---

## Capa 5 — Consolidation job (Issue #82)

Proceso independiente que corre cada 20 minutos. Lee `aportes WHERE consolidated_at IS NULL`.

**Event y AcopioCenter:** dedup automática por `dedup_hash`. El registro con mayor `trust_tier` gana. La decisión queda en `dedup_decisions`.

**Person:** nunca auto-merge. El job calcula similitud (Jaro-Winkler + HMAC match + rango de edad + ubicación) dentro de bloques fonéticos y genera candidatos en `dedup_candidates`. Un voluntario humano aprueba o rechaza. Cada versión anterior queda en `canonical_record_versions`.

El job es incremental e idempotente: si se interrumpe, la próxima corrida retoma desde donde quedó sin re-procesar lo ya consolidado.

---

## Principios del pipeline

1. Un error en un registro individual no tumba el pipeline.
2. Un error en una fuente entera se loguea y se continúa con la siguiente.
3. Los registros sin parser van a cuarentena, no al basura.
4. La PII se enmascara antes que cualquier persistencia.
5. La dedup de personas no es destructiva: propone, un humano decide.
6. Todo registro mantiene trazabilidad hacia la fuente y el raw artifact.
7. Los campos desconocidos se exportan como `null`, nunca se omiten.
8. El staging exporter avanza el watermark solo cuando confirma entrega.

---

## Ejecución local

```bash
# Tests (deben pasar siempre)
pytest scrapers/tests

# Demo offline con datos sintéticos
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml

# Limitar registros por fuente (útil para desarrollo)
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml --limit 10

# Validar config de fuentes
python -m scrapers.cli validate --config scrapers/config/sources.demo.yaml
```

---

## Estado de implementación

| Componente | Estado |
|-----------|--------|
| Adapters (todos) | ✅ |
| `encuentralos` parser | ✅ |
| PII HMAC (`shared/hashing.py`) | ✅ |
| Normalización (texto, fechas, ubicaciones, NLP) | ✅ |
| Modelos Pydantic (Person/AcopioCenter/Event) | ⏳ fix #85 pendiente |
| Staging exporter | ❌ Issue #81 |
| Raw artifact store (R2) | ❌ bloqueado por #81 |
| Quarantine DB | ❌ bloqueado por #81 |
| Watermark por fuente | ❌ Issue #57, bloqueado por #81 |
| Consolidation job | ❌ Issue #82, bloqueado por #81 |
| Build job (Supabase → D1) | ❌ bloqueado por canonical |
| Cloudflare Worker | ❌ bloqueado por build job |