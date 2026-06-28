# VZLA_DEDUP — Plan de implementación del serving público

Este documento desarrolla el §12 de
[`docs/adr/0001-arquitectura-serving-publico.md`](./adr/0001-arquitectura-serving-publico.md)
en fases ejecutables. No reabre la decisión de arquitectura; la implementa.

Principio rector (de `docs/base-standards.md §1`): **pasos pequeños, una cosa a la
vez, con tests**. Cada fase es mergeable por separado y deja el sistema en un
estado coherente.

> **Nota (jun 2026):** El diseño de `tools/ingest_supabase/` descrito en este plan
> fue reemplazado en #81. La ingesta a Supabase ahora vive en
> `scrapers/exporters/staging_exporter.py` via `POST /api/aportes`. Las Fases 2–5
> siguen siendo válidas como referencia de arquitectura objetivo.

---

## Estructura objetivo del repo

```text
serving/                       # Plano público (TypeScript / Cloudflare Workers)
├── src/
│   ├── index.ts               # router + fetch handler
│   ├── routes/                # personas, acopio, events, healthz
│   ├── query.ts               # builders de SQL (FTS5) parametrizados
│   └── cache.ts               # headers de caché por endpoint
├── wrangler.toml              # binding D1, rutas, vars
├── test/                      # tests de contrato (Vitest + Miniflare)
└── README.md

tools/
├── ingest_supabase/           # Ingesta JSONL → Supabase (invocado por scrapers.yml)
│   ├── __init__.py
│   ├── cli.py                 # punto de entrada: lee runtime_output/*.jsonl → upsert
│   ├── ingestor.py            # lógica de upsert por PK (SQLAlchemy)
│   └── tests/
└── build_public_index/        # Build job (Python — estándar del repo)
    ├── __init__.py
    ├── projection.py          # SELECT proyección pública desde Supabase
    ├── d1_publisher.py        # carga a *_staging + swap atómico en D1
    ├── denylist.py            # aplica eliminación a pedido
    ├── cli.py                 # punto de entrada del job
    └── tests/

.github/workflows/
├── ci.yml                     # tests automáticos en cada PR (mejorado)
├── scrapers.yml               # cron de scrapers + ingesta a Supabase
└── build_public_index.yml     # cron del build job Supabase → D1
```

---

## Fase 0 — Preparación operativa

**Objetivo:** dejar listas las cuentas y secretos antes de escribir código de serving.

Tareas:
- Crear proyecto Cloudflare (Workers + D1 + Turnstile habilitados).
- Crear la base D1 (`wrangler d1 create vzla_public`).
- Registrar secretos, nunca en el repo:
  - `PII_HMAC_SECRET` (mismo valor que usa el pipeline; necesario para lookup por cédula).
  - credenciales de lectura a Supabase para el build job.
- Definir el dominio público y zona en Cloudflare.

GitHub Actions secrets requeridos:

| Secret | Usado por | Descripción |
|--------|-----------|-------------|
| `PII_SALT` | `scrapers.yml` | Salt para HMAC de PII en el pipeline |
| `PII_HMAC_SECRET` | `scrapers.yml`, `build_public_index.yml` | Mismo valor; para lookup server-side |
| `SUPABASE_DB_URL` | `scrapers.yml`, `build_public_index.yml` | PostgreSQL connection string |
| `CLOUDFLARE_API_TOKEN` | `build_public_index.yml` | Token CF con permisos D1:Edit |
| `CLOUDFLARE_ACCOUNT_ID` | `build_public_index.yml` | ID de la cuenta Cloudflare |
| `D1_DATABASE_ID` | `build_public_index.yml` | UUID de la base D1 |

**Regla:** ninguno de estos valores va al repo jamás. Ver ADR §8.

Definición de hecho: `wrangler whoami` y `wrangler d1 list` funcionan; secretos
cargados en Cloudflare y en GitHub Actions; nada de esto vive en el repo.

---

## Fase 1 — Vista pública en el plano interno

**Objetivo:** declarar formalmente qué campos son públicos (la proyección §5 del ADR).

Tareas:
- En `docs/schema.md`, declarar la "vista pública" por entidad (`persons`,
  `acopio_centers`, `events`) listando **solo** los campos seguros del ADR §5.
- Definir en Supabase una **vista de solo lectura** (`public_persons`, etc.) que
  exponga únicamente esos campos. La proyección se hace en la base, no en código,
  para que sea imposible filtrar un campo sensible por descuido.
  - El SQL vive en `tools/sql/public_views.sql`.
- Confirmar que `cedula_hmac` se expone como HEX 64 sin prefijo (contrato de
  `shared/hashing.py::identity_token`) y que la cédula en claro **no** está en la vista.

Acceptance / tests:
- Test que falla si la vista pública incluye cualquier campo de la lista prohibida
  (`base-standards.md §10`).
- `docs/schema.md` y la vista quedan alineados.

Dependencias: ninguna. Puede arrancar de inmediato.

---

## Fase 2 — Build job (`tools/build_public_index`)

