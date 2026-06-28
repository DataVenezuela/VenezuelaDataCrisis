# VZLA_DEDUP — Source Config

Este documento define cómo declarar fuentes externas para el pipeline de scraping de VZLA_DEDUP.

La configuración de fuentes existe para que los módulos de recolección sepan:

* Qué fuente consultar.
* Qué tipo de fuente es.
* Qué adapter usar.
* Qué parser debe procesarla.
* Qué nivel de confianza tiene.

La configuración debe vivir en un archivo `YAML` o `JSON`.

---

## 1. Objetivo

Cada fuente externa debe declararse antes de ser procesada por el pipeline.

Esto evita que cada scraper tenga URLs, tipos de fuente, parsers o niveles de confianza definidos directamente en código.

La configuración debe ser leída por los módulos de recolección, que luego llaman al adapter correspondiente y al parser asignado.

---

## 2. Campos definidos actualmente

La especificación actual define estos campos para la configuración de fuentes:

| Campo        | Requerido | Descripción                     |
| ------------ | --------: | ------------------------------- |
| `url`        |        sí | URL de la fuente externa        |
| `type`       |        sí | Tipo de fuente                  |
| `parser`     |        sí | Parser asignado a esa fuente    |
| `trust_tier` |        sí | Nivel de confianza de la fuente |

No se deben agregar campos nuevos al contrato obligatorio sin actualizar este documento.

---

## 3. Formato base en YAML

Ejemplo:

```yaml id="j0simx"
url: "https://example.org/fuente-demo"
type: "html_static"
parser: "example_person_parser"
trust_tier: 3
```

---

## 4. Formato base en JSON

Ejemplo:

```json id="dlu5he"
{
  "url": "https://example.org/fuente-demo",
  "type": "html_static",
  "parser": "example_person_parser",
  "trust_tier": 3
}
```

---

## 5. Tipos de fuente

Los tipos de fuente deben mapearse a los adapters definidos por el pipeline.

| Tipo de fuente | Adapter / herramienta |
| -------------- | --------------------- |
| `webapp_js`    | Playwright            |
| `html_static`  | BeautifulSoup         |
| `api_json`     | httpx                 |
| `pdf_manual`   | pdfplumber            |

---

## 6. `url`

`url` representa la ubicación de la fuente externa.

Ejemplo:

```yaml id="2tr6ho"
url: "https://example.org/fuente-demo"
```

Reglas:

* Debe ser una URL válida.
* Debe apuntar a la fuente que será procesada por el adapter.
* No debe contener credenciales, tokens o secretos.
* Si la fuente no es pública, su uso debe ser revisado antes de agregarse.

---

## 7. `type`

`type` indica qué tipo de fuente se está procesando.

Valores definidos actualmente:

```text id="i44ldg"
webapp_js
html_static
api_json
pdf_manual
```

Ejemplo:

```yaml id="tk9qi4"
type: "api_json"
```

El `type` determina qué adapter debe usarse.

---

## 8. `parser`

`parser` indica qué parser debe procesar el contenido obtenido por el adapter.

Ejemplo:

```yaml id="m2dqcm"
parser: "example_person_parser"
```

El parser conoce la estructura específica de la fuente.

El parser debe producir una entidad tipada.

Entidades tipadas definidas actualmente:

```text id="h54lo8"
Person
AcopioCenter
Event
```

Ejemplos conceptuales:

```text id="iux6hq"
example_person_parser -> Person
example_acopio_parser -> AcopioCenter
example_event_parser  -> Event
```

---

## 9. `trust_tier`

`trust_tier` indica el nivel de confianza de la fuente.

Valores definidos actualmente:

```text id="p77ndt"
1 = oficial
2 = ONG
3 = social/anónimo
```

Ejemplo:

```yaml id="5e9uat"
trust_tier: 1
```

El `trust_tier` debe ser un número entero.

No debe usarse string.

Correcto:

```yaml id="y4w2lt"
trust_tier: 1
```

Incorrecto:

```yaml id="16kr9b"
trust_tier: "oficial"
```

---

