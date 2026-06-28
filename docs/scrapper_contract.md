# VZLA_DEDUP — Scraper Contract

Este documento define el contrato que deben cumplir los scrapers de VZLA_DEDUP.

El objetivo es que todos los scrapers produzcan datos consistentes, seguros y listos para ser ingeridos por DB/API sin que cada fuente invente su propio formato.

Este contrato se basa en las especificaciones actuales del proyecto:

* Pipeline de scraping.
* Salida esperada del parser.
* Especificación de tipos de datos.
* Convenciones globales de JSONL.

---

## 1. Alcance

Este documento aplica a cualquier scraper, parser o módulo de exportación que produzca datos para VZLA_DEDUP.

El contrato cubre:

* Convenciones globales.
* Archivos JSONL esperados.
* Campos por entidad.
* Tipos JSONL.
* Enums permitidos.
* Uso de `null`.
* Estructura de ubicación.
* Reglas mínimas de seguridad para PII.
* Puntos pendientes de definición.

Este documento no define:

* Endpoints de API.
* Modelos internos de base de datos.
* Reglas finales de deduplicación.
* Reglas humanas de verificación.
* UI o consumo público de datos.

---

## 2. Principio general

Cada scraper debe convertir una fuente externa en registros estructurados y compatibles con el contrato JSONL.

El scraper no debe exponer datos sensibles en claro, no debe inventar datos faltantes y no debe descartar registros incompletos solo porque tengan campos ausentes.

Si un valor no existe o no puede determinarse con seguridad, debe exportarse como `null`.

---

## 3. Flujo esperado

El flujo esperado para un scraper es:

```text
Fuente externa
  ↓
Adapter / Fetcher
  ↓
Raw content
  ↓
Parser específico de la fuente
  ↓
Entidad tipada
  ↓
PII / Sanitización
  ↓
Normalización
  ↓
Export JSONL
```

Los adapters obtienen contenido raw.

Los parsers convierten ese contenido raw en entidades tipadas.

Las entidades tipadas actualmente definidas son:

```text
Person
AcopioCenter
Event
```

---
## 4. Archivos de salida definidos actualmente

La especificación actual define los siguientes archivos JSONL independientes:

```text
events.jsonl
persons.jsonl
person_notes.jsonl
person_photos.jsonl
person_sources.jsonl
acopio.jsonl
```

Cada archivo debe usar formato JSONL:

* Una entidad por línea.
* Cada línea debe ser JSON válido.
* No se debe exportar un array completo.
* No se deben omitir campos definidos en el contrato.
* Los campos desconocidos deben ir como `null`.

Ejemplo correcto de JSONL:

```json
{"event_id":"uuid-v4","name":"Terremoto 24-06-2026","event_type":"earthquake"}
{"event_id":"uuid-v4","name":"Otro evento","event_type":"other"}
```

Ejemplo incorrecto:

```json
[
  {"event_id":"uuid-v4","name":"Terremoto 24-06-2026"},
  {"event_id":"uuid-v4","name":"Otro evento"}
]
```

---

## 7. Contrato de `persons.jsonl`

Una línea representa un registro de persona producido desde una fuente.

### 7.1 Campos de identidad

| Campo                 |    Tipo JSONL | Nullable | Descripción                                               |
| --------------------- | ------------: | -------: | --------------------------------------------------------- |
| `person_record_id`    |        string |       no | UUID v4                                                   |
| `event_id`            |        string |       no | Referencia a `events.event_id`                            |
| `full_name`           |        string |       sí | Nombre normalizado                                        |
| `alternate_names`     | array<string> |       sí | Nombres alternativos encontrados                          |
| `cedula_hmac`         |        string |       sí | HMAC SHA-256 de la cédula                                 |
| `cedula_masked`       |        string |       sí | Cédula parcialmente enmascarada                           |
| `age_range`           |        object |       sí | Rango de edad                                             |
| `sex`                 |        string |       sí | Sexo según enum                                           |
| `is_minor`            |       boolean |       sí | `true` si es menor de 18; `null` si no puede determinarse |
| `last_known_location` |        object |       sí | Objeto de ubicación                                       |
| `status`              |        string |       no | Estado de la persona                                      |
| `verification_status` |        string |       no | Estado de verificación                                    |
| `confidence_score`    |        number |       no | Score de confianza                                        |
| `source_url`          |        string |       sí | URL primaria del registro                                 |
| `deterministic_id`    |        string |       sí | ID determinístico basado en hash fonético y ubicación     |

