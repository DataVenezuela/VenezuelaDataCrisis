# ADR 0008 — Retención de PII en claro en Bronze (raw_artifacts) y reaper de 12h

| Campo | Valor |
|---|---|
| Estado | Propuesta |
| Fecha | 2026-07-07 |
| Decisores | Mantenedores (mathiasaiva, mayerlim), equipo de pipeline |
| Reemplaza a | (ninguno) |
| Complementa | ADR 0002 (endurecimiento del borde), ADR 0006 (protección de PII en la ingesta) |
| Relacionado con | `docs/schema.md`, issue #256 |

---

## 1. Contexto

La capa Bronze (issue #256) le da procedencia verificable a cada aporte: una fila
`scrape_runs` por fuente por corrida y una fila `raw_artifacts` (append-only) por
página fetcheada, referenciada por `aportes.artifact_id` (FK NOT NULL). La columna
`raw_artifacts.raw_text` guarda la página cruda tal como llegó de la fuente.

Ese `raw_text` es el **único PII en claro en reposo** del sistema: el resto del
plano interno ya viaja tokenizado o hasheado (cédula como `cedula_hmac`, teléfonos
descartados, menores reducidos), según ADR 0006. Conservar la página cruda habilita
replay y re-parseo (arreglar un parser y reprocesar sin volver a golpear la fuente,
o auditar qué vio exactamente el scraper), pero contradice de frente el modelo de
amenaza de ADR 0002 y ADR 0006, que protege contra reconstrucción masiva de la base,
doxxing por cédula y fuga de insiders. PII en claro acumulada sin límite es
exactamente el activo que ese modelo busca no crear.

---

## 2. Decisión

`raw_artifacts.raw_text` se retiene un máximo de **12 horas**. Pasada esa ventana,
un job `pg_cron` de backend lo **anula** (no borra la fila):

```sql
UPDATE public.raw_artifacts
   SET raw_text = NULL,
       pii_status = 'purged'
 WHERE raw_text IS NOT NULL
   AND fetched_at < now() - interval '12 hours';
```

Puntos de la decisión:

- **Se anula la columna, no se borra la fila.** `aportes.artifact_id` es NOT NULL y
  su FK a `raw_artifacts` no tiene `ON DELETE`, así que borrar la fila quedaría
  bloqueado en cuanto un aporte la referencie (y los aportes viven mucho más de
  12h). La fila sobrevive como token de procedencia.
- **`body_hash` sobrevive para siempre.** Es el SHA-256 (hex puro) de la página;
  permite verificar qué contenido exacto se vio sin conservar el contenido. La
  auditoría se mantiene aunque el texto ya no exista.
- **El replay queda acotado a 12h.** Reprocesar una página vieja exige volver a
  scrapear la fuente, no leer `raw_text`.
- **El scraper nunca loguea `raw_text`.** `ProvenanceExporter` solo registra
  `body_hash` / `page` / `http_status` en logs; ante un fallo de INSERT no vuelca
  ni el body ni `resp.text`. Los logs no los alcanza el reaper, así que no deben
  contener PII. El transporte exige HTTPS (heredado de `StagingConfig`), de modo que
  `raw_text` nunca viaja en claro por la red.
- **`scrape_runs.stats` solo lleva conteos numéricos** (pages, artifacts, entities,
  sent), nunca strings de error que pudieran arrastrar texto derivado de PII.
  `scrape_runs` no tiene ventana de retención.

---

## 3. Consecuencias

**Positivas**

- Procedencia verificable de cada aporte (`artifact_id`) con exposición de PII en
  claro acotada a una ventana corta y explícita.
- `body_hash` durable: auditoría e idempotencia de contenido sin conservar el texto.
- Replay/re-parseo posible dentro de la ventana de 12h.

**Negativas / costos asumidos**

- Durante hasta 12h hay PII en claro en reposo. Se mitiga con el rol dedicado
  `scraper_ingest` (sin `service_role`), RLS, y el endurecimiento de borde de ADR
  0002. Sigue siendo una superficie que antes no existía.
- Pasadas las 12h el re-parseo exige re-scrapear (la fuente puede haber cambiado o
  desaparecido).
- El reaper es infraestructura de backend (`pg_cron`): debe existir, correr y
  monitorearse. Si no corre, la PII se acumula y la decisión se incumple en silencio.

**Riesgos y mitigaciones**

- *El reaper no corre*: alertar si existe alguna fila con `raw_text IS NOT NULL` y
  `fetched_at < now() - interval '12 hours'` (invariante monitoreable).
- *Fuga durante la ventana*: acceso mínimo, rol dedicado, HTTPS, sin `service_role`
  en el path de ingesta.
- *`pii_status`*: el enum `pii_scan_status` debe incluir el valor de purga
  (`purged`) además de `unscanned`; el default sigue siendo `unscanned`.

---

## 4. Estado / implementación

- **Real hoy (este repo, #256):** `scrapers/exporters/provenance_exporter.py`
  escribe `scrape_runs` + `raw_artifacts` vía PostgREST; el pipeline stampa
  `artifact_id` en cada aporte; `staging_exporter` emite `artifact_id` y dejó de
  emitir `run_id`/`scraper_id`/`source_url`/`parser_version`. El exporter no loguea
  `raw_text`.
- **Backend (no en este repo):** las tablas `scrape_runs` / `raw_artifacts` (ya
  reflejadas en `docs/schema.md`), el valor `purged` del enum `pii_scan_status`, y
  el job `pg_cron` del reaper. El DDL exacto va en el cuerpo del PR de #256; el
  mantenedor lo aplica en Supabase (este repo no ejecuta migraciones).
- **Divergencia conocida (fuera de alcance de #256):**
  `quarantined_records.run_id` referencia `scrape_runs.run_id`, pero el `run_id` que
  hoy comparten `staging_exporter` y `quarantine_exporter` es un uuid de corrida del
  pipeline, no el `run_id` per-fuente que genera `scrape_runs`. Reconciliarlo es
  trabajo de seguimiento.

---

## 5. Enlaces

- ADR 0002 (endurecimiento del borde), ADR 0006 (protección de PII en la ingesta).
- `docs/schema.md` (tablas `scrape_runs` / `raw_artifacts` / `aportes` canónico, payload de aportes con `artifact_id`).
- `scrapers/exporters/provenance_exporter.py`, `scrapers/pipelines/run_pipeline.py`.
- issue #256.