**Objetivo:** materializar la vista pública de Supabase en D1, de forma atómica y repetible.

Tareas:
- `projection.py`: lee las vistas públicas de Supabase (SQLAlchemy, solo lectura).
- `d1_publisher.py`:
  1. crea/recarga tablas `*_staging` en D1 via REST API de Cloudflare,
  2. construye índices **FTS5** sobre nombres + claves de bloqueo fonético precomputadas,
  3. **swap atómico** dentro de transacción (reemplaza la tabla en vivo por staging).
- `denylist.py`: excluye de la proyección los `person_record_id` marcados para
  eliminación (derecho al olvido, ADR §7).
- `cli.py`: orquesta `projection → publish → verify` y reporta métricas
  (conteos por entidad, duración) **sin PII** (`docs/pipeline.md §14`).

Acceptance / tests:
- Test con fixtures sintéticos (`base-standards.md §8`): proyección → D1 → query
  devuelve lo esperado.
- Test de **no-PII**: el contenido publicado a D1 no contiene cédula/teléfono en claro.
- Test de swap: durante el reemplazo, una lectura nunca observa estado parcial.
- Test de denylist: un id en denylist no aparece en la salida.

Dependencias: Fase 1.

---

## Fase 3 — Serving Worker (`serving/`)

**Objetivo:** la API pública de solo lectura (contrato v1 del ADR §6).

Tareas:
- `wrangler.toml`: binding a D1, rutas del dominio.
- Endpoints v1:
  - `GET /v1/personas?nombre=&estado=&status=` (FTS5, máx 20 resultados).
  - `GET /v1/personas/{person_record_id}`.
  - `GET /v1/acopio?estado=&needs=`.
  - `GET /v1/events`.
  - `GET /healthz`.
- `query.ts`: SQL parametrizado (siempre `bind`, nunca interpolación) — anti-inyección.
- `cache.ts`: `Cache-Control: public, max-age=120` en respuestas de búsqueda.
- Búsqueda por cédula: HMAC server-side **sin loguear**, exige campo adicional
  (ADR §8). No confirmar existencia a ciegas.

Acceptance / tests (Vitest + Miniflare):
- Contrato de cada endpoint (forma de respuesta, límite de 20, validación de `nombre ≥3`).
- Ningún endpoint expone campos fuera de la proyección §5.
- Logs del Worker no contienen query strings con PII.

Dependencias: Fase 2 (necesita D1 poblada, aunque sea con fixtures).

---

## Fase 4 — Borde: caché, WAF y anti-abuso

**Objetivo:** que el borde absorba los picos y frene el scraping/enumeración.

Tareas:
- Cache rules en Cloudflare alineadas con los `Cache-Control` del Worker.
- Rate-limiting por IP en rutas `/v1/*`.
- Turnstile ante patrones sospechosos (ADR §8).
- Reglas WAF básicas (bloqueo de user-agents de scraping conocidos, límites de tamaño).

Acceptance:
- Prueba de carga sintética: con consultas repetidas, el hit-rate de caché es alto
  y el Worker recibe una fracción del tráfico.
- El rate-limit corta una ráfaga de enumeración.

Dependencias: Fase 3.

---

## Fase 5 — CI/CD y operación

**Objetivo:** que el dato fluya solo, cada ciclo, y la operación sea observable.

Tareas:
- `.github/workflows/scrapers.yml`: cron horario + `workflow_dispatch`, ejecuta
  el pipeline de scrapers e ingesta el resultado a Supabase.
- `.github/workflows/build_public_index.yml`: cron cada 30 min (offset tras scrapers),
  ejecuta `tools/build_public_index` y publica a D1.
- Deploy del Worker vía `wrangler deploy` (manual al inicio; automatizable después).
- Alertas mínimas: el build falló, D1 cerca del tope de tamaño, error-rate del Worker.
- Runbook corto: cómo aplicar una eliminación a pedido (añadir id a denylist → corre el build).

Acceptance:
- El cron corre verde de extremo a extremo en staging con datos sintéticos.
- Una eliminación a pedido se refleja en el plano público en ≤1 ciclo.

Dependencias: Fases 2–4.

---

## Orden y dependencias

```text
Fase 0 ──┐
Fase 1 ──┼─► Fase 2 ─► Fase 3 ─► Fase 4 ─► Fase 5
         │
   (0 y 1 en paralelo)
```

---

## Definición de hecho (global)

```text
Datos públicos = solo proyección sanitizada (ADR §5). Verificado por test.
Sin PII en D1, en logs ni en respuestas.
Contrato API v1 cubierto por tests.
Build job idempotente con swap atómico.
Derecho al olvido propaga en ≤1 ciclo.
Costo en reposo ~0.
docs/ y código alineados (base-standards.md §2).
```

---

## Fuera de alcance de este plan

- Panel/UI de Verification sobre Supabase (plano interno; trabajo aparte).
- Migración a la Alternativa B (FastAPI + SQLite/R2) — solo si D1 queda chico
  (ADR §10, §11). El contrato HTTP v1 no cambiaría.
