---
name: resolve-issue
description: Resuelve un issue de GitHub de punta a punta en VZLA_DEDUP - crea rama desde master, implementa, prueba, autorevisa, abre el PR siguiendo CONTRIBUTING.MD, y vigila los checks de CI hasta dejarlos en verde. Usar cuando el usuario pide "resuelve el issue #N", "haz el PR del issue X" o similar.
---

# Resolve Issue

Resuelve un issue de GitHub completo: rama → implementación → tests →
code review → PR → CI verde. Sigue siempre las reglas de
`CONTRIBUTING.MD` (este repo maneja datos de personas desaparecidas; la
seguridad y la trazabilidad no son negociables).

Argumento esperado: número o URL del issue (`/resolve-issue 103`).

## 0. Antes de empezar

- Verifica que `gh` esté autenticado **y** que el token tenga permiso de
  escritura sobre PRs antes de avanzar — `gh auth status` solo confirma
  que hay sesión, no que el token pueda crear PRs:
  ```bash
  gh auth status
  gh api repos/{owner}/{repo} --jq .permissions.push
  ```
  Si `gh auth status` falla, pide al usuario que corra `gh auth login`.
  Si `permissions.push` es `false`, o si más adelante `gh pr create`
  falla con "Resource not accessible by personal access token", detente
  de inmediato y pide al usuario que amplíe los scopes del token
  (`gh auth refresh -h github.com -s repo`) — no sigas intentando otras
  cosas para esquivarlo.
- Si no se pasó número de issue, pídelo.
- Lee el issue completo:
  ```bash
  gh issue view <n> --json title,body,labels,url,state
  ```
  Si `state` no es `OPEN`, avisa al usuario y confirma si quiere
  continuar igual.
- Si el issue no tiene criterios de aceptación claros, o es ambiguo en
  alcance, pregunta al usuario antes de escribir código. No asumas.
- Identifica el área que toca (`scrapers`, `db-api`, `verification`,
  `docs`) a partir de las labels y del contenido — la necesitas para el
  nombre de la rama. Si las labels no son concluyentes, confírmalo
  mirando qué directorios tendrás que modificar.

### Reanudar trabajo existente (idempotencia)

El nombre de rama siempre incluye el número del issue (ver paso 1), así
que el chequeo de trabajo previo se hace por número, sin depender de
ningún slug que todavía no existe en este punto. Antes de crear nada:

```bash
git fetch origin --prune
git branch --list "*<n>-*"
git branch -r --list "origin/*<n>-*"
gh pr list --search "<n> in:body" --state all
```

(El `git fetch --prune` es obligatorio antes de mirar ramas remotas —
sin él, `git branch -r` puede mostrar refs obsoletos y no detectar una
rama que otra persona ya creó.)

- Si la rama ya existe localmente o en remoto: haz `git checkout` sobre
  ella en vez de crear una nueva, y sigue desde el paso que corresponda
  según lo que ya esté hecho (¿ya hay commits? ¿ya hay PR abierto?).
- Si ya existe un PR abierto: continúa desde el paso 7 (vigilar checks)
  en vez de recrearlo. La búsqueda por `<n> in:body` es texto libre y
  puede traer falsos positivos (el número aparece en el body por otra
  razón) o falsos negativos (el PR cierra el issue de otra forma) —
  confirma siempre con el usuario antes de asumir que es "el mismo PR".
- No crees una segunda rama o un segundo PR para el mismo issue salvo
  que el usuario lo pida explícitamente.

## 1. Rama nueva desde master actualizado

```bash
git checkout master
git pull origin master
```

Si hay cambios locales sin commitear que no son tuyos, detente y avisa al
usuario — no los descartes.

Crea la rama con el prefijo correcto (ver tabla) y el formato
`<prefijo>/<n>-<slug-descriptivo>` — el número del issue siempre va
primero en el slug para que el chequeo de idempotencia del paso 0 lo
pueda encontrar sin ambigüedad:

```bash
git checkout -b <prefijo>/<n>-<slug-descriptivo>
```

Si `git checkout -b` falla porque la rama ya existe, no la borres ni
hagas `-D` — es probable que sea trabajo de una corrida anterior (ver
"Reanudar trabajo existente"). Haz `git checkout <rama>` y continúa.

| Área tocada | Prefijo |
|---|---|
| scrapers (recolección, parsing, normalización, exportación) | `scrapers/` |
| db-api (modelos, endpoints, cifrado, ingestión) | `db-api/` |
| verification (validación de registros/fuentes/claims) | `verification/` |
| docs (documentación, guías, contratos) | `docs/` |
| arreglo de bug sin encajar en lo anterior | `fix/` |

## 2. Implementar

- Resuelve **una sola cosa**: el alcance del issue. No mezcles refactors
  no relacionados ni cambios de área distinta en el mismo PR.
- Escribe el código necesario para cumplir cada criterio de aceptación
  del issue, uno por uno.
- Actualiza documentación (`README.md`, docs en `docs/`, docstrings de
  contrato) si el cambio toca contratos, schemas o comportamiento
  esperado de adapters/parsers/modelos.
