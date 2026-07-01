---
name: pr-checklist
description: Revisa un Pull Request contra las reglas del repo: Person.status en inglés, cedula_hmac sin prefijo, trust_tier en letras, garantía del watermark en staging_exporter, y docs/source_config.md sincronizado con SourceConfig. Usar cuando el usuario pide "revisa mi PR", "checklist del PR", o antes de abrir un PR.
---

# PR Checklist

Revisa que un Pull Request cumpla las reglas de `CONTRIBUTING.md`.
Esto debe correrse **antes** de abrir el PR o durante el code review.

> **Nota:** los comandos `rg` son señales a verificar manualmente, no
> verdad absoluta. Pueden generar falsos positivos en comentarios,
> docstrings o strings que mencionen estos términos sin ser
> asignaciones reales. Si `rg` marca un archivo, revisá la línea
> específica antes de dar `[FAIL]`.

Uso: ejecutá esto en la rama del PR (con los cambios sin commitear o ya
commiteados). No modifica archivos — solo inspecciona y reporta.

## Reglas que verifica

### 1. `Person.status` en inglés

El enum `Person.status` debe usar uno de:
- `missing`
- `found`
- `injured`
- `deceased`
- `unknown`

Busca valores en español (`desaparecido`, `localizado`, `herido`,
`fallecido`, `hallado`) en cualquier archivo que defina o asigne
`status`. La definición del enum está típicamente en
`scrapers/models/person.py` o similar.

```bash
# Detectar valores en español en modelos
rg -n '"(desaparecido|localizado|herido|fallecido|hallado)"' scrapers/models/ scrapers/parsers/

# Detectar literales en español en parsers
rg -n 'status\s*=\s*"(desaparecido|localizado|herido|fallecido|hallado)"' scrapers/parsers/
```

### 2. `cedula_hmac` sin prefijo `hmac_sha256:`

`cedula_hmac` debe ser 64 caracteres hexadecimales puros, sin prefijo.
Si ves `hmac_sha256:` al inicio del valor, está mal.

```bash
# Detectar prefijo incorrecto en asignaciones
rg -n '"hmac_sha256:' scrapers/parsers/ scrapers/models/ scrapers/pipelines/

# Detectar concatenación de prefijo
rg -n 'hmac_sha256.*cedula\|cedula.*hmac_sha256' scrapers/parsers/ scrapers/pipelines/

# Verificar que identity_token no incluya prefijo
rg -n 'identity_token' shared/hashing.py scrapers/parsers/
```

### 3. `trust_tier` en letras `A/B/C/D`, nunca enteros

`trust_tier` debe ser una letra (`"A"`, `"B"`, `"C"`, `"D"`), no un
entero (`1`, `2`, `3`, `4`).

```bash
# Detectar trust_tier como entero
rg -n 'trust_tier\s*=\s*[0-9]' scrapers/parsers/ scrapers/models/
rg -n 'trust_tier.*[0-9]' scrapers/parsers/ scrapers/pipelines/

# Detectar trust_tier como entero en tests
rg -n 'trust_tier\s*=\s*[0-9]' scrapers/tests/
```

### 4. Garantía del watermark en `staging_exporter.py`

Si el PR toca `scrapers/exporters/staging_exporter.py`, verificar que
`export_source()` solo avanza el watermark cuando **TODOS** los POST de
la fuente terminaron en 200/201. Buscar cualquier cambio que llame a
`set_watermark` o actualice `watermark_at` fuera de ese chequeo.

```bash
# Verificar si el PR toca el exporter
git diff --name-only origin/master... 2>/dev/null | rg -q 'staging_exporter' && echo "PR TOCA staging_exporter" || echo "No aplica"

# Revisar lógica de watermark
rg -n 'watermark' scrapers/exporters/staging_exporter.py
```

Marcar `[WARN]` si el PR modifica la lógica de avance del watermark sin
mantener visible la condición "todos los POST exitosos".

### 5. `docs/source_config.md` actualizado junto con `SourceConfig`

Si el PR agrega o modifica un campo en `scrapers/models/source.py` (la
clase `SourceConfig`), verificar que `docs/source_config.md` se modificó
en el mismo diff.

```bash
# Chequeo: si toca SourceConfig, debe tocar el doc
git diff --name-only origin/master... 2>/dev/null | rg -q 'scrapers/models/source.py' && \
  git diff --name-only origin/master... 2>/dev/null | rg -q 'docs/source_config.md' || \
  echo "[FAIL] SourceConfig cambió pero docs/source_config.md no"
```

Marcar `[FAIL]` si `source.py` está en el diff y `source_config.md` no.

## Reporte

La salida tiene este formato:

```
## PR Checklist Results

### Person.status
- [PASS] No se encontraron valores en español
- [FAIL] scrapers/parsers/mi_parser.py:42 — status = "desaparecido"

### cedula_hmac
- [PASS] Sin prefijo hmac_sha256: en asignaciones

### trust_tier
- [WARN] scrapers/parsers/mi_parser.py:18 — trust_tier = 3 (debe ser "C")

### Watermark (staging_exporter)
- [PASS] No toca staging_exporter, no aplica
  o
- [WARN] Lógica de watermark modificada sin condición visible

### docs/source_config.md
- [PASS] SourceConfig no cambió, no aplica
  o
- [FAIL] SourceConfig cambió pero docs/source_config.md no
```

Si todo pasa: `[PASS] Todas las reglas cumplidas.`

Si algo falla: indica archivo, línea y el cambio necesario.