### 7.2 Enums

`sex` permite:

```text
M
F
unknown
```

`status` permite:

```text
missing
found
injured
deceased
unknown
```

`verification_status` permite:

```text
unverified
pending
verified
conflicting
```

### 7.3 `age_range`

`age_range` debe ser un objeto.

Estructura definida:

```json
{
  "min": 30,
  "max": 40
}
```

Si no se conoce la edad o el rango no puede determinarse con seguridad:

```json
"age_range": null
```

### 7.4 `last_known_location`

Debe usar la estructura `location_object` definida en este contrato.

Si no se conoce ubicación:

```json
"last_known_location": null
```

### 7.5 Ejemplo

```json
{
  "person_record_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "event_id": "f0e1d2c3-b4a5-6789-0fed-cba987654321",
  "full_name": "JOSE LUIS PEREZ MARIN",
  "alternate_names": ["JOSE PEREZ", "JOSELO PEREZ MARIN"],
  "cedula_hmac": "3b4c9e2a1fd82f6a0bc347e1a9f2c8d5e047b3a12f9c6d71e8b405a3c2d1f9e0",
  "cedula_masked": "V-****5821",
  "age_range": {
    "min": 30,
    "max": 40
  },
  "sex": "M",
  "is_minor": false,
  "last_known_location": {
    "raw": "El Tocuyo, Lara",
    "estado": "Lara",
    "municipio": "Morán",
    "parroquia": null,
    "lat": 9.7834,
    "lng": -69.7921
  },
  "status": "missing",
  "verification_status": "unverified",
  "confidence_score": 0.420,
  "source_url": "https://encuentralos.org/registro/12345"
}
```

---

## 8. Contrato de `person_notes.jsonl`

Una línea representa una nota, claim o información adicional asociada a una persona.

### 8.1 Campos base de nota

| Campo                 | Tipo JSONL | Nullable | Descripción                                      |
| --------------------- | ---------: | -------: | ------------------------------------------------ |
| `note_record_id`      |     string |       no | UUID v4                                          |
| `person_record_id`    |     string |       no | Referencia a la persona                          |
| `note_type`           |     string |       no | Tipo de nota                                     |
| `found_by`            |     string |       sí | Nombre de quien encontró                         |
| `status`              |     string |       no | Estado de la nota                                |
| `source_date`         |     string |       sí | Fecha en que ocurrió o fue publicado el hecho    |
| `entry_date`          |     string |       no | Fecha en que entra el registro                   |
| `found`               |    boolean |       sí | `true` si fue localizada; `null` si se desconoce |
| `last_known_location` |     object |       sí | Objeto de ubicación                              |

### 8.2 Enums

`note_type` permite:

```text
missing
injured
found
deceased
```

`status` permite:

```text
active
superseded
retracted
```

### 8.3 Campos cuando `note_type = "missing"`

| Campo                | Tipo JSONL | Nullable |
| -------------------- | ---------: | -------: |
| `last_seen_at`       |     string |       sí |
| `last_seen_location` |     object |       sí |

### 8.4 Campos cuando `note_type = "injured"`

| Campo                | Tipo JSONL | Nullable |
| -------------------- | ---------: | -------: |
| `hospital_name`      |     string |       sí |
| `hospital_municipio` |     string |       sí |
| `severity`           |     string |       sí |
| `admitted_time`      |     string |       sí |

