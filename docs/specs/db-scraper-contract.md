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
`scrapers/exporters/staging_exporter.py`. Es el contrato que sigue al de
`docs/scrapper_contract.md`: ese documento cubre `RawContent → list[Person |
AcopioCenter | Event]`; este cubre `list[Person | AcopioCenter | Event] →
aportes` (staging en Supabase).

No cubre:

- Schema completo de producción ni migraciones reales. Esas viven en el repo
  de BD/API (ver ADR 0003), no en este.
- Reglas del consolidation job (lectura de `aportes`, scoring, candidatos).
  Ver `docs/specs/person-dedup.md` para el caso de `Person`.
- Verificación humana.
- Endpoints de la API pública. Ver `docs/adr/0001-arquitectura-serving-publico.md`
  §5-6 y la sección "Vista pública" de `docs/schema.md`.
- Contrato parser → entidad (campos, enums, PII). Ver `docs/scrapper_contract.md`.

---

## 2. Precondiciones

Antes de que un scraper pueda exportar registros a staging:

1. Debe existir una fila en `sources` cuyo `slug` sea igual a `source.id` del
   YAML de la fuente (`docs/source_config.md`). El exporter la resuelve con:

   ```text
   GET /rest/v1/sources?slug=eq.<slug>&select=id
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
| `GET` | `/rest/v1/sources?slug=eq.<slug>&select=id` | Resolver `source_slug → source_id` |
| `GET` | `/rest/v1/source_watermarks?slug=eq.<slug>&select=watermark_at` | Leer el watermark actual de la fuente |
| `POST` | `/rest/v1/aportes?on_conflict=source_id,external_id` | Upsert de registros a staging |
| `POST` | `/rest/v1/source_watermarks?on_conflict=slug` | Upsert del watermark tras exportar |

Los `POST` llevan `Prefer: resolution=merge-duplicates` (más
`return=minimal` en el de `aportes`), para que PostgREST resuelva el
conflicto de `on_conflict` como update-o-insert sin devolver `409`.

---

## 4. Payload de `aportes`

Columnas que produce `_build_payload`, según sean siempre presentes u
omitidas cuando no aplican (nunca enviadas como `null`):

| Columna | Obligatoria | Origen |
|---|---|---|
| `run_id` | sí | UUID de la corrida del exporter (`self.run_id`) |
| `entity_type` | sí | slug en minúscula, ver §5 |
| `external_id` | sí | ver §6 |
| `dedup_version` | sí | `spec.version`, ver §7 |
| `block_keys` | sí | `scrapers/dedup/specs.py::block_keys` |
| `content_hash` | sí | sha256 del record limpio (sin claves `_*`), JSON canónico ordenado |
| `source_id` | sí | resuelto de `sources.slug`, §2 |
| `scraper_id` | sí | constante fija `_SCRAPER_ID` (UUID) |
| `raw_json` | sí | el record limpio (sin claves internas `_*`) |
| `dedup_hash` | no | omitida si no hay valor (ver §6) |
| `source_record_id` | no | omitida si `rec["_source_record_id"]` es `None`/vacío |
| `source_url` | no | omitida si `rec["_source_url"]` es `None`/vacío |
| `parser_version` | no | omitida si `rec["_parser_version"]` es `None`/vacío |
| `normalizer_version` | no | omitida si `rec["_normalizer_version"]` es `None`/vacío |

`source_slug` (string) **nunca** viaja en el payload: la DB espera
`source_id` (uuid), no el slug legible del YAML.

**Cableado muerto (hoy):** `_source_url`, `_parser_version` y
`_normalizer_version` se leen aquí, pero el pipeline nunca los asigna, así que las
columnas `source_url`/`parser_version`/`normalizer_version` **siempre** se omiten
(viajan como ausentes, nunca con valor). Es un bug de trazabilidad, no del
contrato: ver el issue de seguimiento (#236). Solo `_entity_type` y
`_source_record_id` se pueblan de verdad. La columna `contract_version` por fila
(ADR 0004) tampoco existe aún: se añade al implementar `contract-v1.0`.

---

## 5. Enum `entity_type` (PascalCase del parser → slug de la DB)

El parser produce entidades con `_entity_type` en PascalCase
(`docs/scrapper_contract.md`). El exporter las traduce a un slug en
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

- **`Event`** y **`AcopioCenter`**: `external_id` y `dedup_hash` son el mismo
  valor, un fingerprint determinístico (`specs.event_dedup_key` /
  `specs.acopio_dedup_key`, versión `FINGERPRINT_VERSION`).
- **`Person`**:
  - Si el record trae `deterministic_id` (`docs/scrapper_contract.md`), se
    usa ese valor como `external_id` (versión `PERSON_ID_VERSION =
    "person-detid-v1"`).
  - Si no, y hay `_source_record_id`, `external_id` es
    `sha256("person|<source_slug>|<source_record_id>")`.
  - Si tampoco hay `_source_record_id`, se cae a un hash del contenido
    limpio combinado con `event_id` y `cedula_hmac` (o solo `event_id` +
    hash de contenido si no hay cédula).
  - `dedup_hash` viene de `specs.dedup_key(rec, "Person")`; si no hay
    `deterministic_id`, la columna se **omite** (no se envía `null`), es
    nullable en la DB justamente para este caso.

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

El consolidation job (no vive en este repo) lee `aportes` cada ~20 minutos y
escribe en las tablas canónicas `persons` / `events` / `acopio_centers`. Ver
`CONTRIBUTING.md` ("Dónde aterriza cada cosa") y `docs/specs/person-dedup.md`
para el detalle de cómo se procesa `Person` específicamente.

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

---

## 10. Lo que NO garantiza este contrato

- No garantiza acceso de lectura a las tablas canónicas (`persons`,
  `events`, `acopio_centers`), solo a `aportes`, `source_watermarks` y
  `sources`.
- No garantiza el schema de producción completo ni las migraciones reales.
  La fuente de verdad de esas sigue siendo el repo de BD/API
  (`CONTRIBUTING.md` "PRs que tocan contrato exporter -> DB"), no una copia
  en este repo.
- No garantiza que un watermark avanzado implica cero pérdida de registros
  en ese ciclo (ver §7).

---

## 11. Referencias

- `scrapers/exporters/staging_exporter.py`
- `scrapers/dedup/specs.py`
- `scrapers/pipelines/run_pipeline.py` (`_run_source`, líneas ~675-807)
- `scrapers/tests/test_staging_contract.py`
- issue #224 (punto 4), ADR 0003 §8 y §12
- `docs/scrapper_contract.md`
- `docs/specs/person-dedup.md`
- `docs/source_config.md`
