# Política para agregar fuentes

Cada fuente debe tener:

- `id`
- `name`
- `type`
- `enabled`
- `trust_tier`
- `url`
- `refresh_minutes`
- `required_keywords`

## Tipos soportados

- `html_static`
- `api_json`
- `rss`
- `manual_file`

## Trust tier

- A: fuente altamente confiable
- B: fuente institucional/local
- C: medio o página pública
- D: red social/carga manual
- E: baja confianza / solo señal

## Reglas

- No activar fuentes sin revisar frecuencia y términos de uso.
- No usar scraping agresivo.
- No publicar claims sensibles sin verificación.
- No guardar raw salvo cuarentena autorizada.
