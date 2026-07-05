# ADR 0004 — Versionado del contrato DB/scrapers

| Campo | Valor |
|---|---|
| Estado | Propuesta |
| Fecha | 2026-07-04 |
| Decisores | Mantenedores (mathiasaiva, mayerlim), equipo de pipeline |
| Reemplaza a | (ninguno) |
| Complementa | ADR 0003 (reestructuración de repos) |
| Relacionado con | spec #231 (PR #232, `docs/specs/db-scraper-contract.md`), `docs/scrapper_contract.md`, `docs/specs/contracts/TEMPLATE.md` |

---

## 1. Contexto

El pipeline público `VZLA_DEDUP` expone un contrato entidad->DB hacia staging
(`aportes`) que el repo privado `vzla-deployment` importa y consume (ADR 0003).
Ese contrato ya está descrito como spec (`docs/specs/db-scraper-contract.md`,
PR #232), pero no tiene una versión explícita. ADR 0003 §8 dejó el versionado
como seguimiento de esta ADR.

Sin versión, un cambio en la forma del payload es invisible para el consumidor:
un `vzla-deployment` que fija una versión vieja no puede detectar que el pipeline
cambió, y una fila con forma incompatible se procesaría mal o se perdería sin
señal. Eso viola la regla de oro heredada ("nada se descarta en silencio"). El
equipo es pequeño y el deploy está cerca, así que la política debe ser barata de
operar.

---

## 2. Decisión

Se versiona el contrato entidad->DB de forma explícita.

- **`CONTRACT_VERSION`** identifica la forma del contrato, con **semver**:
  - **major**: cambio breaking (quitar o renombrar una columna obligatoria,
    cambiar la semántica de un campo, endurecer una precondición).
  - **minor**: cambio aditivo compatible (columna nueva opcional).
  - **patch**: aclaración de documentación sin cambio de forma.
- Cada fila exportada a `aportes` lleva su **`contract_version`**. Es distinto de
  `dedup_version` (que versiona el algoritmo de dedup, no la forma del contrato):
  ver §4.
- **Mismatch incompatible** (major que el consumidor no soporta) manda la fila a
  **cuarentena**, nunca a descarte silencioso.
- Las versiones estables se etiquetan en git como **`contract-v*`** (por ejemplo
  `contract-v1.0`); `vzla-deployment` fija ese tag como dependencia (ADR 0003).
- **Proceso de cambio**: toda spec de contrato vive en `docs/specs/contracts/`,
  sigue `docs/specs/contracts/TEMPLATE.md` y declara su `CONTRACT_VERSION` en el
  encabezado. Los cambios entran por PR contra esa spec; el PR sube la versión
  según la regla semver de arriba. Un cambio breaking exige un nuevo major y su
  tag antes de que `vzla-deployment` lo adopte.

Esta ADR define la **política y el proceso** de versionado, no un schema. El
schema concreto de cada contrato es su spec.

**Alcance.** La política aplica al contrato entidad->DB, que es el que tiene
filas y el que importa el repo privado. El contrato parser->entidad
(`docs/scrapper_contract.md`) es el contrato complementario aguas arriba: produce
entidades, no filas, así que no carga un `contract_version` por fila, pero su
forma se versiona bajo el mismo proceso (spec en `docs/specs/contracts/`, semver,
tag).

---

## 3. Consecuencias

**Positivas**

- El consumidor puede detectar un cambio de forma y decidir (adoptar, cuarentenar
  o rechazar) en vez de romperse en silencio.
- El acoplamiento público/privado queda pineable a un tag reproducible.
- Da un lugar único y un proceso claro para toda spec de contrato.

**Negativas / costos asumidos**

- Cada cambio de forma cuesta un bump de versión y, si es breaking, un tag y una
  coordinación con `vzla-deployment`.
- Añade una columna (`contract_version`) y lógica de comparación al exporter y al
  consumidor.

**Riesgos y mitigaciones**

- *Versión que no se sube al cambiar la forma*: el PR de contrato debe justificar
  el nivel de bump; la revisión (CODEOWNERS, ADR 0005 <!-- pendiente PR 238 -->)
  lo verifica.
- *Cuarentena que crece sin observarse*: la cuarentena debe ser visible (métrica
  o alerta), tema de una propuesta de telemetría futura (ver
  `PROPOSALS.md` <!-- pendiente PR 238 -->).

---

## 4. Estado / implementación

Aspiracional en su mayoría. Hoy en el código:

- **No existe** `CONTRACT_VERSION` ni la columna `contract_version` en `aportes`.
  La cuarentena por mismatch de versión tampoco está implementada.
- **Sí existe** `dedup_version` por fila (`spec.version`, por ejemplo
  `person-detid-v1`), pero versiona el algoritmo de la clave de dedup, no la
  forma del contrato: no confundir uno con otro.
- La spec del contrato entidad->DB (`docs/specs/db-scraper-contract.md`, PR #232)
  existe, pero aún sin versión formal ni carpeta `contracts/` poblada.

El primer paso de implementación es declarar `contract-v1.0` sobre la spec actual
y añadir la columna; recién entonces `vzla-deployment` puede fijar el tag.

---

## 5. Enlaces

- ADR 0003 (reestructuración de repos), §8 y §12.
- Issue #231, PR #232: `docs/specs/db-scraper-contract.md` (contrato entidad->DB).
- `docs/scrapper_contract.md` (contrato parser->entidad, complementario).
- `docs/specs/contracts/TEMPLATE.md` (plantilla de spec de contrato).
- `docs/adr/PROPOSALS.md` (telemetría de cuarentena, futura). <!-- pendiente PR 238 -->
