# VZLA_DEDUP — Pipeline técnico

Este documento describe el flujo técnico del pipeline de VZLA_DEDUP.

El objetivo es recolectar registros dispersos, convertirlos en entidades tipadas, proteger datos sensibles, normalizarlos y enviarlos a staging en Supabase para que el consolidation job los deduplique y los mueva a las tablas canónicas.

---

## Flujo completo

```
Fuentes externas
      ↓
Adapters (fetch raw)
      ↓
Parsers (raw → entidad tipada)
      ↓
PII masking (HMAC antes que nada)
      ↓
Normalización (texto, fechas, ubicaciones)
      ↓
Claves de dedup pre-calculadas (dedup_hash, block_keys)
      ↓
┌─────────────────────────────┐     ┌──────────────────────┐
│  Raw DB (R2 + Supabase)     │     │  Quarantine DB        │
│  Payload enmascarado,       │ ←── │  Sin parser, PII      │
│  inmutable, trazable        │     │  no redactable, etc.  │
└─────────────────────────────┘     └──────────────────────┘
      ↓
Staging exporter → POST /api/v1/dedup/* → aportes (Supabase)
      ↓  consolidation job (cada 20 min)
Canonical: persons / events / acopio_centers
      ↓  build job (cada 30 min)
Cloudflare D1 → Worker → API pública
```

---

## Capa 1 — Adapters

Cada tipo de fuente tiene un adapter dedicado. El adapter solo hace fetch: devuelve un `RawContent` con el payload crudo y metadatos del request (status HTTP, timestamp, hash del contenido). No interpreta ni transforma nada.

| Tipo | Módulo | Estado |
|------|--------|--------|
| `api_json` | `scrapers/adapters/api_adapter.py` | ✅ Implementado (httpx, paginación, retry) |
| `html_static` | `scrapers/adapters/html_adapter.py` | ✅ Implementado (requests + BeautifulSoup) |
| `rss` | `scrapers/adapters/rss_adapter.py` | ✅ Implementado (un registro por `<item>` del feed) |
| `manual_file` / `text` | `scrapers/adapters/local_file.py` | ✅ Implementado (lectura local) |
| `pdf` | `scrapers/adapters/pdf_adapter.py` | ✅ Implementado (pdfplumber) |
| `webapp_js` | `scrapers/adapters/playwright_adapter.py` | ✅ Implementado (Playwright headless, timeout/retries configurables) |

Helpers de implementación compartidos entre adapters (timestamp UTC, hash de
contenido para `content_hash`, backoff exponencial con jitter) viven en
`scrapers/adapters/_shared.py` — ningún adapter debería reimplementarlos.

### Parsers implementados

| Parser | Módulo | Estado |
|--------|--------|--------|
| `encuentralos` | `scrapers/parsers/encuentralos_parser.py` | ⏳ Pendiente |
| Fallback genérico | `_TextFallbackParser` (interno) | ✅ Para text/html/rss |

### Ejecución

```bash
# Pipeline completo con fuentes del config
python -m scrapers.cli run --config scrapers/config/sources.yaml --output scrapers/runtime_output

# Limitar a N registros por fuente
python -m scrapers.cli run --config scrapers/config/sources.yaml --output scrapers/runtime_output --limit 50
```

### Diseño de resiliencia

- Un error en un registro individual no tumba el pipeline.
- Un error en una fuente entera se loguea y se continúa con la siguiente.
- `adapter.close()` corre en `finally` dentro de `_run_source()`: si el fetch
  de una fuente falla (ej. Playwright agota sus reintentos), el adapter
  libera sus recursos (browser, conexiones) igual, sin importar si la fuente
  finalmente cuenta como error o no.
- `PII_SALT` es opcional en CI: sin salt, los campos PII crudos se eliminan antes de exportar.
- La deduplicación de Person se excluye intencionalmente del orquestador (requiere revisión humana).

### Tests

28 tests de integración offline en `scrapers/tests/test_run_pipeline.py`.

---

## Principios del pipeline

El pipeline sigue estos principios:

