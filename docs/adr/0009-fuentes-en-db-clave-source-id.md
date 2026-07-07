# ADR 0009 — Definiciones de fuentes en la DB, clave por source_id (fin del slug)

| Campo | Valor |
|---|---|
| Estado | Propuesta |
| Fecha | 2026-07-07 |
| Decisores | Mantenedores (mathiasaiva, mayerlim), equipo de pipeline |
| Reemplaza a | (ninguno) |
| Complementa | ADR 0002 (endurecimiento del borde), ADR 0006 (protección de PII en la ingesta) |
| Relacionado con | `docs/schema.md`, `docs/specs/db-scraper-contract.md`, `docs/source_config.md` |

---

## 1. Contexto

Hasta ahora cada fuente se definía por completo en un YAML versionado
(`scrapers/config/sources.*.yaml`): `id` (un slug humano), `name`, `url`,
`required_keywords`, `trust_tier`, `parser_asignado`. Cualquiera que leyera el
repo (o su historia de git, que es para siempre) aprendía exactamente qué sitios
scrapeamos. Eso contradice el modelo de amenaza de ADR 0002, que protege contra la
reconstrucción de nuestra lista de fuentes y el doxxing.

El disparador fue una falla real en produccion: el pipeline no lograba resolver la
fuente con `GET /rest/v1/sources?slug=eq.<slug>&select=id`, que devolvía HTTP 400 /
PostgREST `42703 undefined_column`. La causa raíz era un bug de código, no una fila
faltante: la PK de `sources` es `source_id`, pero los exporters seleccionaban `id`
(que no existe). Ese resolver slug -> id era, además, la pieza que forzaba a tener
el slug en el repo.

Dos hechos delicados condicionan el diseño:

- `aportes.external_id` se hashea a partir de la clave de la fuente
  (`sha256(entity | fuente | basis)`), asi que cambiar la clave re-bucketea la
  idempotencia del upsert (`on_conflict=source_id,external_id`).
- El watermark vivía en una tabla aparte `source_watermarks`, keyeada por `slug`.

Como la base está esencialmente vacía (greenfield: el pipeline venía fallando al
resolver la fuente), se puede re-keyear sin backfill.

---

## 2. Decisión

Las **definiciones** de fuente (url, name, type, keywords, tier, refresh, tuning)
se mudan a la tabla `sources` en Supabase. El repo conserva solo un **mapa thin
`uuid -> parser`**: cada entrada del YAML es únicamente `{id: <source_id UUID>,
parser_asignado, enabled}`. Todo se keyea por `source_id` (UUID) y el concepto de
`slug` desaparece.

Puntos de la decisión:

- **El repo no expone la identidad de la fuente, solo su "shape" (el parser).** La
  url/name/keywords viven solo en la DB. El loader
  (`scrapers/sources/loader.py`) resuelve cada entrada thin con
  `GET /rest/v1/sources?source_id=in.(<uuids>)` y fusiona: parser del repo, el
  resto de la DB. `enabled` efectivo = `enabled` del repo AND `active` de la DB.
- **Fallback offline.** Una entrada "completa" (con `url`) se arma directo del
  YAML, sin tocar la red: es la ruta de `sources.demo.yaml` y de los tests. Si el
  config trae fuentes thin y faltan las env SUPABASE_*, se falla cerrado.
- **Se elimina el resolver slug -> id** en `staging_exporter` y
  `provenance_exporter`: `source.id` ya ES el `source_id` UUID, se usa directo en
  el payload de `aportes` y de `scrape_runs`. Esto también arregla el `42703`.
- **El watermark se muda a `sources.watermark_at`** (no hay tabla nueva). Se lee
  con `GET /rest/v1/sources?source_id=eq.<uuid>&select=watermark_at` y se escribe
  con `PATCH /rest/v1/sources?source_id=eq.<uuid>` (la fila ya existe, no es
  upsert). Se retira `source_watermarks`.
- **Nunca se loguea la identidad de la fuente.** El flujo es: GET de la fila por
  UUID, el parser obtiene la `url` y trabaja desde ahí. La `url`, el `display_name`
  y cualquier valor identificador jamás se loguean; los logs solo llevan el
  `source_id` (UUID opaco) más el "shape" (type, parser). Además `httpx`/`httpcore`
  se suben a WARNING en la CLI para que ni siquiera en `--verbose` filtren la URL
  de cada request.
- **`quarantined_records` no se toca.** Recibe el `source_id` (ahora UUID) sin
  cambios de código.

---

## 3. Modelo / contrato

