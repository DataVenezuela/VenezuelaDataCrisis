# ADR 0010 — Fusión uniforme por aristas para todos los entity_type

| Campo | Valor |
|---|---|
| Estado | Aceptada |
| Fecha | 2026-07-27 |
| Decisores | DB/API, Scrapers/Cleaners, Verification |
| Reemplaza a | [ADR 0007](./0007-modelo-consolidacion-gold.md) §2.1 (`events` como catálogo compartido) y §3 (compuerta de publicación, ahora uniforme para los tres tipos) |
| Complementa | [ADR 0007](./0007-modelo-consolidacion-gold.md) (mantiene el resto del modelo medallón y la regla de oro §8) |
| Relacionado con | `docs/pipeline.md`, `docs/schema.md`, `docs/specs/person-dedup.md`, [ADR 0009](./0009-fuentes-en-db-clave-source-id.md), #315, #316, #319 |

---

## 1. Contexto

ADR 0007 fijó una capa **gold** como única sede de la fusión y una regla de oro
(§8): "Silver nunca colapsa. La fusión vive solo en gold." Hoy esa regla solo se
cumple para `person`, que ya emite **aristas puntuadas** a `dedup_candidates` y se
funde por componentes conexos en gold.

Event y AcopioCenter siguen otro camino que contradice esa regla:

- El writer canónico (`upsert_canonical`, `on_conflict=dedup_hash` con
  merge-duplicates, ver ADR 0009) **auto-funde en silver** los aportes que comparten
  `dedup_hash`. Eso colapsa silver y viola §8.
- ADR 0007 §2.1 modela `events` como **catálogo compartido** (PK propia, sin FK 1:1
  por aporte). Es asimétrico con la proyección 1:1 de `person`/`acopio` y pierde la
  trazabilidad por aporte que sí tienen los otros tipos.

El resultado es un pipeline con **dos modelos de fusión** distintos y una regla de
oro que solo se respeta a medias. Esta ADR corrige el rumbo hacia 0007, no lo
abandona.

---

## 2. Decisión

Se adopta **fusión por aristas para todos los `entity_type`** (Person, Event,
AcopioCenter). Silver queda 1:1 sin excepción y la fusión vive solo en gold.

1. **Silver 1:1 para todos, incluido `events`.** El materializer proyecta cada
   aporte a su tabla tipada 1:1. `events` deja de ser catálogo compartido: recibe
   una fila por aporte con PK compartida y FK a `aportes.id`, igual que
   `persons`/`acopio_centers`. Esto **reemplaza** el modelo de catálogo de 0007
   §2.1.
2. **La fusión de Event/Acopio se emite como aristas**, no como merge en el sitio.
   La coincidencia que hoy colapsa silver (`dedup_hash` exacto) se convierte en una
   **arista fuerte** en `dedup_candidates`, exactamente como `person` ya hace con
   `ced:`. Se **retira** el auto-merge en silver (`on_conflict=dedup_hash` con
   merge-duplicates).
3. **Gold clustering agrupa por relación** (componentes conexos sobre el grafo de
   aristas) para los tres tipos por igual, y materializa clusters en `gold_entities`
   + `gold_members` + `gold_history`, tal como 0007 §2.3.

El build job sigue publicando solo gold (`gold_entities` publicado + aportes
huérfanos), sin cambios respecto de 0007.

---

## 3. Modelo / contrato

La compuerta de publicación de 0007 §3 (**fuerza de la arista**) se aplica ahora de
forma **uniforme** a los tres tipos:

- **Aristas fuertes (fusión automática en gold):** identidad determinista.
  - Person: `ced:…` (cédula).
  - Event / AcopioCenter: coincidencia exacta de `dedup_hash`. El mismo `dedup_hash`
    que hoy colapsa silver pasa a ser señal de arista fuerte hacia
    `dedup_candidates`.
- **Aristas difusas (no fusionan solas):** fonética o nombre (`phon:…`) para
  cualquier tipo. Quedan `pending` en `dedup_candidates` (cola de revisión humana) y
  sus aportes se publican **sin fusionar** hasta que un humano acepta el candidato.
- **Sin cambios respecto de 0007 §3:** `verification_status` vive en `gold_entities`
  y es ortogonal a la confianza de merge; la estabilidad de `gold_id` (acuñado una
  vez, reutilizado mientras el cluster recomputado solape miembros actuales); los
  huérfanos (aporte sin arista fuerte) no reciben fila en gold y el build los une.

---

## 4. Seguridad

- Silver 1:1 total y bronze inmutable: ninguna fusión destruye evidencia. Hacer
  `events` 1:1 **añade** trazabilidad (qué aporte creó qué evento) que el catálogo
  compartido perdía.
- La protección de menores (ADR 0006) y la no reintroducción de PII al fusionar el
  cluster (0007 §4) se mantienen sin cambios.

---

## 5. Consecuencias

**Positivas**

- Un solo mecanismo de fusión (aristas hacia gold) para los tres tipos: más simple
  de razonar y de auditar.
- La regla de oro (§8) se cumple de verdad, sin excepción para Event/Acopio.
- Trazabilidad por aporte también para `events`.

**Negativas / costos asumidos**

- `events` pasa de una fila por evento lógico a una fila por aporte en silver (más
  filas).
- La deduplicación de Event/Acopio deja de ser inmediata en silver: depende de que
  el gold clustering corra. Hasta entonces, la vista pública puede mostrar
  duplicados de esos tipos (el mismo costo tolerable que 0007 §5 ya acepta para
  `person`).

**Riesgos y mitigaciones**

- *Transición con doble modelo*: si el retiro del auto-merge (#319) y el clustering
  (#320/#321) no se despliegan coordinados, coexistirían dos comportamientos.
  Mitigación: el orden de dependencias del epic #315.
- *Reproyección de `events` existentes*: al pasar a 1:1 hay que rehidratar filas ya
  colapsadas. Mitigación: se hace desde `aportes` (bronze/silver inmutables), que
  conservan cada aporte original.

---

## 6. Alternativas consideradas

- **Mantener `events` como catálogo compartido con auto-merge en silver (status quo
  0007 §2.1).** Rechazada: viola §8, es asimétrica con los otros tipos, pierde
  trazabilidad por aporte y hace la fusión irreversible en silver.
- **Retirar el auto-merge solo para uno de los tipos.** Rechazada: dejaría dos
  modelos de fusión conviviendo y la regla de oro seguiría rota a medias.

---

## 7. Plan de implementación

- **#317:** tablas `gold_*` y grants para el rol `consolidation_job`.
- **#318:** production `GoldDataPort` adapter (Supabase PostgREST).
- **#319:** Event/Acopio emiten aristas fuertes (`dedup_hash` exacto) y se retira el
  auto-merge en silver (`on_conflict=dedup_hash` con merge-duplicates en
  `upsert_canonical`); ajustar los índices que 0009 exige para ese `on_conflict`.
- **#320:** clustering orchestrator (componentes conexos hacia `GoldWriter`) para
  los tres tipos.
- **#321:** cablear el clustering al `consolidate` CLI/cron.

---

## 8. Regla de oro

Se reafirma 0007 §8, ahora sin excepción: **silver nunca colapsa para ningún tipo**.
La fusión (también la de Event y AcopioCenter) vive solo en gold, vía aristas. Solo
las señales fuertes fusionan solas; duplicar es tolerable, fusionar mal puede ser
peligroso.