1. Cada capa tiene una responsabilidad clara.
2. La recolección no debe conocer reglas de negocio.
3. Los parsers no deben persistir PII en claro.
4. La limpieza debe operar sobre entidades tipadas.
5. La deduplicación de personas no debe ser destructiva.
6. Todo output debe mantener trazabilidad hacia la fuente.
7. Los registros incompletos no deben descartarse automáticamente.
8. Los errores de un registro no deben tumbar todo el pipeline.
9. Los campos desconocidos deben exportarse como `null`.
10. Nada de datos reales debe aparecer en tests, fixtures o documentación.

---

## Diagrama general

```mermaid
flowchart TD
    A[Fuentes externas] --> B[Adapters / Fetchers]
    B --> C[RawSnapshot interno]
    C --> D[Parsers]
    D --> E[Entidades tipadas]
    E --> F[PII Sanitizer]
    F --> G[Normalizer]
    G --> H[Dedup Engine]
    H --> I[Technical Validator]
    I --> J[JSONL Export]
    J --> K[DB / API]
    K --> L[Verification]
```

---

## Capas del pipeline

## 1. Fuentes externas

Las fuentes externas son los lugares desde donde se obtiene información.

Ejemplos:

* Webs públicas.
* WebApps con JavaScript.
* APIs públicas.
* PDFs públicos.
* Archivos manuales autorizados.
* Planillas públicas.
* Publicaciones verificables.
* Fuentes oficiales.
* Fuentes de organizaciones humanitarias.

No todas las fuentes deben tener el mismo nivel de confianza.

Cada fuente debe estar declarada en una configuración explícita antes de ser scrapeada.

---

## 2. Source Config

Antes de crear un scraper, la fuente debe registrarse en un archivo de configuración.

Ejemplo:

```yaml
source_key: hospital_central_demo
name: Hospital Central Demo
type: html_static
entity_type: person
url: https://example.org/demo
parser: hospital_central_person_parser
trust_tier: 1
enabled: true
rate_limit_per_minute: 10
allowed_domains:
  - example.org
notes: Fuente demo sin datos reales.
```

La configuración debe indicar:

* Qué fuente se va a consultar.
* Qué tipo de fuente es.
* Qué parser debe procesarla.
* Qué entidad produce.
* Qué nivel de confianza tiene.
* Qué dominios están permitidos.
* Qué límites de consulta deben respetarse.

La configuración detallada vive en `docs/source_config.md`.

---

## 3. Adapters / Fetchers

Los adapters son responsables únicamente de obtener contenido raw desde una fuente.

Un adapter no debe interpretar el significado del contenido.

### Tipos de adapters

```text
webapp_js    → Playwright
html_static  → BeautifulSoup / httpx
api_json     → httpx
pdf_manual   → pdfplumber
local_file   → lectura local controlada
```

### Responsabilidad del adapter

El adapter debe:

* Hacer fetch de la fuente.
* Respetar rate limits.
* Validar dominio permitido.
* Capturar status HTTP.
* Capturar content type.
* Calcular hash del contenido.
* Devolver raw content al parser.
* Registrar errores técnicos sin PII.

El adapter no debe:

* Normalizar nombres.
* Hashear cédulas.
* Deduplicar personas.
* Decidir estados de negocio.
* Hacer merges.
* Persistir datos sensibles.
* Loguear contenido raw con PII.

---

## Salida interna del adapter

La salida del adapter es un objeto interno llamado `RawSnapshot`.

Ejemplo:

```json
{
  "source_key": "hospital_central_demo",
  "source_url": "https://example.org/demo",
  "fetched_at": "2026-06-24T15:30:00Z",
  "http_status": 200,
  "content_type": "text/html",
  "content_hash": "sha256:examplehash",
  "raw_content": "<html>...</html>"
}
```

`raw_content` puede contener PII.

Por eso:

* Es solo de uso interno.
* No debe exportarse a JSONL.
* No debe commitearse.
* No debe imprimirse completo en logs.
* No debe persistirse sin una política explícita de seguridad.

---

## 4. Parsers

Los parsers convierten el contenido raw de una fuente en entidades tipadas.

Cada fuente debe tener su propio parser, porque cada fuente puede tener estructuras, nombres de campos y formatos distintos.

Ejemplos:

```text
encuentralos_parser      → Person
veneconnect_parser       → AcopioCenter
usgs_parser              → Event
hospital_central_parser  → Person
```

---

## Responsabilidad del parser

El parser debe:

