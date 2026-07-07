# Spec: Contrato público DB/Scrapers (#231)

> **Estado:** Propuesta
> **CONTRACT_VERSION:** 1.0
> **Issue:** #231
> **Origen:** issue #224 (punto 4), ADR 0003 §8, ADR 0004 (versionado)
> **Fecha:** 2026-07-04

---

## 1. Alcance

Este documento define el contrato entre un scraper del pipeline (`VZLA_DEDUP`)
y la capa de staging en Supabase, tal como lo implementa
`scrapers/exporters/staging_exporter.py`. Cubre el tramo
`list[Person | AcopioCenter | Event] → aportes` (staging en Supabase), aguas
abajo del contrato parser -> entidad.

No cubre:

- Schema completo de producción ni migraciones reales. Esas viven en el repo
  de BD/API (ver ADR 0003), no en este.
- Reglas del consolidation job (lectura de `aportes`, scoring, candidatos).
  Ver `docs/specs/person-dedup.md` para el caso de `Person`.
- Verificación humana.
- Endpoints de la API pública. Ver `docs/adr/0001-arquitectura-serving-publico.md`
  §5-6.
- Contrato parser → entidad (campos, enums, PII), aguas arriba de este documento.

---

## 2. Precondiciones

Antes de que un scraper pueda exportar registros a staging:

1. Debe existir una fila en `sources` cuyo `slug` sea igual a `source.id` del
   YAML de la fuente (`docs/source_config.md`). El exporter la resuelve con:

   ```text
   GET /rest/v1/sources?slug=eq.<slug>&select=source_id
   ```

   Resultado cacheado en memoria por corrida (`_resolve_source_id`). Si no
   existe la fila, `_build_payload` lanza `ValueError` y el registro no se
   envía.

2. Deben estar seteadas las tres variables de entorno `SUPABASE_URL`,
   `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_INGEST_JWT` (`StagingConfig.from_env`):
   - Si **ninguna** está seteada: dry-run intencional (log `INFO`), el
     pipeline sigue, no exporta nada.
   - Si está seteada **alguna pero no todas**: dry-run también, pero se
     loguea `ERROR` con las que faltan (indica config rota, no dev local).
   - `SUPABASE_URL` debe empezar con `https://`; si no, dry-run + `ERROR`
     (nunca se envían credenciales/PII en claro por http).

3. El JWT (`SUPABASE_INGEST_JWT`) debe estar firmado para el rol
   `scraper_ingest`, con permisos de `SELECT` sobre `sources` y
   `source_watermarks`, e `INSERT`/`UPDATE` (upsert) sobre `aportes` y
   `source_watermarks`. Un JWT sin estos grants produce `401`/`403`, que
   `get_watermark`/`_set_watermark` propagan como `PermissionError`
   explícito (no como dry-run silencioso).

Nada de esto lo garantiza el scraper: son condiciones de despliegue/config
que el equipo de DB/API debe dejar satisfechas para que el contrato de este
documento aplique.

---

## 3. Interfaz: auth y transporte