- Reglas de seguridad que no se pueden romper (CONTRIBUTING.MD):
  - Nunca subas datos reales, dumps, CSVs, PDFs, JSONL ni imágenes con
    información real de personas.
  - Nunca loguees ni imprimas cédulas, teléfonos, direcciones o nombres
    completos sensibles.
  - Los tests usan solo datos ficticios.
  - Cualquier output local del pipeline va a `scrapers/runtime_output/`
    (ignorado por git), nunca al repo.
  - Si el issue toca datos de menores de edad, el manejo de esa
    protección debe quedar explícito en el código y en la descripción
    del PR.

## 3. Tests y lint

```bash
python -m pytest scrapers/tests
ruff check .
```

Ambos deben pasar limpio. Si rompiste un test existente, arréglalo —
nunca lo borres o lo marques `skip` para esquivarlo, salvo que el test
en sí esté mal y el usuario lo confirme.

Si después de **dos intentos de corrección** un test sigue fallando por
una razón que no entiendes (flaky, dependencia externa, infraestructura),
detente y pídele ayuda al usuario en vez de seguir iterando a ciegas o
de silenciarlo.

## 4. Code review propio

Antes de dar por terminada la implementación, revisa tu propio diff
como lo haría un revisor: usa el skill `/code-review` sobre los cambios
(o, si no está disponible, revisa manualmente `git diff master...HEAD`
buscando bugs de lógica, manejo de errores faltante en límites del
sistema, y fugas de PII).

Corrige todo lo que encuentres y vuelve a correr el paso 3
(`pytest` + `ruff`) hasta que quede limpio.

## 5. Commit

El historial del repo usa Conventional Commits
(`git log --oneline -10` para confirmar). Usa ese formato:

```
<tipo>(<scope opcional>): <resumen en imperativo>
```

Tipos vistos en el repo: `feat`, `fix`, `ci`, `docs`, `refactor`, `test`.
El scope suele ser el área tocada (`adapters`, `types`, `parsers`, etc.).
Ejemplos reales del historial: `fix(types): mypy --strict limpio en
scrapers/adapters y scrapers/parsers`, `feat(adapters): adapter
Playwright para fuentes webapp_js`.

Commits atómicos: si la implementación tuvo varias fases lógicas
(p.ej. modelo nuevo + adapter + tests), está bien usar varios commits en
vez de uno gigante, siempre dentro de la misma rama/PR.

No incluyas `Co-Authored-By` salvo que el usuario lo pida explícitamente
para este flujo — confírmalo si no está claro.

## 6. Crear el PR

```bash
git push -u origin <rama>
gh pr create --title "<título claro>" --body "$(cat <<'EOF'
## Qué cambié
...

## Por qué era necesario
Resuelve #<n>.

## Cómo se prueba
...

## Qué riesgo tiene
...

## Cómo protege PII
... (o "No aplica" si el cambio no toca datos personales)

## Ejemplo de salida (datos ficticios)
... (si aplica)

## Checklist
- [x] Corrí los tests.
- [x] No incluí datos reales.
- [x] No incluí dumps, CSVs, PDFs, imágenes reales ni JSONL con información sensible.
- [x] No logueo cédulas, teléfonos, direcciones, nombres completos sensibles ni secretos.
- [x] El cambio mantiene trazabilidad hacia la fuente original.
- [x] Actualicé documentación si cambié contratos, schemas o comportamiento esperado.
- [x] El PR resuelve una sola cosa.
EOF
)"
```

`gh pr create` imprime la URL del PR creado (termina en `/pull/<n>`).
Toma el número de ahí para el paso siguiente — no lo inventes ni asumas
que es el mismo que el del issue.

El PR debe resolver exactamente lo que pide el issue, ni más ni menos.

## 7. Vigilar los checks de CI

```bash
gh pr checks <pr-number> --watch
```

(`<pr-number>` es el que obtuviste de la URL devuelta por
`gh pr create` en el paso anterior, o el de `gh pr list --search "<n> in:body"`
si estás reanudando un PR existente.)

El workflow `ci.yml` corre: tests (pytest), lint (ruff), gitleaks,
bloqueo de archivos de datos reales, pip-audit, bandit, scan de
palabras clave de PII/secretos, y `dependency-review`.

Si algún check falla:

1. Revisa el log del job que falló:
   ```bash
   gh run view <run-id> --log-failed
   ```
2. Diagnostica la causa raíz (no la enmascares con `continue-on-error`
   ni excluyendo el check).
3. Corrige en local, vuelve a correr `pytest`/`ruff` (paso 3).
4. Commit nuevo (no hagas `--amend` ni force-push) y push.
5. Repite hasta que todos los checks estén en verde.

**Límite: máximo 3 intentos de corrección por check.** Si tras 3 ciclos
de corrección el mismo check sigue fallando, detente y reporta al
usuario el log relevante y tu diagnóstico — no sigas iterando
indefinidamente.

Si un check falla por algo fuera de tu control (p.ej. una
vulnerabilidad en una dependencia de terceros sin fix disponible),
detente y explícaselo al usuario en vez de intentar esquivarlo.

## 8. Cierre

No mergees el PR — este repo exige al menos una aprobación humana.
Reporta al usuario: URL del PR, estado de los checks, y cualquier
decisión de alcance que hayas tomado al implementar.
