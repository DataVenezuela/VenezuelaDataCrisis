---
name: Bug report
about: Reportar un comportamiento incorrecto en el pipeline o la infraestructura
title: "[BUG] "
labels: bug
assignees: ''
---

## Qué pasó

<!-- Descripción clara y concisa del bug -->

## Qué esperabas que pasara

<!-- Comportamiento esperado -->

## Cómo reproducirlo

1. ...
2. ...
3. ...

## Área afectada

<!-- Marcá la que aplica -->

- [ ] `scrapers` (adapters, parsers, PII, normalización, staging exporter)
- [ ] `db-api` (Supabase, consolidation job, Cloudflare Worker)
- [ ] `verification`
- [ ] CI/CD (`.github/workflows/`)
- [ ] `docs`

## Logs relevantes

<!-- Pegá el log con --verbose si aplica (scrapers/cli.py solo loguea progreso con ese flag). NUNCA pegues cédulas, teléfonos, direcciones o nombres completos reales, sanitizá antes de pegar. -->

```
pega el log acá
```

## Contexto adicional

<!-- Versión del pipeline, fuente afectada (source_id), si es reproducible en local con sources.demo.yaml o solo en producción -->