`severity` permite:

```text
leve
moderado
grave
critico
unknown
```

### 8.5 Campos cuando `note_type = "found"`

| Campo      | Tipo JSONL | Nullable |
| ---------- | ---------: | -------: |
| `found_at` |     string |       sí |

### 8.6 Campos cuando `note_type = "deceased"`

| Campo                   | Tipo JSONL | Nullable |
| ----------------------- | ---------: | -------: |
| `deceased_at`           |     string |       sí |
| `recovery_location`     |     object |       sí |
| `identification_status` |     string |       sí |
| `confirmed_by`          |     string |       sí |

`identification_status` permite:

```text
identified
unidentified
pending
```

---

## 9. Contrato de `person_photos.jsonl`

Una línea representa una foto asociada a una persona.

### 9.1 Campos

| Campo              | Tipo JSONL | Nullable | Descripción                    |
| ------------------ | ---------: | -------: | ------------------------------ |
| `photo_id`         |     string |       no | UUID v4                        |
| `person_record_id` |     string |       no | Referencia a persona           |
| `url`              |     string |       no | URL de la imagen               |
| `caption`          |     string |       sí | Texto asociado a la foto       |
| `source_id`        |     string |       sí | Referencia a fuente            |
| `uploaded_at`      |     string |       no | Fecha de ingesta en el sistema |

---

## 10. Contrato de `person_sources.jsonl`

Una línea representa una fuente o corroboración asociada a una persona.

### 10.1 Campos

| Campo              | Tipo JSONL | Nullable | Descripción                          |
| ------------------ | ---------: | -------: | ------------------------------------ |
| `source_id`        |     string |       no | UUID v4                              |
| `person_record_id` |     string |       no | Referencia a persona                 |
| `source_url`       |     string |       no | URL donde se encontró el dato        |
| `ext_id`           |     string |       sí | ID del registro en la fuente externa |
| `trust_tier`       |     number |       no | Nivel de confianza                   |
| `fetched_at`       |     string |       no | Fecha/hora en que fue scrapeado      |

### 10.2 `trust_tier`

Valores definidos:

```text
1 = oficial
2 = ONG
3 = social/anónimo
```

---

## 11. Contrato de `acopio.jsonl`

Una línea representa un centro de acopio.

### 11.1 Campos

| Campo              |    Tipo JSONL | Nullable | Descripción                       |
| ------------------ | ------------: | -------: | --------------------------------- |
| `acopio_id`        |        string |       no | UUID v4                           |
| `event_id`         |        string |       no | Referencia a `events.event_id`    |
| `name`             |        string |       no | Nombre del centro                 |
| `location`         |        object |       sí | Objeto de ubicación               |
| `confidence_score` |        number |       no | Score de confianza                |
| `status`           |        string |       no | Estado del centro                 |
| `needs`            | array<string> |       sí | Array de `need_keyword`           |
| `last_verified_at` |        string |       sí | Última verificación               |
| `managing_org`     |        string |       sí | Organización responsable          |
| `contact_hmac`     |        string |       sí | HMAC SHA-256 del contacto         |
| `contact_masked`   |        string |       sí | Contacto parcialmente enmascarado |
| `capacity`         |        number |       sí | Capacidad                         |
| `current_load`     |        number |       sí | Carga actual                      |

### 11.2 Enums

`status` permite:

```text
active
full
closed
unverified
```

`needs[]` permite:

```text
agua
alimentos
medicamentos
colchonetas
ropa
calzado
higiene
pañales
leche_formula
generador
combustible
herramientas
voluntarios
transporte
otro
```

### 11.3 Reglas para `needs`

`needs` debe ser un array de keywords controladas.

El parser debe mapear texto libre antes de exportar.

Ejemplos:

```text
"H2O" -> "agua"
"agua potable" -> "agua"
"AGUA" -> "agua"
```

Si una necesidad no tiene keyword equivalente, usar:

```text
otro
```

No exportar necesidades como texto libre si existe una keyword definida.

### 11.4 Ejemplo

```json
{
  "acopio_id": "h8c9d0e1-f2a3-4567-bcde-678901234567",
  "event_id": "f0e1d2c3-b4a5-6789-0fed-cba987654321",
  "name": "Centro de Acopio Polideportivo Municipal San Felipe",
  "location": {
    "raw": "Polideportivo Municipal, San Felipe, Yaracuy",
    "estado": "Yaracuy",
    "municipio": "San Felipe",
    "parroquia": null,
    "lat": 10.3401,
    "lng": -68.7456
  },
  "confidence_score": 0.850,
  "status": "active",
  "needs": ["agua", "alimentos", "medicamentos", "colchonetas", "pañales"],
  "last_verified_at": "2026-06-26T08:00:00Z",
  "managing_org": "Cruz Roja Venezuela — Seccional Yaracuy",
  "contact_hmac": "9f1c3e7a2b4d6f8e0a2c4e6f8b0d2f4a6c8e0b2d4f6a8c0e2f4b6d8a0c2e4f6",
  "contact_masked": "+58 412 ***7834",
  "capacity": 400,
  "current_load": 283
}
```

---

## 13. Reglas de PII definidas actualmente

La especificación actual indica que las cédulas y teléfonos se reemplazan por HMAC antes de cualquier otro procesamiento y que los campos originales no se guardan.

En el schema actual de persona están definidos:

```text
cedula_hmac
cedula_masked
```

Por lo tanto, el contrato actual para exportación de persona solo contempla esos campos para cédula.

No se deben exportar campos no definidos como:

```text
cedula
document_number
phone
telefono
phone_hmac
phone_masked
```

Para centros de acopio, el schema actual define:

```text
contact_hmac
contact_masked
```

El contacto de un centro de acopio puede ser el teléfono personal de un voluntario, no necesariamente un número institucional.

Por defecto:

```text
contact_hmac   -> HMAC para lookup
contact_masked -> versión enmascarada para display
```

No se debe exportar:

```text
public_contact
contact
phone
telefono
```

Si el equipo de Validation confirma que un contacto es genuinamente público, esa decisión debe manejarse en app layer. El default del contrato JSONL es protegerlo.

Si el proyecto decide soportar teléfonos de personas en el contrato JSONL, debe actualizar este documento y el schema antes de que los scrapers lo exporten.

---

## 17. Validación mínima antes de exportar

Antes de escribir JSONL, cada registro debe validar:

```text
JSON válido
Campos requeridos presentes
Tipos JSONL correctos
Enums permitidos
Fechas en formato ISO 8601 UTC
UUIDs válidos
Scores en rango 0.000 - 1.000
Nulls representados como null
Ausencia de cédula en claro
Ausencia de contacto de acopio en claro
needs[] contiene solo keywords permitidas
```

---

## 18. Puntos pendientes de definición

Este contrato no resuelve las ambigüedades que todavía existen en las especificaciones actuales.

### 18.1 Teléfonos de personas

La especificación de scraping menciona teléfonos como datos sensibles a reemplazar por HMAC.

El schema actual no define campos de teléfono para personas en JSONL.

Pendiente definir si se agregan campos como:

```text
phone_hmac
phone_masked
```

o si los teléfonos de personas quedan fuera del contrato de exportación.

Hasta que se defina, los scrapers no deben exportar teléfonos de personas.

### 18.2 Deduplicación de personas

La especificación actual indica que los duplicados probables deben marcarse para revisión y que un voluntario confirma.

Este documento no define todavía un archivo JSONL de candidatos de deduplicación porque no está definido en el schema actual.

Pendiente definir contrato de salida para duplicados probables, si DB/API lo requiere.
