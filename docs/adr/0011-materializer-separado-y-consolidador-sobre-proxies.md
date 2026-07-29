# ADR 0011 — Materializer separado y consolidador sobre proxies tipados

| Campo | Valor |
|---|---|
| Estado | Aceptada |
| Fecha | 2026-07-29 |
| Decisores | DB/API, Scrapers/Cleaners, Verification |
| Reemplaza a | [ADR 0010](./0010-fusion-uniforme-por-aristas.md) §2 punto 1 (`events` 1:1 por aporte) — `events` **vuelve a ser catálogo compartido**, restaurando [ADR 0007](./0007-modelo-consolidacion-gold.md) §2.1 |
| Enmienda a | [ADR 0007](./0007-modelo-consolidacion-gold.md) §2 (el materializer deja de ser etapa del cron de consolidación; el consolidador lee los proxies tipados, no `raw_json`) |
| Complementa | [ADR 0007](./0007-modelo-consolidacion-gold.md) (resto del modelo medallón y regla de oro §8) y [ADR 0010](./0010-fusion-uniforme-por-aristas.md) (la compuerta uniforme de §3 y el retiro del auto-merge siguen vigentes) |
| Relacionado con | `docs/pipeline.md`, `docs/schema.md`, `docs/specs/person-dedup.md`, PR #334, #336, #337, #338 (#319 queda absorbido por #338); fuera de alcance: #320/#321 |

---

## 1. Contexto

La arquitectura pretendida del medallón es: `aportes` es el **sobre de staging**
que escribe el pipeline de scraping, y las subtablas tipadas (`persons`,
`acopio_centers`, `events`) son proyecciones que actúan como **proxies para el
consolidador** — presentan exactamente los campos que a la consolidación le
importan, de modo que los `entity_type` puedan evolucionar sin tocar `aportes`
ni el pipeline de ingesta.

El código derivó de esa intención en dos puntos:

- El consolidation job **parsea `aportes.raw_json` en Python**, saltándose los
  proxies. Los campos de dominio (`full_name`, `cedula_hmac`,
  `last_known_location`, `age_range`, `status`) ya existen como columnas reales
  en `persons`, pero el job no los usa.
- El materializer (que crea los proxies) corre como **etapa 1 dentro del cron de
  consolidación** (`consolidate.yml`), acoplando la creación del proxy a la
  consolidación, cuando en realidad no actúa sobre los datos: solo proyecta.