* Recibir un `RawSnapshot`.
* Extraer registros individuales.
* Mapear campos de la fuente al modelo interno.
* Convertir estados externos a enums internos.
* Extraer fechas, ubicaciones, nombres y notas.
* Enviar datos sensibles al sanitizer antes del export.
* Asociar cada registro con su fuente.
* Producir entidades tipadas.

El parser no debe:

* Guardar PII en claro.
* Loguear cédulas, teléfonos o direcciones exactas.
* Hacer deduplicación global.
* Confirmar que dos personas son la misma.
* Descartar registros por estar incompletos.
* Inventar campos que la fuente no tiene.

---

## NLP y texto libre

Cuando una fuente contiene texto libre, como PDFs narrativos o HTML sin estructura clara, el parser puede usar extracción de entidades.

Ejemplos:

* Nombres de personas.
* Hospitales.
* Estados.
* Municipios.
* Fechas.
* Condición reportada.
* Centros de acopio.
* Necesidades urgentes.

Este paso pertenece al parser o a un extractor usado por el parser.

La limpieza posterior no debería trabajar sobre texto crudo, sino sobre entidades ya tipadas.

---

## Salida interna del parser

El parser debe producir entidades tipadas.

Ejemplo conceptual:

```json
{
  "entity_type": "person",
  "source_key": "hospital_central_demo",
  "source_url": "https://example.org/demo",
  "raw_external_id": "row-15",
  "full_name_raw": "José Luis Pérez",
  "cedula_raw": "V-12345678",
  "phone_raw": null,
  "age_raw": "aprox. 35",
  "status_raw": "No localizado",
  "location_raw": "El Tocuyo, Lara",
  "source_date_raw": "24/06/2026 14:30"
}
```

Este objeto es interno.

Antes de exportar, debe pasar por sanitización PII y normalización.

---

## 5. Entidades tipadas

Después del parser, el sistema debe trabajar con entidades tipadas.

Entidades principales:

```text
Event
Person
PersonNote
PersonSource
PersonPhoto
AcopioCenter
DedupCandidate
```

La idea es que el resto del pipeline no dependa de la estructura original de la fuente.

Una vez que existe una entidad tipada, los módulos de limpieza, normalización, deduplicación y export pueden ser reutilizados para muchas fuentes.

---

## 6. PII Sanitizer

La sanitización de PII debe ocurrir lo antes posible después del parsing.

PII significa información que puede identificar, ubicar o contactar directamente a una persona.

Ejemplos:

* Cédula.
* Teléfono.
* Dirección exacta.
* Nombre de contacto familiar.
* Fotos.
* Información de menores.
* Datos médicos sensibles.
* Ubicación exacta de una persona vulnerable.

---

## Responsabilidad del PII Sanitizer

El sanitizer debe:

* Hashear cédulas usando HMAC SHA-256.
* Hashear teléfonos si el proyecto decide almacenarlos.
* Generar versiones masked cuando aplique.
* Eliminar valores crudos antes del export.
* Evitar que PII llegue a logs.
* Evitar que PII llegue a errores serializados.
* Marcar datos sensibles para revisión si aplica.

Ejemplo:

```json
{
  "cedula_hmac": "sha256-hmac-hex",
  "cedula_masked": "V-****5821"
}
```

No se debe exportar:

```json
{
  "cedula": "V-12345678"
}
```

---

## Regla crítica sobre PII

El parser puede tocar PII en memoria para transformarla, pero la PII cruda no debe persistirse ni aparecer en logs, fixtures, outputs o commits.

---

## Política de normalización de `cedula_hmac`

`cedula_hmac` se calcula sobre el valor normalizado de la cédula
(`shared.hashing.identity_token` / `hmac_hex`, usados también por
`scrapers.sanitizers.pii_tokenizer.mask_cedula`). Esa normalización:

* Quita puntuación, espacios y acentos.
* **Conserva** la letra de nacionalidad (V/E) si la fuente la trae.

Decisión explícita: la letra de nacionalidad SÍ forma parte del
identificador canónico. `"V12345678"` y `"12345678"` (mismos dígitos, sin
prefijo) producen `cedula_hmac` **distintos**.

