---
name: Fuente nueva
about: Proponer o trackear la integración de una fuente de datos nueva
title: "[FUENTE] "
labels: scrapers, new-source
assignees: ''
---

## Fuente

- **Nombre:** 
- **URL:** 
- **Tipo:** <!-- api_json / html_static / rss / pdf / webapp_js / manual_file -->
- **Entidad que produce:** <!-- Person / AcopioCenter / Event -->

## Volumen estimado

<!-- IMPORTANTE: confirmar el volumen real antes de habilitar en producción. encuentralos_tecnosoft se estimó en ~290 registros y resultó tener ~98.830, esa diferencia causó timeouts en producción. Si es una API paginada, hacé un fetch de la primera página y mirá el campo `total` antes de estimar. -->

- Estimado: 
- Confirmado (si ya se hizo un fetch de prueba): 

## trust_tier propuesto

<!-- A/B/C/D, ver docs/pipeline.md §"Conversión de trust_tier" -->

## Checklist de integración

- [ ] Declarada en `scrapers/config/sources.venezuela.starter.yaml` con `enabled: false` hasta tener parser
- [ ] Parser escrito en `scrapers/parsers/` implementando `ParserProtocol`
- [ ] Parser registrado en `run_pipeline.py::_get_parser`
- [ ] Tests con fixtures sintéticos en `scrapers/tests/`
- [ ] Volumen real confirmado (no solo estimado)
- [ ] Si el volumen es grande (1000+ registros), considerar impacto en
      `timeout-minutes` del job de `ingest.yml` antes de habilitar
- [ ] `trust_tier` documentado y justificado