Cambios en la tabla `sources` (el DDL exacto va en el cuerpo del PR; el mantenedor
lo aplica en Supabase, este repo no ejecuta migraciones):

- Se agregan columnas operativas: `url`, `source_type`, `required_keywords`,
  `refresh_minutes`, `watermark_at`, más el tuning (`allowed_domains`, `page_size`,
  `cursor_field`, `full_scan`, `rate_limit_per_minute`, `timeout_seconds`,
  `max_retries`, `probe_limit`, `max_concurrent_pages`, `max_concurrent_posts`,
  `bulk_size`). `display_name` ya cubría "name"; `governed_tier` (A/B/C/D) cubre
  `trust_tier`; `active` cubre el enabled a nivel DB.
- Se elimina la columna `slug`.
- Se retira la tabla `source_watermarks` (su función pasa a `sources.watermark_at`).

`aportes` y `scrape_runs` no cambian de forma: solo cambia el origen del
`source_id` (ya no se resuelve, llega del config). `external_id` se siembra ahora
con el UUID; al ser greenfield no hay filas previas que reconciliar.

---

## 4. Seguridad

- **Identidades fuera de version control y fuera de los logs.** Un repo filtrado (o
  su historia) ya no entrega la lista de fuentes; los logs de CI tampoco.
- **Exposición residual (honesta):** quien tenga la key de lectura de la DB ve las
  fuentes (es la fuente de verdad, esperado); el egress de red sigue mostrando al
  scraper llamando a esos hosts. Esto acota el activo, no lo vuelve anónimo.
- La asignación de parser queda bajo code review (vive en el repo); editar una
  fuente (url/keywords/tier) es una edición en la DB, no en el repo.

---

## 5. Consecuencias

**Positivas**

- La lista de fuentes deja de estar en el repo y en los logs (alineado con ADR 0002).
- Se borra el resolver slug -> id (menos red, menos código) y se arregla el `42703`.
- El watermark deja de necesitar una tabla propia.

**Negativas / costos asumidos**

- Un config thin no corre offline: exige SUPABASE_* para resolver las fuentes (por
  eso se conserva el formato completo para demo/tests).
- Cambiar qué parser maneja una fuente sigue en code review, pero editar la
  definición de la fuente ahora es una operación en la DB (menos trazable en git).
- El watermark en `sources` mezcla estado de corrida con configuración de la fuente
  en una misma tabla; se asume por simplicidad (no crear una tabla nueva).

---

## 6. Alternativas consideradas

- **Solo renombrar el slug a UUID, dejando url/name en el YAML.** Rechazada: no
  esconde nada, la `url` (el verdadero identificador) seguiría en el repo.
- **DB con todo, incluido el parser (repo sin ninguna referencia).** Máximo opsec,
  pero reasignar un parser dejaría de pasar por code review. Se prefirió el mapa
  thin `uuid -> parser` para mantener la asignación de parser bajo revisión.
- **Mantener `source_watermarks` re-keyeada por source_id.** Rechazada: al ser
  greenfield, mover el watermark a `sources.watermark_at` evita una tabla entera.

---

## 7. Plan de implementación

- **Backend (no en este repo):** el `ALTER TABLE sources` (agregar columnas, quitar
  `slug`), `DROP TABLE source_watermarks`, y el seed de las filas reales de
  `sources` (con sus `source_id` generados). El DDL + seed van en el cuerpo del PR;
  el mantenedor los corre en Supabase y comparte los UUIDs.
- **Repo:** loader dual (thin + fallback completo), validador de entradas thin,
  exporters sin resolver, watermark en `sources`, quieting de httpx/httpcore, y el
  config de produccion en formato thin (`sources.custom.yaml`, gitignored;
  `sources.custom.template.yaml` documenta el formato).
- **Verificación:** correr offline con `sources.demo.yaml` (sin SUPABASE_*), la
  suite de tests, y un grep de los logs buscando `url`/`display_name` (debe dar 0).

---

## 8. Regla de oro

El repo describe la **forma** de las fuentes (qué parser las entiende), nunca su
**identidad** (url, nombre). La identidad vive en la DB y se referencia por UUID
opaco; ni el repo ni los logs deben poder reconstruir la lista de fuentes.

---

## 9. Enlaces

- ADR 0002 (endurecimiento del borde), ADR 0006 (protección de PII en la ingesta).
- `docs/schema.md` (columnas de `sources`, `source_watermarks` retirada).
- `docs/specs/db-scraper-contract.md`, `docs/source_config.md`.
- `scrapers/sources/loader.py`, `scrapers/exporters/staging_exporter.py`,
  `scrapers/exporters/provenance_exporter.py`, `scrapers/cli.py`.
