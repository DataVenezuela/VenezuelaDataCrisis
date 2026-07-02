# Spec: Person dedup → candidatos (#92)

> **Estado:** Aprobado
> **Issue:** #92 — feat(consolidation): Person dedup → candidatos (reutiliza PR #79)
> **Bloqueado por:** #90 (SQL migration), #81 (staging exporter), PR #79 (coordinar correcciones)
> **Fecha:** 2026-06-28

---

## 1. Objetivo

El consolidation job para Person lee de `aportes` en Supabase, agrupa registros por block keys, compara pares con scoring multi-campo, y produce candidatos de dedup en `dedup_candidates` con `decision = 'pending'`. Las personas **nunca se auto-fusionan** — la revisión humana decide.

---

## 2. Origen de datos

Lectura incremental con cursor blando desde `aportes`:

```sql
SELECT * FROM aportes
WHERE consolidated_at IS NULL AND entity_type = 'person'
  AND (created_at, id) > (:last_created_at, :last_id)
ORDER BY created_at ASC, id ASC
LIMIT :batch_size;
```

- **Cursor inicial:** `('1970-01-01T00:00:00Z', '00000000-0000-0000-0000-000000000000')`
- **Batch size default:** 500, configurable
- **Avance del cursor:** después de cada batch, los aportes sanos se marcan
  `consolidated_at = NOW()`. Si un candidato puntual falla, los aportes
  relacionados con ese candidato no se marcan; el resto del batch puede avanzar.
  El cursor se reconstruye desde el último id leído.
- **Resiliencia:** si el job se cae, los registros con `consolidated_at = NULL` se re-encuentran en la próxima corrida. El cursor es en memoria, no persiste — no hace falta

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
| `left_person_record_id` | FK a `persons.person_record_id` de la persona izquierda |
| `right_person_record_id` | FK a `persons.person_record_id` de la persona derecha |
| `blocking_key` | Clave que produjo el candidato |
| `score` | Score numérico (0.0 a 1.0) |
| `reasons` | JSONB con desglose por campo: `{"nombre": 0.35, "cedula": 0.30, "ubicacion": 0.15, "edad": 0.10, "status": 0.05}` |
| `priority` | `"high"` si score >= 0.95, `"medium"` si 0.85 <= score < 0.95 |
| `decision` | `"pending"` — nunca otro valor |
| `event_id` | FK al evento |
| `created_at` | NOW() |

---

## 6. Idempotencia

- `dedup_candidates_pair_blocking_uniq` en master usa:
  `LEAST(left_person_record_id, right_person_record_id)`,
  `GREATEST(left_person_record_id, right_person_record_id)`, `blocking_key`.
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
  - Error de escritura de candidato → no marcar `consolidated_at`
  - Error al marcar consolidado → CLI non-zero
  - Cursor con mismo `created_at` e `id` mayor → no se salta registros
  - Lookup batch de candidatos existentes → no un GET por candidato
  - Candidato sin `event_id` o `blocking_key` → error controlado, no tumba todo
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
| #90 (SQL migration) | `dedup_candidates` table + UNIQUE indexes |
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
