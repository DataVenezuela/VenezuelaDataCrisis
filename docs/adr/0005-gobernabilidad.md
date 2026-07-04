# ADR 0005 — Gobernabilidad del repositorio y quórum de mantenedores

| Campo | Valor |
|---|---|
| Estado | Propuesta |
| Fecha | 2026-07-04 |
| Decisores | Admins (mathiasaiva, mayerlim, @9v52602d) |
| Reemplaza a | (ninguno) |
| Complementa | ADR 0003 (reestructuración de repos), ADR 0004 (versionado de contrato) |
| Relacionado con | `CODEOWNERS`, `CONTRIBUTING.md`, `docs/base-standards.md` |

---

## 1. Contexto

El proyecto es mayormente de una persona con dos colaboradores más, sobre un repo
público de contribuciones abiertas que contiene código sensible: modelos y
parsers que definen la forma de los datos, workflows de CI con permisos, y las
specs de contrato que el repo privado `vzla-deployment` importa (ADR 0003, ADR
0004). Un cambio malicioso o descuidado en esas rutas puede filtrar PII, romper
el contrato o comprometer el pipeline.

ADR 0003 §8 y su plan de implementación difieren aquí la gobernanza del repo de
deployment ("quórum de mantenedores, ver ADR 0005"). ADR 0004 nombra a
"CODEOWNERS, ADR 0005" como el revisor que verifica que un cambio de contrato
suba la versión. Falta la ADR que fije esa política de forma explícita: quién
puede escribir, quién debe aprobar y sobre qué rutas.

---

## 2. Decisión

Se adopta una gobernanza selectiva y por quórum, barata de operar para un equipo
chico.

- **CODEOWNERS selectivo (ya en el repo).** Las rutas sensibles tienen dueños
  múltiples y el resto un dueño por defecto:
  - `/scrapers/`, `/.github/workflows/`: los tres admins.
  - `/docs/`, `/README.md`, `/CONTRIBUTING.md`, `/AGENTS.md`: mathiasaiva y
    mayerlim (las specs de contrato viven bajo `/docs/`).
  - `/.claude/`, `/verification/`: mathiasaiva.
  - `*` (por defecto): mathiasaiva.
- **Quórum de 2 de 3 admins** para cambios sensibles (modelos y parsers,
  contratos, workflows de CI). El conjunto del quórum son los tres admins
  (mathiasaiva, mayerlim, @9v52602d): "2 de 3", no un solo aprobador ni un solo
  check. Para el resto de rutas basta la revisión normal de CONTRIBUTING.md.
- **Allowlist de colaboradores.** El acceso de escritura al repo es solo por
  invitación (los admins y colaboradores ya listados). Cualquier otra persona
  contribuye por fork y PR: sin acceso directo a ramas.
- **Repo privado `vzla-deployment`.** La misma regla de quórum (2 de 3 admins)
  gobierna los cambios de infraestructura sensible del repo privado (credenciales,
  DDL, fuentes y parsers de producción). ADR 0003 difiere esta regla aquí; se
  enuncia en esta ADR pública aunque el repo privado aún no exista.

---

## 3. Consecuencias

**Positivas**

- Ningún cambio sensible (PII, contrato, CI) entra por un solo aprobador.
- CODEOWNERS enruta la revisión al dueño correcto de forma automática, sin pedir
  reviewer a mano.
- El acoplamiento con `vzla-deployment` (ADR 0003, ADR 0004) hereda una regla de
  aprobación clara.

**Negativas / costos asumidos**

- Un cambio sensible necesita dos admins disponibles: con el equipo chico eso
  puede frenar un merge urgente.
- La allowlist cierra la contribución directa: todo externo pasa por fork.

**Riesgos y mitigaciones**

- *Quórum que bloquea un hotfix*: los tres admins tienen el rol, así que dos
  cualesquiera desbloquean; no depende de una sola persona.
- *Política sin forzar por tooling*: el quórum y la allowlist son política hasta
  configurar branch protection (ver §4); mientras tanto dependen de la disciplina
  de revisión y de CODEOWNERS.

---

## 4. Estado / implementación

Parcial. Hoy en el repo:

- **Sí existe** `CODEOWNERS` selectivo, con dueños múltiples en `/scrapers/`,
  `/.github/workflows/`, `/docs/` y las rutas sensibles descritas arriba.
- **No está forzado** el quórum de 2 de 3: `CONTRIBUTING.md` documenta "al menos
  una aprobación y rama al día con master", pero `master` no tiene branch
  protection configurado hoy (la API la reporta como no protegida). Con una sola
  aprobación requerida, CODEOWNERS sugiere reviewers pero no obliga a dos.
- La **allowlist de colaboradores** es el estado actual de acceso del repo, pero
  como política escrita vive recién en esta ADR.
- El **quórum del repo privado** es aspiracional: `vzla-deployment` todavía no
  existe (ADR 0003 §12).

Primer paso de implementación: configurar branch protection en `master`
(aprobaciones requeridas alineadas al quórum, revisión de CODEOWNERS obligatoria,
rama al día) para que la política deje de depender solo de la disciplina.

---

## 5. Enlaces

- ADR 0003 (reestructuración de repos), §8 y plan de implementación (difiere el
  quórum de deployment aquí).
- ADR 0004 (versionado de contrato): nombra CODEOWNERS + ADR 0005 como revisor
  del bump de versión.
- `CODEOWNERS` (reglas de propiedad actuales).
- `CONTRIBUTING.md` (proceso de revisión y branch protection).
- Issue #237.
