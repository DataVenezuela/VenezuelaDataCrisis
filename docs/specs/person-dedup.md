# Spec: Person dedup → candidatos (#92)

> **Estado:** Aprobado
> **Issue:** #92 — feat(consolidation): Person dedup → candidatos (reutiliza PR #79)
> **Bloqueado por:** #90 (SQL migration), #81 (staging exporter), PR #79 (coordinar correcciones)
> **Fecha:** 2026-06-28
>
> **Reconciliado (2026-07-06):** Este spec se alineó con el esquema vivo de
> `dedup_candidates` (ver `docs/schema.md`): las aristas referencian `aportes`
> (`left_aporte_id`/`right_aporte_id`), `priority` es entero y existe
> `touches_gold`; no hay columna `event_id`. El código actual
> (`scrapers/dedup/clustering.py`, `scrapers/jobs/consolidation_job.py`) todavía
> emite la forma vieja (`*_person_record_id`, `priority` string, columna
> `event_id`) y debe migrarse a esta forma antes de correr contra la BD real.
>
> **Resuelto (2026-07-19, option B / #93):** el cursor de §2 ya **no** usa
> `aportes.consolidated_at` (columna inexistente en el `aportes` canónico,
> `docs/schema.md`; un probe en vivo confirmó `400 42703`). El código
> (`consolidation_job.py`) pagina ahora por una **frontera durable** persistida
> por `entity_type` en la tabla `consolidation_state` (cursor keyset
> `(created_at, id)`), de modo que cada corrida procesa **solo aportes nuevos**
> (no re-escanea todo ni re-pisa la revisión humana). La completitud
> nuevo-vs-viejo la aporta un **fetch de compañeros de bloque históricos**
> (`fetch_partners_by_block_keys`, por `block_keys` e **ignorando** la frontera):
> un duplicado recién llegado sigue bloqueando contra registros ya procesados. La
> idempotencia de escritura la da `find_existing_candidates`. Si `consolidation_state`
> falta o rechaza el acceso, el cursor degrada a scan completo (no repite el 400).

---

## 1. Objetivo

El consolidation job para Person lee de `aportes` en Supabase, agrupa registros por block keys, compara pares con scoring multi-campo, y produce candidatos de dedup en `dedup_candidates` con `decision = 'pending'`. Las personas **nunca se auto-fusionan** — la revisión humana decide.

---

## 2. Origen de datos

> Nota de canon: la paginación usa el cursor keyset `(created_at, id)`, columnas
> que sí existen en el `aportes` canónico (`docs/schema.md`). No se filtra ni se
> escribe `consolidated_at` (columna inexistente). La **frontera** vive en una
> tabla de estado dedicada (`consolidation_state`), no en una columna de `aportes`.

Lectura por páginas con cursor keyset desde `aportes`, arrancando desde la frontera durable:

```sql
SELECT * FROM aportes
WHERE entity_type = 'person'
  AND (created_at, id) > (:cursor_created_at, :cursor_id)  -- frontera de consolidation_state
ORDER BY created_at ASC, id ASC
LIMIT :batch_size;
```

- **Frontera durable (option B, #93):** se lee de `consolidation_state`
  (`entity_type = 'person'`) al inicio y se persiste al final de cada página. Cada
  corrida procesa **solo aportes nuevos** posteriores a la frontera — no re-escanea
  todo el set ni re-emite `decision = 'pending'` sobre aristas ya revisadas. DDL de
  la tabla en el cuerpo del PR; el mantenedor la aplica primero.
- **Cursor inicial (sin frontera):** `('1970-01-01T00:00:00Z', '00000000-0000-0000-0000-000000000000')`
- **Batch size default:** 500, configurable
- **Avance del cursor:** después de cada página, la frontera avanza al último
  `(created_at, id)` leído y se persiste (`write_cursor`, best-effort).
- **Compañeros históricos (completitud nuevo-vs-viejo):** la frontera sola solo
  compararía un aporte nuevo contra otros nuevos de su propia página. Para las
  aristas nuevo-vs-viejo, cada página trae además los compañeros de bloque
  históricos (`fetch_partners_by_block_keys`, por `block_keys` e **ignorando** la
  frontera), los une a la página y bloquea+puntúa sobre la unión. La idempotencia
  de escritura (`find_existing_candidates`) absorbe los pares viejo-vs-viejo que
  reaparezcan. Ver el hueco residual (nuevo-vs-nuevo cross-página) en la nota de §3.
- **Degradación graciosa:** si `consolidation_state` falta (404/406) o rechaza el
  acceso (401/403), `read_cursor` devuelve la frontera inicial (scan completo) y el
  job sigue; nunca aborta ni repite el `400` de `consolidated_at`.

---

## 3. Blocking

### 3.1 Block keys

Para cada persona, se generan hasta dos block keys:

| Tipo | Key | Condición |
|------|-----|-----------|
| **Fuerte** (cedular) | `ced:{event_id}:{cedula_hmac}` | Solo si `cedula_hmac` no está vacío |
| **Laxa** (fonética) | `phon:{event_id}:{phonetic_hash(full_name)}` | Siempre, si `phonetic_hash` existe |

> **Corrección de Mayerlim #4:** El blocking con solo `deterministic_id` era demasiado restrictivo — personas con la misma fonética pero distinto detalle de ubicación ("Caracas" vs "Caracas, Miranda") caían en bloques distintos y nunca se comparaban. La clave laxa por `phonetic_hash(full_name)` sin ubicación resuelve esto. `similarity_score` ya pondera ubicación con 0.15 y decide si el par pasa el umbral.

### 3.2 Agrupación

- Personas con block key fuerte: se agrupan con otras que comparten esa misma key
- Personas con block key laxa: mismo mecanismo
- Las block keys se leen de `aportes.block_keys` (ya populado por el staging exporter)
- Si una fila histórica o un mock no trae `block_keys`, el job las reconstruye
  desde `event_id`, `cedula_hmac` y `phonetic_hash` para compatibilidad. En
  producción, `aportes.block_keys` es la fuente de verdad.
- Una persona puede pertenecer a múltiples bloques (uno por cada block key)

> **Hueco de completitud residual (follow-up):** el fetch de compañeros históricos
> (§2) cierra el caso nuevo-vs-viejo (un aporte nuevo se compara contra **todos**
> los miembros de su bloque, incluidos los ya procesados). Queda un hueco menor:
> dos aportes **ambos nuevos** del mismo bloque que caen en páginas distintas
> **de la misma corrida** no se comparan entre sí en esa corrida (cada uno solo ve
> compañeros históricos, no al otro nuevo de la otra página). La próxima corrida los
> compara si uno queda como histórico del otro. Cerrar esto del todo exige blocking
> sobre el set completo o acumulación en memoria. No bloquea la meta de producir
> candidatos. (Refinamiento adicional: hoy la unión re-puntúa también pares
> viejo-vs-viejo —idempotentes vía `find_existing_candidates`—; acotar el scoring a
> pares que toquen ≥1 aporte nuevo es una optimización de costo, no de correctitud.)

---

## 4. Similarity scoring

### 4.1 Pesos

| Campo | Peso | Lógica |
|-------|------|--------|
| `full_name` | 0.40 | Jaro-Winkler con `jellyfish` sobre nombres normalizados |
| `cedula_hmac` | 0.30 | Match binario: 1.0 si iguales, 0.0 si ausente en uno o ambos, **veto** si ambas existen y son distintas |
| `last_known_location` | 0.15 | Parcial: misma ciudad = 1.0, mismo estado = 0.5, distinto = 0.0 |
| `age_range` | 0.10 | Solape de rangos: normalizado a [0,1] según proporción de solape |
| `status` | 0.05 | Match binario: 1.0 si igual, 0.0 si distinto |

### 4.2 Veto por cédula distinta

> **Corrección de Mayerlim #2:** Implementado como score total = 0 si ambas personas tienen cédula y son distintas. No es neutral.

```python
if left_cedula and right_cedula and left_cedula != right_cedula:
    return 0.0  # score total = 0 → nunca candidato
```

### 4.3 Jaro-Winkler

Se usa `jellyfish.jaro_winkler_similarity()`. Ya está en `scrapers/requirements.txt`. Reemplaza la implementación pura Python del PR #79.

### 4.4 Ubicación parcial

> **Corrección de Mayerlim #1:** Reemplaza el match binario del PR #79 (1.0 o 0.0). Scoring parcial:

```python
def location_score(left_loc, right_loc):
    if left_city == right_city and left_state == right_state:
        return 1.0
    if left_state == right_state:
        return 0.5
    return 0.0
```

---

## 5. Umbral y candidatos

- **Umbral default:** 0.85 (configurable vía `--threshold`)
- Pares con `score >= threshold` → fila en `dedup_candidates` con:

| Columna | Valor |
|---------|-------|
| `left_aporte_id` | FK a `aportes.id` del aporte izquierdo (orden canónico, ver §6). Como `persons.person_record_id` es 1:1 con `aportes.id`, es el mismo identificador |
| `right_aporte_id` | FK a `aportes.id` del aporte derecho |
| `blocking_key` | Clave que produjo el candidato; lleva el `event_id` embebido (ej. `ced:{event_id}:{cedula_hmac}`), así que el contexto de evento no se pierde |
| `score` | Score numérico (0.0 a 1.0) |
| `reasons` | JSONB con desglose por campo: `{"nombre": 0.35, "cedula": 0.30, "ubicacion": 0.15, "edad": 0.10, "status": 0.05}` |
| `priority` | Entero (mayor = más urgente). Convención propuesta: `2` si score >= 0.95, `1` si 0.85 <= score < 0.95. Confirmar los valores enteros con el backend antes de escribir |
| `touches_gold` | Booleano NOT NULL: `true` si alguno de los dos aportes ya pertenece a una entidad gold; en la generación inicial de candidatos es `false` |
| `decision` | `dedup_decision`, default `'pending'`. El job solo escribe `pending` |
| `resolved_by` / `second_reviewer` / `resolved_at` | Nulos al insertar; los puebla la revisión humana, no este job |
| `created_at` | NOW() |

---

## 6. Idempotencia

- El índice único de par canónico usa:
  `LEAST(left_aporte_id, right_aporte_id)`,
  `GREATEST(left_aporte_id, right_aporte_id)`, `blocking_key`.
- PostgREST no puede apuntar ese índice expresivo con `on_conflict` portable.
  El job usa lookup batch `select-before-insert/update` por par canónico +
  `blocking_key`.
- Si el candidato ya existe → `UPDATE score, reasons, priority, decision`.
- Si es nuevo → `INSERT` en bulk cuando PostgREST lo soporta.
- Errores fatales (auth 401/403, schema mismatch estructural, respuesta global
  no parseable) abortan el job. Errores no fatales de candidato se acumulan,
  bloquean solo el marcado de los aportes afectados y permiten continuar.

---

## 7. Nunca auto-merge

- **Cero filas nuevas en `persons`** — ningún INSERT/UPDATE a la tabla canónica
- Solo se lee `aportes` y se escribe `dedup_candidates`
- La revisión humana decide qué pares fusionar (fuera del alcance de este job)

---

## 8. Reutilización de PR #79

| Módulo | Acción |
|--------|--------|
| `scrapers/dedup/similarity.py` | Adaptar: usar `jellyfish`, implementar veto de cédula, ubicación parcial |
| `scrapers/dedup/blocking.py` | Adaptar: leer de `aportes.block_keys` con fallback reconstruido, implementar blocking laxo por phonetic_hash |
| `scrapers/dedup/clustering.py` | Adaptar: leer de `aportes` en vez de lista en memoria |
| `scrapers/dedup/deduplicator.py` | Subir imports al top del archivo (corrección Mayerlim #3) |
| `scrapers/jobs/consolidation_job.py` | NUEVO: orquestador standalone para Person dedup |

---

## 9. Ejecución

```bash
python -m scrapers.jobs.consolidation_job --entity-type person --batch-size 500 --threshold 0.85
```

- Corre cada 20 min vía GitHub Actions (issue F, evilla-dev)
- Schema de columnas en `aportes` y `dedup_candidates` asegurado por #90 y #81

---

## 10. Testing

- 100% offline con mock del cliente Supabase (`httpx.MockTransport`)
- Fixtures sintéticos simulando `aportes` con block_keys pobladas
- Casos a cubrir:
  - Par con score >= 0.85 → candidato creado
  - Par con cédulas distintas → score = 0, sin candidato
  - Par con misma fonética, distinta ubicación → comparados (por bloque laxo)
  - Par ya existente → UPDATE, no INSERT duplicado
  - Person sin `cedula_hmac` → solo bloque laxo
  - Cédula ausente en uno o ambos registros → no suma 0.30
  - Person sin `phonetic_hash` → sin bloque laxo, posiblemente sin candidatos
  - Actualización de arista ya existente → NO re-escribe `decision` (la revisión
    humana `confirmed`/`rejected` + `resolved_by`/`resolved_at` sobrevive)
  - Aporte nuevo (posterior a la frontera) que duplica a uno viejo (anterior,
    otra página) → sigue generando arista vía compañeros históricos
  - `consolidation_state` ausente/sin permiso → degrada a scan completo, sin abortar
  - Fetch de compañeros históricos falla → página degrada a solo-página, no fatal
  - Cursor con mismo `created_at` e `id` mayor → no se salta registros
  - Lookup batch de candidatos existentes → no un GET por candidato
  - Aporte sin `event_id` (no puede formar block key) o candidato sin `blocking_key` → error controlado, no tumba todo
  - Fallback sin `block_keys` → genera `ced:*` y `phon:*` esperados
  - Sin `block_keys` y sin `event_id` → no genera claves inválidas
  - Batch con 0 registros → el job termina sin error
  - Job interrumpido a mitad → registros no procesados re-encontrados en próxima corrida

---

## 11. Summary de salida (métricas)

```json
{
  "entity_type": "person",
  "run_id": "uuid",
  "batches": 3,
  "records_read": 1200,
  "blocks": 85,
  "pairs_compared": 410,
  "candidates_inserted_or_updated": 15,
  "duplicates_skipped": 2,
  "upsert_errors": 0,
  "mark_errors": 0,
  "execution_time_ms": 4200
}
```

---

## 12. Dependencias

| Dependencia | Estado |
|-------------|--------|
| Esquema `dedup_candidates` | forma viva (`*_aporte_id`, `priority` entero, `touches_gold`) + índice único de par; ver `docs/schema.md` |
| #81 (staging exporter) | `aportes` poblada con block_keys |
| PR #79 | Reutilizar lógica + aplicar 4 correcciones |
| `jellyfish` | Ya en `requirements.txt` |

---

## 13. Lo que NO hace este job

- No inserta en `persons`
- No modifica `events` ni `acopio_centers`
- No hace merge automático de ningún tipo

---

## 14. Correcciones de Mayerlim aplicadas

| # | Corrección | Dónde se aplica |
|---|-----------|----------------|
| 1 | Ubicación con scoring parcial (no binario) | Sección 4.4 — `location_score()` |
| 2 | Cédula distinta = veto (score = 0) | Sección 4.2 — veto explícito |
| 3 | Imports al top del archivo | Sección 8 — `deduplicator.py` |
| 4 | Blocking laxo por phonetic_hash sin ubicación | Sección 3.1 — clave laxa |