Por qué: los rangos de cédula V (venezolano) y E (extranjero) se asignan de
forma independiente, así que los mismos 8 dígitos pueden pertenecer a dos
personas reales distintas según el prefijo. Ignorar el prefijo arriesga un
falso merge entre esas dos personas, que es justo el daño que busca evitar
la "Regla crítica de deduplicación" (ver sección 8): *fusionar mal puede
ser peligroso*, *duplicar es tolerable*.

Costo aceptado: si una fuente reporta la cédula sin el prefijo de
nacionalidad (error de captura o formato), ese registro no va a coincidir
por `cedula_hmac` con el mismo dato sí-prefijado. Mitigación: `cedula_hmac`
es una señal de blocking/similarity, no la única — el scoring de Personas
(ver "Similarity scoring") debe poder generar candidatos por nombre, edad y
ubicación aunque `cedula_hmac` no coincida; la revisión humana decide el
merge final.

Si en el futuro se decide ignorar el prefijo de nacionalidad, ese cambio
debe documentarse explícitamente aquí y migrar/recalcular los
`cedula_hmac` ya exportados — no son compatibles entre políticas distintas.

---

## Protección de menores (`is_minor`)

`Person.is_minor` es `bool | None`: `true` si la persona reportada es menor
de 18 años, `false` si se sabe que es mayor, `None` si no se puede
determinar (no hay edad reportada).

Solo el valor explícito `true` activa protección — `None`/`false` no
disparan ninguna reducción, porque ausencia de edad no implica minoría de
edad.

Cuando `is_minor=true`, antes de exportar a `persons.jsonl`
(`scrapers.sanitizers.minor_protection.protect_minor_fields`, ejecutado como
etapa propia del pipeline justo antes del export):

* `foto` se anula (`null`) — una foto es directamente identificable.
* `cedula_masked` se anula (`null`) — deja de mostrarse la cédula parcial en
  claro. `cedula_hmac` **no** se toca: sigue siendo un hash, no identifica
  por sí solo, y Stage 1 lo necesita para matching.
* `last_known_location` se acota a nivel estado (`"Municipio, Estado"` →
  `"Estado"`) para no facilitar la localización exacta de un menor.

`EncuentralosParser` deriva `is_minor` automáticamente cuando la fuente
reporta `edad` (`edad < 18` → `true`); si la fuente no reporta edad,
`is_minor` queda en `None`. Cualquier parser futuro que reciba una edad
puntual o un `age_range` debe derivar `is_minor` de la misma forma.

Pendiente para Stage 1 (#81): `person_sources` (tabla/modelo todavía no
implementado en este repo) debe heredar/propagar la misma protección — un
`PersonSource` corroborando un `Person` con `is_minor=true` no debe exponer
más detalle del que expone el `Person` protegido.

---

## 7. Normalizer

El normalizer convierte datos heterogéneos en formatos estables.

Debe trabajar sobre entidades ya tipadas y sanitizadas.

---

## Responsabilidad del normalizer

El normalizer debe normalizar:

* Nombres.
* Fechas.
* Ubicaciones.
* Enums.
* Rango de edad.
* Estados de persona.
* Estados de acopio.
* Necesidades.
* Strings vacíos.
* Booleanos.

---

## Reglas globales de normalización

```text
Fechas      → UTC ISO 8601
IDs         → UUID v4
Nulls       → null explícito
Booleanos   → true / false
Enums       → strings controlados
Scores      → número entre 0.000 y 1.000
```

No usar:

```text
""
"N/A"
"null"
"None"
"desconocido" como sustituto de null
0 como sustituto de valor desconocido
"Si" / "No" para booleanos
```

---

## Normalización de nombres

Reglas recomendadas:

* Trim de espacios.
* Colapsar espacios múltiples.
* Convertir a mayúsculas.
* Normalizar unicode.
* Remover caracteres invisibles.
* Mantener nombre original solo si existe política para eso.
* Guardar variantes en `alternate_names`.

Ejemplo:

```text
"  José   Luis Pérez  "
```

Debe normalizarse como:

```text
"JOSE LUIS PEREZ"
```

---

## Normalización de fechas

Helpers compartidos entre adapters (timestamp UTC, hash de contenido, backoff exponencial) viven en `scrapers/adapters/_shared.py`.

---

## Capa 2 — Parsers