El exporter habla **directo con PostgREST de Supabase**, no con el endpoint
Vercel `/api/aportes` histórico (deprecado; ver docstring de
`staging_exporter.py`: "Reemplaza el export via Vercel (/api/aportes) por
escritura directa a Supabase").

Headers en cada request:

```text
apikey: <SUPABASE_PUBLISHABLE_KEY>
Authorization: Bearer <SUPABASE_INGEST_JWT>
```

Rutas usadas:

| Método | Ruta | Uso |
|---|---|---|
| `GET` | `/rest/v1/sources?slug=eq.<slug>&select=source_id` | Resolver `source_slug → source_id` |
| `GET` | `/rest/v1/source_watermarks?slug=eq.<slug>&select=watermark_at` | Leer el watermark actual de la fuente |
| `POST` | `/rest/v1/aportes?on_conflict=source_id,external_id` | Upsert de registros a staging |
| `POST` | `/rest/v1/source_watermarks?on_conflict=slug` | Upsert del watermark tras exportar |

Los `POST` llevan `Prefer: resolution=merge-duplicates` (más
`return=minimal` en el de `aportes`), para que PostgREST resuelva el
conflicto de `on_conflict` como update-o-insert sin devolver `409`.

---

## 4. Payload de `aportes`

### 4.1 Columnas canónicas de `aportes`

La fuente de verdad del esquema es `docs/schema.md`. El `aportes` canónico tiene
estas columnas: `id`, `entity_type`, `raw_json`, `artifact_id`, `source_record_id`,
`external_id`, `dedup_hash`, `dedup_version`, `block_keys`, `content_hash`,
`normalizer_version`, `created_at`, `source_id`. `id` y `created_at` los genera la
DB; `artifact_id` es **NOT NULL** y referencia `raw_artifacts` (ver nota de hueco
abajo).

### 4.2 Claves que emite `_build_payload` hoy

El exporter produce estas claves (presentes siempre u omitidas cuando no aplican,
nunca enviadas como `null`). La última columna marca si la clave corresponde a una
columna del `aportes` canónico:

| Clave enviada | Obligatoria | Origen | ¿Columna canónica de `aportes`? |
|---|---|---|---|
| `entity_type` | sí | slug en minúscula, ver §5 | sí |
| `external_id` | sí | ver §6 | sí |
| `dedup_version` | sí | `spec.version`, ver §7 | sí |
| `block_keys` | sí | `scrapers/dedup/specs.py::block_keys` | sí |
| `content_hash` | sí | sha256 del record limpio (sin claves `_*`), JSON canónico ordenado | sí |
| `source_id` | sí | resuelto de `sources.slug`, §2 | sí |
| `raw_json` | sí | el record limpio (sin claves internas `_*`) | sí |
| `dedup_hash` | no (el código la omite, ver §6) | omitida si no hay valor (ver §6) | sí, **`NOT NULL`** en canon (ver §4.3) |
| `source_record_id` | no | omitida si `rec["_source_record_id"]` es `None`/vacío | sí |
| `normalizer_version` | no | omitida si `rec["_normalizer_version"]` es `None`/vacío | sí |
| `run_id` | sí | UUID de la corrida (`self.run_id`) | **no** (la corrida vive en `raw_artifacts.run_id` vía `artifact_id`) |
| `scraper_id` | sí | constante fija `_SCRAPER_ID` (UUID) | **no** (no existe en el `aportes` canónico) |
| `source_url` | no | omitida si `rec["_source_url"]` es `None`/vacío | **no** (es columna de `raw_artifacts`) |
| `parser_version` | no | omitida si `rec["_parser_version"]` es `None`/vacío | **no** (el `aportes` canónico usa `normalizer_version`) |

`source_slug` (string) **nunca** viaja en el payload: la DB espera `source_id`
(uuid), no el slug legible del YAML.

### 4.3 Divergencias código vs canon (huecos conocidos)

Estas divergencias son huecos de un diseño nuevo, no del contrato canónico. Se
cierran fuera de este pase de docs (ver el Apéndice del plan de limpieza / issue
de seguimiento #236):

- El exporter emite `run_id`/`scraper_id`/`source_url`/`parser_version`, que **no**
  son columnas del `aportes` canónico. La provenance de corrida y URL vive en
  `raw_artifacts` (referenciada por `aportes.artifact_id`).
- El exporter **no** envía `artifact_id` ni (para `Person` sin `deterministic_id`)
  `dedup_hash`, aunque el canon (`docs/schema.md`) marca ambas `NOT NULL`. Aun así
  producción inserta OK: señal de que la tabla `aportes` viva todavía es la legacy
  (nullability laxa), no el `aportes` canónico. La migración de `aportes` a
  `schema.md` reconcilia esto; hasta entonces `schema.md` describe el destino, no
  el estado vivo de esas dos columnas.
- **Cableado muerto (hoy):** `_source_url`, `_parser_version` y
  `_normalizer_version` se leen pero el pipeline nunca los asigna, así que esas
  claves siempre se omiten. Solo `_entity_type` y `_source_record_id` se pueblan
  de verdad. La columna `contract_version` por fila (ADR 0004) tampoco existe aún.

---

## 5. Enum `entity_type` (PascalCase del parser → slug de la DB)

El parser produce entidades con `_entity_type` en PascalCase. El exporter las traduce a un slug en
minúscula para la columna `aportes.entity_type`:

| `_entity_type` (parser) | `entity_type` (DB) |
|---|---|
| `Person` | `person` |
| `AcopioCenter` | `acopio` |
| `Event` | `event` |

Este mapeo hoy solo vive en `_ENTITY_TYPE_SLUGS` dentro de
`staging_exporter.py` y no estaba documentado en ningún lugar público antes
de este documento.

---

## 6. `external_id` y `dedup_hash` por tipo de entidad

La identidad del aporte es el **registro-fuente**, nunca su contenido ni su
identidad real (`.agents/CONTEXT.md`: "silver nunca colapsa"). `external_id` se
computa igual para **todos** los tipos de entidad (`_aporte_external_id`):

- Si el record trae `_source_record_id` (id de registro nativo de la fuente),
  `external_id` es `sha256("<entity>|<source_slug>|<source_record_id>")`.
- Si no, `external_id` es `sha256("<entity>|<source_slug>|<content_hash>")`,
  donde `content_hash` es el hash del `raw_json` limpio.

Donde `<entity>` es el tipo en minúsculas (`person`, `event`, `acopiocenter`).
Dos registros DISTINTOS de una misma fuente que compartan cédula, fingerprint o
nombre producen así **dos** aportes, no uno: la deduplicación vive en los edges
(`dedup_candidates`) y en gold, jamás en la ingesta a silver.

`dedup_hash` sigue siendo la señal de linkeo por tipo (`specs.dedup_key`):

- **`Event`** y **`AcopioCenter`**: fingerprint determinístico
  (`specs.event_dedup_key` / `specs.acopio_dedup_key`, versión
  `FINGERPRINT_VERSION`). Ya no coincide con `external_id`.
- **`Person`**: el `deterministic_id` (versión `PERSON_ID_VERSION =
  "person-detid-v1"`); si no hay `deterministic_id`, el código **omite**
  `dedup_hash` (no se envía `null`). Ojo: el `aportes` canónico marca
  `dedup_hash` como `NOT NULL` (`docs/schema.md`), así que esta omisión es un
  hueco código-vs-canon (ver §4.3), no un caso soportado por el esquema.

Las señales de dedup (`deterministic_id`, cédula, fonética, fingerprint) siguen
viajando por `block_keys` y `raw_json`: alimentan el linkeo en gold, ya no la
identidad del aporte.

---

## 7. Postcondiciones / garantías

- **Idempotencia**: `on_conflict=source_id,external_id` +
  `resolution=merge-duplicates` garantiza que reenviar el mismo registro
  (mismo `source_id`+`external_id`) nunca crea una fila duplicada; actualiza
  la existente. `ExportResult.duplicates` siempre es `0` con este esquema
  (ya no hay respuesta `409` que contar).
- **`allow_automerge`** (`scrapers/dedup/specs.py`, `DedupSpec`):
  `Event`/`AcopioCenter` tienen `allow_automerge=True` (el consolidation job
  los puede fusionar automáticamente vía `dedup_hash`); `Person` tiene
  `allow_automerge=False` (nunca automerge, siempre pasa por
  `dedup_candidates` y revisión humana, ver `docs/specs/person-dedup.md`).
- **Reintentos**: hasta `_MAX_POST_RETRIES = 4` intentos con backoff
  exponencial en errores de red/timeout y en status `429, 500, 502, 503,
  504`. Un batch rechazado (status no-2xx, no reintentable, o agotó
  reintentos) se reintenta registro por registro antes de darlo por fallido.
- **Avance del watermark (comportamiento real, no el que sugiere el
  docstring del módulo)**: `advance_watermark` (usado por el loop de
  streaming en `run_pipeline.py::_run_source`) avanza el watermark si:
  1. no hubo errores **previos al export** (parseo, PII, enriquecimiento,
     protección de menores, acumulados en `source_errors`), **y**
  2. se envió al menos un registro (`sent > 0`).

  Esto significa que el watermark **puede avanzar aunque algún `POST` a
  `aportes` haya fallado** (esos errores quedan en `result.errors` pero no
  se pliegan de vuelta a `source_errors` antes de llamar
  `advance_watermark`, ver `run_pipeline.py` líneas ~797-807). El docstring
  del módulo dice "el watermark solo avanza si TODOS los batches del source
  terminaron en 200/201"; eso no es lo que hace el código hoy, este
  documento describe el comportamiento real verificado, no el aspiracional.
  Un scraper no debe asumir que un watermark avanzado implica cero pérdida
  de registros en ese ciclo.
  - Se aplica un margen de seguridad de 5 minutos
    (`_WATERMARK_SAFETY_MARGIN`) restado al máximo `fetched_at` del batch,
    para no perder registros que hayan llegado durante el ciclo de scraping.

---

## 8. Downstream (contexto, fuera de este repo)

Modelo downstream previsto (fuera de este repo), en capas: a partir de `aportes`
un materializer proyecta filas tipadas 1:1 en `persons` / `acopio_centers`
(silver, un registro por aporte, PK = `aportes.id`), mientras `events` es un
catálogo compartido (no una proyección 1:1); el consolidation job genera aristas
de candidatos en `dedup_candidates`; y un proceso de clustering por relación
consolida entidades canónicas en `gold_entities` / `gold_members` /
`gold_history`, que es lo que lee el plano público. Silver nunca colapsa; la
fusión vive solo en gold. Ver `CONTRIBUTING.md` ("Dónde aterriza cada cosa") y
`docs/specs/person-dedup.md` para el detalle de cómo se procesa `Person`.

---

## 9. Ejemplo de payload (datos ficticios)

```json
{
  "run_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "entity_type": "person",
  "external_id": "3b4c9e2a1fd82f6a0bc347e1a9f2c8d5e047b3a12f9c6d71e8b405a3c2d1f9e0",
  "dedup_version": "person-detid-v1",
  "block_keys": ["ced:f0e1d2c3-b4a5-6789-0fed-cba987654321:3b4c9e...1f9e0"],
  "content_hash": "9f1c3e8a2b7d6c5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f",
  "source_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "scraper_id": "00000000-0000-0000-0000-000000000001",
  "raw_json": {
    "full_name": "JOSE LUIS PEREZ DEMO",
    "event_id": "f0e1d2c3-b4a5-6789-0fed-cba987654321",
    "status": "missing",
    "fuente": "encuentralos.tecnosoft.dev"
  },
  "source_url": "https://encuentralos.tecnosoft.dev/registro/demo-12345"
}
```

> Nota: el ejemplo muestra lo que emite el exporter **hoy**. `run_id`,
> `scraper_id` y `source_url` **no** son columnas del `aportes` canónico (ver §4.3
> y `docs/schema.md`); `source_url` además viaja ausente por cableado muerto
> (issue #236). El ejemplo no representa el `aportes` canónico, sino la salida
> actual del código a cerrar contra el schema.

---

## 10. Lo que NO garantiza este contrato

- No garantiza acceso de lectura a las tablas canónicas (`persons`,
  `events`, `acopio_centers`), solo a `aportes`, `source_watermarks` y
  `sources`.
- No garantiza acceso de lectura al schema de producción completo desde el rol
  de ingest. La fuente de verdad del esquema es `docs/schema.md` (mirror completo
  y autoritativo), no una copia paralela en este repo (ver `CONTRIBUTING.md`
  "PRs que tocan contrato exporter -> DB").
- No garantiza que un watermark avanzado implica cero pérdida de registros
  en ese ciclo (ver §7).

---

## 11. Referencias

- `scrapers/exporters/staging_exporter.py`
- `scrapers/dedup/specs.py`
- `scrapers/pipelines/run_pipeline.py` (`_run_source`, líneas ~675-807)
- `scrapers/tests/test_staging_contract.py`
- issue #224 (punto 4), ADR 0003 §8 y §12
- `docs/specs/person-dedup.md`
- `docs/source_config.md`