## 10. Ejemplos por tipo de fuente

### 10.1 WebApp JS

```yaml id="9kd55l"
url: "https://example.org/webapp-demo"
type: "webapp_js"
parser: "example_person_parser"
trust_tier: 3
```

Adapter esperado:

```text id="20k982"
Playwright
```

El adapter de Playwright (`scrapers/adapters/playwright_adapter.py`) acepta
dos campos opcionales en la configuración real (`SourceConfig`) para
controlar su comportamiento:

```yaml
timeout_seconds: 30   # numero positivo en segundos; si se omite, default 30
max_retries: 3         # entero >= 1 (numero total de intentos); si se omite, default 5
```

`max_retries: 0` se rechaza explícitamente (tanto en el validador de YAML
como en el constructor del adapter) porque dejaría el adapter sin hacer
ningún intento de fetch.

---

### 10.2 HTML estático

```yaml id="txd190"
url: "https://example.org/html-demo"
type: "html_static"
parser: "example_person_parser"
trust_tier: 3
```

Adapter esperado:

```text id="zzqsod"
BeautifulSoup
```

---

### 10.3 API JSON

```yaml id="l3zpdt"
url: "https://example.org/api/demo"
type: "api_json"
parser: "example_event_parser"
trust_tier: 1
```

Adapter esperado:

```text id="csgqvy"
httpx
```

---

### 10.4 PDF / archivo manual

```yaml id="af69lb"
url: "https://example.org/documento-demo.pdf"
type: "pdf_manual"
parser: "example_person_parser"
trust_tier: 2
```

Adapter esperado:

```text id="wtxbpv"
pdfplumber
```

---

## 11. Relación con el pipeline

La configuración de fuentes debe alimentar la capa de recolección.

Flujo esperado:

```text id="7le6cf"
Source Config
  ↓
Adapter según type
  ↓
Raw content
  ↓
Parser asignado
  ↓
Entidad tipada
  ↓
Limpieza / PII / Normalización
  ↓
Export JSONL
```

La configuración no debe contener lógica de parsing.

La lógica de parsing debe vivir en el parser asignado.

---

## 12. Validación mínima de configuración

Antes de ejecutar un scraper, la configuración de cada fuente debe validar:

```text id="cg2uu4"
url existe
type existe
parser existe
trust_tier existe
type tiene un valor permitido
trust_tier es 1, 2 o 3
```

Si una fuente no cumple estas reglas, no debe ejecutarse.

---

## 13. Errores esperados

Errores posibles de configuración:

```text id="epq8l1"
missing_url
missing_type
missing_parser
missing_trust_tier
invalid_type
invalid_trust_tier
```

Ejemplo de error:

```json id="kxhdo3"
{
  "error_type": "invalid_type",
  "message": "Unknown source type",
  "field": "type"
}
```

El error no debe incluir datos personales.

---

## 14. Campos pendientes de definición

Actualmente solo están definidos como parte de la configuración:

```text id="mrm71z"
url
type
parser
trust_tier
```

Existen campos útiles que podrían agregarse después, pero todavía no forman parte del contrato definido.

Ejemplos de campos pendientes:

```text id="5jzazl"
source_key
name
enabled
rate_limit
allowed_domains
entity_type
notes
```

No deben tratarse como obligatorios hasta que el equipo los apruebe y actualice este documento.

---

## 15. Pendiente: formato final del archivo

La especificación actual menciona que la configuración puede vivir en `YAML` o `JSON`.

Pendiente definir el nombre y ubicación exacta del archivo.

Opciones posibles a decidir por el equipo:

```text id="0er65n"
scrapers/config/sources.yaml
scrapers/config/sources.json
```

Hasta que se defina, los contribuidores deben seguir la ubicación que use el código actual del pipeline.

---

## 16. Regla final

La configuración de fuentes debe ser simple.

```text id="n7h3z6"
La config declara la fuente.
El adapter obtiene el contenido.
El parser interpreta la estructura.
La limpieza normaliza y protege.
El export genera JSONL.
```

No se debe meter lógica de negocio dentro de la configuración.