Cada fuente tiene un parser específico que implementa `ParserProtocol`. El parser recibe el `RawContent` del adapter y devuelve `list[Person | AcopioCenter | Event]`.

El parser conoce la estructura de su fuente: qué campo es el nombre, qué campo es la cédula, qué valor de status mapea a qué enum.

**Agregar una fuente nueva = escribir un parser nuevo.** El resto del pipeline no cambia.

| Parser | Módulo | Entidad | Estado |
|--------|--------|---------|--------|
| `encuentralos` | `scrapers/parsers/encuentralos_parser.py` | `Person` | ✅ |

Si una fuente no tiene parser asignado, sus registros van a **cuarentena** — no al basura, no a un fallback genérico. El FallbackParser fue eliminado.

---

## Capa 3 — Limpieza (orden fijo e inamovible)

### 3.1 PII — va primero

Cédulas y teléfonos se HMAC antes de cualquier otro procesamiento. El campo original no se guarda en ningún lugar.

- `cedula_hmac` = `shared/hashing.identity_token(cedula, secret)` → hex puro 64 chars, sin prefijo
- `cedula_masked` = últimos 4 dígitos con máscara (`V-****5821`)
- `telefono_contacto` de terceros se descarta explícitamente (familiar que reportó)

El secreto viene de `PII_HMAC_SECRET` (env var). Sin él, el pipeline no produce HMAC — los campos quedan `None`. En CI offline esto está aceptado; en producción es obligatorio.

### 3.2 Normalización — va antes de dedup

El matching necesita texto uniforme. `"JOSE LUIS"` y `"José Luis"` deben ser el mismo registro antes de comparar.

- **Texto:** unicode, tildes, mayúsculas, espacios, abreviaciones venezolanas (`ve_abbreviations.json`)
- **Fechas:** todo a ISO 8601 UTC (`normalize_date`)
- **Ubicaciones:** nombre normalizado + coordenadas opcionales via OpenStreetMap (`normalize_location`). Si la API falla, `lat/lng = null`; el registro no se descarta
- **NLP:** para fuentes de texto libre (PDFs, HTML narrativo), `spaCy es_core_news_sm` extrae entidades antes del mapeo

### 3.3 Claves de dedup — se calculan aquí, antes de enviar a staging

- `dedup_hash` — SHA-256 del contenido normalizado. Para Event y AcopioCenter, dos registros con el mismo hash son duplicados exactos.
- `block_keys` — para Person: fonética del nombre (Double Metaphone / NYSIIS) + primeras letras + estado. Permite agrupar candidatos sin comparar todos contra todos.

---

## Capa 4 — Staging exporter (Issue #81)

Lee las entidades procesadas y hace `POST /api/v1/dedup/*` a `dataVenezuela`.

Responsabilidades del exporter:
- Convertir `trust_tier` A/B/C/D → 1/2/3 (la BD usa enteros)
- Enviar primero el Event si la entidad es Person o AcopioCenter (FK obligatoria)
- Manejar reintentos y backoff en caso de error de red
- Avanzar el watermark por fuente solo cuando todos los registros de esa fuente llegaron exitosamente

El exporter no toma decisiones de dedup. Su única responsabilidad es persistir en staging.

---

## Capa 5 — Consolidation job (Issue #82)

Proceso independiente que corre cada 20 minutos. Lee `aportes WHERE consolidated_at IS NULL`.

**Event y AcopioCenter:** dedup automática por `dedup_hash`. El registro con mayor `trust_tier` gana. La decisión queda en `dedup_decisions`.

**Person:** nunca auto-merge. El job calcula similitud (Jaro-Winkler + HMAC match + rango de edad + ubicación) dentro de bloques fonéticos y genera candidatos en `dedup_candidates`. Un voluntario humano aprueba o rechaza. Cada versión anterior queda en `canonical_record_versions`.

El job es incremental e idempotente: si se interrumpe, la próxima corrida retoma desde donde quedó sin re-procesar lo ya consolidado.

---

## Salida de deduplicación de personas

La deduplicación de personas debe producir candidatos.

Ejemplo:

```json
{
  "candidate_id": "uuid-v4",
  "event_id": "uuid-v4",
  "left_person_record_id": "uuid-v4",
  "right_person_record_id": "uuid-v4",
  "score": 0.87,
  "reasons": [
    "similar_name",
    "same_state",
    "compatible_age_range"
  ],
  "blocking_key": "JLS-PE-LARA",
  "decision": "pending",
  "created_at": "2026-06-24T17:30:00Z"
}
```

El estado inicial debe ser:

```text
pending
```

---

## Regla crítica de deduplicación

```text
Duplicar es tolerable.
Fusionar mal puede ser peligroso.
```

Por eso:

* No eliminar registros originales.
* No sobrescribir fuentes.
* No perder notas.
* No descartar estados conflictivos.
* No confirmar automáticamente identidades dudosas.
* No usar solo nombre como criterio de merge.

---

## 9. Technical Validator

El validator revisa que las entidades cumplan el contrato técnico antes de exportarse.

No verifica la verdad del dato en el mundo real.

Solo valida estructura, tipos, enums y reglas mínimas.

---

## Responsabilidad del validator

Debe validar:

* JSON serializable.
* Campos requeridos.
* Tipos correctos.
* Enums permitidos.
* Fechas ISO 8601 UTC.
* UUIDs válidos.
* Scores entre 0 y 1.
* `null` correcto.
* Ausencia de PII en claro.
* Referencias internas coherentes.

---

## Validator vs Verification

El validator técnico responde:

```text
¿Este registro cumple el contrato?
```

Verification responde:

```text
¿Este dato es cierto, vigente y corroborado?
```

Son responsabilidades distintas.

---

## 10. Export JSONL

El export genera archivos JSONL listos para DB/API.

Cada línea debe ser un JSON válido.

Ejemplo:

```json
{"person_record_id":"uuid-v4","event_id":"uuid-v4","full_name":"JOSE PEREZ"}
{"person_record_id":"uuid-v4","event_id":"uuid-v4","full_name":"MARIA GOMEZ"}
```

No se debe exportar un array completo.

Incorrecto:

```json
[
  {"person_record_id": "uuid-v4"},
  {"person_record_id": "uuid-v4"}
]
```

Correcto:

```json
{"person_record_id": "uuid-v4"}
{"person_record_id": "uuid-v4"}
```

---

## Archivos de salida recomendados

```text
events.jsonl
persons.jsonl
person_notes.jsonl
person_sources.jsonl
person_photos.jsonl
acopio_centers.jsonl
dedup_candidates.jsonl
```

---

## Reglas del export

Cada export debe cumplir:

1. UTF-8.
2. Una entidad por línea.
3. JSON válido por línea.
4. Campos requeridos presentes.
5. Campos desconocidos como `null`.
6. Fechas en UTC ISO 8601.
7. IDs como UUID v4.
8. Enums controlados.
9. Sin PII en claro.
10. Trazabilidad hacia fuente.

---

## 11. DB/API

DB/API ingiere los JSONL producidos por el pipeline.

Responsabilidades de DB/API:

* Validar nuevamente schema.
* Guardar entidades.
* Mantener relaciones.
* Mantener trazabilidad.
* Exponer endpoints seguros.
* Controlar qué campos son públicos.
* Proteger datos sensibles.
* Permitir revisión humana.
* Permitir actualizaciones sin destruir historial.

DB/API no debe asumir que un registro está verificado solo porque fue ingerido correctamente.

---

## Conversión de `trust_tier`

Los modelos tipados (`Person`, `Event`, `AcopioCenter`) usan `trust_tier: str` con valores `A/B/C/D` por legibilidad en configs y código de parsers.

