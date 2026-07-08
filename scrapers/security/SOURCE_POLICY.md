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

- `api_json`
- `html_static`
- `webapp_js`
- `pdf`
- `manual_file`
- `rss`
- `x_recent_search`

## Trust tier

- A: fuente oficial (gobierno, USGS, Cruz Roja, FUNVISIS)
- B: ONG verificada o medio establecido
- C: voluntario/comunidad con ownership visible
- D: anónima o sin verificar

Solo existen los tiers `A`/`B`/`C`/`D` (enum `trust_tier`). No hay tier `E`.

## Reglas

- No activar fuentes sin revisar frecuencia y términos de uso.
- No usar scraping agresivo.
- No publicar claims sensibles sin verificación.
- No guardar raw salvo cuarentena autorizada.
