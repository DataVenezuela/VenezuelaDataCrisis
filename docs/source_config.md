# VZLA_DEDUP — Source Config

Este documento define cómo declarar fuentes externas en el pipeline de VZLA_DEDUP.

La configuración de fuentes existe para que el pipeline sepa qué scrapear, cómo fetchearlo, qué parser usar y con cuánta confianza tratar los datos.

---

## Formato YAML

> ⚠️ El bloque `pagination:` de este ejemplo es **aspiracional, no
> implementado**. `SourceConfig` (`scrapers/models/source.py`) no tiene
> campo `pagination` ni `page_size`, y `_get_adapter` en `run_pipeline.py`
> instancia `ApiAdapter` sin pasarlo — siempre usa el default interno de
> `api_adapter.py` (`page_size=20`), sin importar lo que digas en el YAML.
> Si agregás este bloque a una fuente real, el loader lo ignora
> silenciosamente sin error. Ver `AGENTS.md` §2.1 para el detalle completo
> y el impacto medido en producción (`encuentralos_tecnosoft`, ~98.830
> registros con page_size=20 ≈ 4.941 requests).

```yaml
project:
  name: "Venezuela Earthquake 2026"
  event_id: "f0e1d2c3-b4a5-6789-0fed-cba987654321"  # UUID del Event en BD

sources:
  - id: encuentralos_tecnosoft
    name: "Encuentralos (tecnosoft.dev)"
    url: "https://encuentralos.tecnosoft.dev"
    type: api_json
    parser_asignado: encuentralos
    trust_tier: C
    enabled: true
    refresh_minutes: 30
    max_concurrent_pages: 4  # opcional; solo aplica si la primera pagina reporta total
    max_concurrent_posts: 8  # opcional; POSTs paralelos al staging API (default: 1)
    probe_limit: 1000        # opcional; tamaño de la primera request para descubrir el límite real del API
    # pagination:               # NO IMPLEMENTADO — ver advertencia arriba
    #   path: /api/personas
    #   limit_param: limit
    #   offset_param: offset
    #   page_size: 20

  - id: mi_fuente_html
    name: "Hospital Central Barquisimeto"
    url: "https://example.org/listado"
    type: html_static
    parser_asignado: hospital_central    # debe existir en scrapers/parsers/
    trust_tier: B
    enabled: false                       # deshabilitada hasta tener parser
    refresh_minutes: 60
```

---

## Campos

| Campo | Requerido | Descripción |
|---|---|---|
| `id` | sí | Identificador único de la fuente (slug, sin espacios) |
| `name` | sí | Nombre legible para logs y trazabilidad |
| `url` | sí | URL base de la fuente |
| `type` | sí | Tipo de adapter a usar (ver §Tipos) |
| `parser_asignado` | sí | Nombre del parser. Sin parser → cuarentena |
| `trust_tier` | sí | Letra A/B/C/D — nivel de confianza |
| `enabled` | sí | `true`/`false`. Las deshabilitadas se ignoran |
| `refresh_minutes` | no | Frecuencia mínima de scraping. Default: 60 |
| `max_concurrent_pages` | no | Máximo de páginas API en vuelo cuando la primera respuesta reporta `total`, `count`, `total_count` o `totalCount`. Si se omite, `api_adapter.py` usa un default conservador. Sin total confiable, el adapter conserva paginación secuencial. |
| `max_concurrent_posts` | no | Máximo de POSTs en paralelo al staging API durante `export_source()`. Default: `1` (comportamiento secuencial original). Útil para fuentes con muchos registros donde la latencia de red domina. |
| `probe_limit` | no | Entero positivo: tamaño de la primera request de paginación, usado para descubrir el límite real que soporta el API. Si el API devuelve ≥ `probe_limit` registros, ese valor se adopta como `page_size` efectivo; si devuelve menos y hay más datos, el cap detectado queda en los logs. La primera página se reutiliza como datos reales (sin requests extra). Sin este campo, `api_adapter.py` usa el `page_size` configurado o su default interno. Solo aplica a fuentes `api_json`. |
| `allowed_domains` | no | Lista de hosts **exactos** permitidos para `url`. Si se define y el host de la URL no está en la lista, la fuente se omite **sin hacer ningún request** y el error queda visible en el summary. Match exacto, case-insensitive — no acepta subdominios. |
| `rate_limit_per_minute` | no | Entero positivo: máximo de requests por ventana deslizante de 60s. Solo lo aplica `api_json` (es el único adapter que pagina dentro de una corrida); los demás fetchean una vez por corrida y su frecuencia la gobierna `refresh_minutes`. |

No se deben agregar campos nuevos al contrato sin actualizar este documento.

---

## Tipos de fuente

| `type` | Adapter | Cuándo usarlo |
|--------|---------|---------------|
| `api_json` | `api_adapter.py` (httpx) | APIs con respuesta JSON paginada |
| `html_static` | `html_adapter.py` (BeautifulSoup) | HTML servido sin JS |
| `webapp_js` | `playwright_adapter.py` | SPAs o páginas que requieren JS |
| `pdf` | `pdf_adapter.py` (pdfplumber) | PDFs con texto extraíble |
| `manual_file` | `local_file.py` | Archivos locales / uploads manuales |
| `rss` | `rss_adapter.py` (PR #100) | Feeds RSS/Atom |

---

## `trust_tier`

El `trust_tier` en el YAML siempre usa **letras**. La BD usa enteros. La conversión ocurre en el staging exporter.

| Letra | Entero BD | Significado |
|---|---|---|
| `A` | `1` | Fuente oficial: gobierno, USGS, Cruz Roja, FUNVISIS |
| `B` | `2` | ONG verificada o medio establecido |
| `C` | `3` | Voluntario/comunidad con ownership visible |
| `D` | `3` | Anónima o sin verificar |

---

## Fuentes sin parser

Si una fuente no tiene parser asignado todavía, declararla con `enabled: false`. No usar un nombre de parser inventado.

Cuando lleguen registros de una fuente sin parser registrado, el pipeline los envía a **cuarentena** (no al basura, no a un fallback genérico).

---

## Reglas

- La URL no debe contener credenciales, tokens o secretos
- Si `allowed_domains` está presente, el host de `url` debe coincidir exactamente con uno de sus valores
- Si `rate_limit_per_minute` está presente, debe ser un entero positivo
- Si `probe_limit` está presente, debe ser un entero positivo; solo tiene efecto en fuentes `api_json`
- Si la fuente no es pública, su uso debe revisarse antes de agregarse (ver `scrapers/security/SOURCE_POLICY.md`)
- El `id` debe ser único en el archivo
- `trust_tier` = letra, nunca entero en el YAML

---

## Validar config

```bash
python -m scrapers.cli validate --config scrapers/config/sources.demo.yaml
```