La tabla `person_sources` persiste el trust tier como `SMALLINT` (`1/2/3`). Ningún módulo del pipeline hace esa conversión hoy — la implementa Stage 1 (#81) al momento de escribir `person_sources`.

Mapeo canónico:

```text
A → 1   (fuente oficial)
B → 2   (ONG verificada)
C → 2   (ONG no verificada)
D → 3   (redes sociales, anónimo)
```

Este mapeo es la única fuente de verdad para esa conversión — cualquier implementación de Stage 1 debe usar exactamente estos valores en vez de inventar una escala propia.

---

## 12. Verification

Verification valida datos contra fuentes externas, organizaciones, hospitales, voluntarios o revisión humana.

Responsabilidades:

* Confirmar o rechazar candidatos de duplicado.
* Marcar registros como verificados.
* Marcar conflictos.
* Resolver estados contradictorios.
* Validar centros de acopio activos.
* Corroborar claims sensibles.
* Mantener evidencia.
* Evitar borrar historial.

Estados sugeridos:

```text
unverified
pending
verified
conflicting
```

---

## Manejo de conflictos

Los conflictos no deben resolverse borrando información.

Ejemplo:

Una fuente dice:

```text
Persona desaparecida
```

Otra fuente dice:

```text
Persona encontrada
```

El sistema debe preservar ambas fuentes y crear una actualización verificable.

No se debe sobrescribir sin trazabilidad.

---

## 13. Manejo de errores

Un registro inválido no debe tumbar todo el pipeline.

Los errores deben clasificarse.

Tipos sugeridos:

```text
fetch_error
parse_error
validation_error
pii_error
schema_error
rate_limit_error
unknown_error
```

Ejemplo:

```json
{
  "source_key": "hospital_central_demo",
  "error_type": "parse_error",
  "message": "Missing required field: status",
  "record_ref": "row-15",
  "occurred_at": "2026-06-24T17:00:00Z"
}
```

El error no debe incluir PII.

---

## 14. Logs

Los logs deben ayudar a depurar sin exponer personas.

Permitido:

```text
source_key
source_url general
http_status
content_hash
cantidad de registros
tipo de error
record_ref no sensible
duración del proceso
```

No permitido:

```text
cédulas completas
teléfonos completos
direcciones exactas
nombres completos sensibles
raw_content completo
fotos reales
datos médicos identificables
tokens
cookies
secretos
```

---

## 15. Runtime output

Los archivos generados localmente deben ir en:

```text
scrapers/runtime_output/
```

Esa carpeta debe estar ignorada por Git.

No se deben commitear:

```text
*.jsonl
*.csv
*.xlsx
*.pdf
*.db
*.sqlite
screenshots reales
imágenes reales
```

---

## 16. Tests mínimos del pipeline

Cada módulo debe tener tests con fixtures ficticios.

Casos mínimos:

1. Fuente vacía.
2. Fuente con un registro válido.
3. Fuente con campos incompletos.
4. Fuente con fecha inválida.
5. Fuente con ubicación no geocodificable.
6. Fuente con enum desconocido.
7. Registro con cédula en claro antes del sanitizer.
8. Verificación de que la cédula no aparece en output.
9. Output JSONL válido.
10. Error controlado sin tumbar el pipeline.

---

## 17. Flujo esperado para agregar una nueva fuente

1. Un error en un registro individual no tumba el pipeline.
2. Un error en una fuente entera se loguea y se continúa con la siguiente.
3. Los registros sin parser van a cuarentena, no al basura.
4. La PII se enmascara antes que cualquier persistencia.
5. La dedup de personas no es destructiva: propone, un humano decide.
6. Todo registro mantiene trazabilidad hacia la fuente y el raw artifact.
7. Los campos desconocidos se exportan como `null`, nunca se omiten.
8. El staging exporter avanza el watermark solo cuando confirma entrega.

---

## Ejecución local

```bash
# Tests (deben pasar siempre)
pytest scrapers/tests

# Demo offline con datos sintéticos
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml

# Limitar registros por fuente (útil para desarrollo)
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml --limit 10

# Validar config de fuentes
python -m scrapers.cli validate --config scrapers/config/sources.demo.yaml
```

---

## Estado de implementación

| Componente | Estado |
|-----------|--------|
| Adapters (todos) | ✅ |
| `encuentralos` parser | ✅ |
| PII HMAC (`shared/hashing.py`) | ✅ |
| Normalización (texto, fechas, ubicaciones, NLP) | ✅ |
| Modelos Pydantic (Person/AcopioCenter/Event) | ⏳ fix #85 pendiente |
| Staging exporter | ❌ Issue #81 |
| Raw artifact store (R2) | ❌ bloqueado por #81 |
| Quarantine DB | ❌ bloqueado por #81 |
| Watermark por fuente | ❌ Issue #57, bloqueado por #81 |
| Consolidation job | ❌ Issue #82, bloqueado por #81 |
| Build job (Supabase → D1) | ❌ bloqueado por canonical |
| Cloudflare Worker | ❌ bloqueado por build job |