Efecto colateral del bypass: el `select=*` arrastra `raw_json` completo en cada
fetch de dedup, y ese peso de payload alimentó el statement timeout del fetch de
compañeros de bloque (SQLSTATE 57014, mitigado en PR #334).

---

## 2. Decisión

1. **El materializer pasa a un workflow propio** (`materialize.yml`, cron
   propio, rol `consolidation_job`). La ingesta queda como escritora pura de
   staging; el cron `consolidate` deja de materializar. (#336)

2. **Cursor con tope de watermark.** El consolidador mantiene su cursor keyset
   `(created_at, id)` en `consolidation_state` sobre el orden de `aportes`, pero
   **nunca lo avanza más allá del watermark de `silver_materialize_state`** (el
   cursor durable del materializer). Así el atraso del materializer es pura
   latencia, nunca filas saltadas. Cero DDL: `persons` no tiene columna de
   timestamp, así que cursar las subtablas directamente es imposible sin DDL; el
   tope de watermark elimina la trampa de la fila materializada tarde que
   quedaría detrás de un cursor ya avanzado. (#337)

3. **Forma de la consulta: conducir `aportes`, embeber el proxy.** El
   consolidador selecciona de `aportes` solo columnas de sobre (`id`,
   `created_at`, `block_keys`) y trae el dominio **únicamente** vía embed del
   proxy tipado (`persons!inner(...)`). `raw_json` no se pide ni se parsea
   nunca. El keyset, el orden y el fetch de compañeros por contención de
   `block_keys` (índice GIN) siguen sobre la tabla conductora ya probada. (#337)

4. **Una sola vuelta de consolidación, una estrategia por `entity_type`**
   (`CandidateGenerator`): person = blocking + similaridad existentes
   (`scrapers/dedup/`); event/acopio = coincidencia exacta de `dedup_hash` →
   arista fuerte (score 1.0). Espeja el patrón de un parser por tipo. La
   compuerta uniforme de ADR 0010 §3 **se mantiene**, y el retiro del auto-merge
   en silver (`on_conflict=dedup_hash` con merge-duplicates) sigue en pie,
   absorbido por #338 (antes #319).

5. **`events` sigue siendo catálogo compartido** — se reemplaza ADR 0010 §2
   punto 1 (events 1:1) y se restaura ADR 0007 §2.1. Las estrategias por hash de
   event/acopio leen **solo columnas del sobre** (`dedup_hash` vive en
   `aportes`), así que no necesitan fila tipada 1:1; las aristas siguen anclando
   el aporte, que es la traza por aporte que 0010 buscaba.

6. **`dedup_candidates` conserva las FK → `aportes.id`** (cero DDL). Para
   person/acopio, el id anclado **es** la PK de la subtabla (PK compartida), así
   que las aristas ya son semánticamente "entre filas del proxy"; lo que se
   corrige es la ruta de lectura, no el destino de la FK. Para `events`
   (catálogo, sin fila 1:1) el aporte es el único ancla uniforme posible.

---

## 3. Modelo / contrato

Lectura del consolidador (person; forma objetivo de #337):

```
GET /rest/v1/aportes
  ?select=id,created_at,block_keys,persons!inner(full_name,cedula_hmac,
          cedula_partial,last_known_location,age_range,status,...)
  &entity_type=eq.person
  &order=created_at.asc,id.asc          -- keyset (created_at, id) > frontera
  LIMIT batch_size                      -- y frontera ≤ watermark del materializer
```

- El fetch de compañeros históricos (`block_keys @> [...]`, troceado, no
  fatal) conserva su forma y su índice GIN; solo cambia el `select`.
- Event/acopio: `select` de sobre solamente (`id, created_at, dedup_hash,
  block_keys`); sin embed.
- Sin cambios en la escritura de aristas: par canónico
  `LEAST/GREATEST + blocking_key`, select-before-insert/update (PostgREST no
  apunta índices de expresión con `on_conflict`), y las actualizaciones jamás
  reescriben `decision` (la revisión humana sobrevive re-corridas).
- Sin cambios en gold (ADR 0007 §3 / ADR 0010 §3): compuerta por fuerza de
  arista, `gold_id` estable, huérfanos.

## 4. Seguridad

- El consolidador deja de traer `raw_json` (que conserva el registro de negocio
  completo): **menos superficie de PII** en memoria y en logs del job de
  consolidación. Las reglas de PII de ADR 0006 no cambian.
- Ninguna fusión destruye evidencia: bronze inmutable, silver 1:1, trazabilidad
  por aporte intacta (las aristas anclan `aportes.id`).

## 5. Consecuencias

**Positivas**

- Los proxies vuelven a ser el contrato del consolidador: un `entity_type` nuevo
  se agrega con parser + subtabla + estrategia, sin tocar `aportes` ni la
  ingesta.
- Payloads de dedup mucho menores (sin `raw_json`): menos presión sobre el
  `statement_timeout` que causó los 57014.
- Materializer y consolidador se observan y reintentan por separado; un fallo de
  proyección ya no tumba (ni se esconde dentro de) la corrida de consolidación.
- Cero DDL en toda la serie (#336–#338).

**Negativas / costos asumidos**

- Si el materializer se atasca, el consolidador se detiene en el watermark —
  correcto pero visible: hay que vigilar dos workflows.
- Una lectura extra por corrida (`silver_materialize_state`).
- `events` queda asimétrico (catálogo vs 1:1). Costo aceptado: la estrategia por
  hash no usa fila tipada y la traza por aporte la da la arista.

## 6. Alternativas consideradas

- **Columna `materialized_at` en las subtablas y cursor sobre ellas.**
  Rechazada: DDL en tres tablas + backfill, y sin ella `persons` no tiene
  timestamp por el que cursar.
- **Conducir `persons` con orden por columna embebida de `aportes`.**
  Rechazada: paginación keyset e `order` sobre columnas embebidas es terreno
  sensible a la versión de PostgREST; el fetch por GIN de `block_keys` también
  pasaría por el embed.
- **Retargetear las FK de `dedup_candidates` a las PK de las subtablas.**
  Rechazada: mismos valores UUID (PK compartida) = DDL sin ganancia, y `events`
  no puede cumplir (sin fila 1:1).
- **Tablas de candidatos por tipo.** Rechazada: 3× tablas, revisión y grants
  para ids que son idénticos de todas formas.
- **`events` 1:1 por aporte (ADR 0010 §2 punto 1).** Rechazada aquí: la
  estrategia por hash no necesita la fila tipada, y el 1:1 exigía el DDL más
  pesado de la serie (re-proyección de filas colapsadas, re-cableado de
  `persons.event_id`) para un beneficio que la arista ya da.

## 7. Plan de implementación

- **#336:** `materialize.yml` propio; `consolidate` deja de materializar.
- **#337:** ruta de lectura del consolidador (conducir `aportes` + embed tipado,
  tope de watermark, sin `raw_json`).
- **#338:** estrategias `CandidateGenerator` por tipo + retiro del auto-merge en
  silver (absorbe #319).
- Orden: este ADR primero (revisión humana); luego #336 ∥ #337; #338 tras #337.
- Fuera de alcance: el writer de gold clustering (#320/#321), sin cambios.

## 8. Regla de oro

Se reafirma ADR 0007 §8 y ADR 0010 §8: **silver nunca colapsa; la fusión vive
solo en gold**. Y se añade la regla de esta ADR: **el dominio entra al
consolidador solo por los proxies tipados** — el consolidador no lee `raw_json`.
