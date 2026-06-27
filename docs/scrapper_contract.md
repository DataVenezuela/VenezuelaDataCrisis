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

La especificación actual define tres streams JSONL independientes:

```text
Persons.jsonl
acopio.jsonl
events.jsonl
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

## 5. Convenciones globales

Todos los archivos JSONL deben cumplir estas convenciones.

### 5.1 Fechas

Todas las fechas deben estar en UTC.

Formato JSONL:

```text
ISO 8601
```

Ejemplo:

```json
"2026-06-24T14:32:00Z"
```

---

### 5.2 Booleanos

Los booleanos deben ser booleanos reales de JSON.

Correcto:

```json
true
```

```json
false
```

Incorrecto:

```json
1
```

```json
0
```

```json
"Si"
```

```json
"No"
```

```json
"true"
```

```json
"false"
```

---

### 5.3 Nulos

Los valores desconocidos deben exportarse como `null`.

Correcto:

```json
"municipio": null
```

Incorrecto:

```json
"municipio": ""
```

```json
"municipio": "N/A"
```

```json
"municipio": "null"
```

```json
"municipio": 0
```

---

### 5.4 IDs internos

Los IDs internos deben ser UUID v4 como string.

Ejemplo:

```json
"person_record_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

No usar autoincrement entero para IDs que salen del sistema.

---

### 5.5 Enums

Los enums deben ser strings con valores controlados.

No se deben agregar valores nuevos sin actualizar este contrato y la especificación de tipos.

---

### 5.6 HMAC

Los valores HMAC definidos en el schema deben representarse como string hexadecimal SHA-256.

Longitud esperada:

```text
64 caracteres
```

---

### 5.7 Scores

Los scores deben ser números entre:

```text
0.000 y 1.000
```

Ejemplo:

```json
"confidence_score": 0.420
```

---

## 6. Contrato de `events.jsonl`

Una línea representa un evento.

### 6.1 Campos

| Campo             |    Tipo JSONL | Nullable | Descripción                           |
| ----------------- | ------------: | -------: | ------------------------------------- |
| `event_id`        |        string |       no | UUID v4 generado por el sistema       |
| `name`            |        string |       no | Nombre legible del evento             |
| `event_type`      |        string |       no | Tipo de evento                        |
| `occurred_at`     |        string |       no | Fecha/hora del evento en ISO 8601 UTC |
| `affected_states` | array<string> |       sí | Estados venezolanos afectados         |
| `magnitude`       |        number |       sí | Magnitud                              |
| `depth_km`        |        number |       sí | Profundidad en kilómetros             |
| `status`          |        string |       no | Estado del evento                     |
| `external_ids`    |        object |       sí | IDs externos asociados                |

---

### 6.2 Enums

`event_type` permite:

```text
earthquake
flood
landslide
other
```

`status` permite:

```text
active
monitoring
closed
```

---

### 6.3 Ejemplo

```json
{
  "event_id": "f0e1d2c3-b4a5-6789-0fed-cba987654321",
  "name": "Terremoto 24-06-2026",
  "event_type": "earthquake",
  "occurred_at": "2026-06-24T14:32:00Z",
  "affected_states": ["Yaracuy"],
  "magnitude": null,
  "depth_km": 12.50,
  "status": "active",
  "external_ids": {
    "usgs": "us7000n4x",
    "funvisis": "2026-001"
  }
}
```

---

## 7. Contrato de `Persons.jsonl`

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

---

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

---

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

---

### 7.4 `last_known_location`

Debe usar la estructura `location_object` definida en este contrato.

Si no se conoce ubicación:

```json
"last_known_location": null
```

---

### 7.5 Ejemplo

```json
{
  "person_record_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "event_id": "f0e1d2c3-b4a5-6789-0fed-cba987654321",
  "full_name": "JOSE LUIS PEREZ MARIN",
  "alternate_names": ["JOSE PEREZ", "JOSELO PEREZ MARIN"],
  "cedula_hmac": "3b4c9e2a1f...",
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
  "source_url": "https://ejemplo.com/registro/12345"
}
```

---

## 8. Información adicional de persona

La especificación actual define campos para notas o información adicional asociada a una persona.

Estos campos están definidos como `PERSONA → nota, información adicional`.

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

---

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

---

### 8.3 Campos cuando `note_type = "missing"`

| Campo                | Tipo JSONL | Nullable |
| -------------------- | ---------: | -------: |
| `last_seen_at`       |     string |       sí |
| `last_seen_location` |     object |       sí |

---

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

---

### 8.5 Campos cuando `note_type = "found"`

| Campo      | Tipo JSONL | Nullable |
| ---------- | ---------: | -------: |
| `found_at` |     string |       sí |

---

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

## 9. Fotos de persona

La especificación actual define campos para fotos asociadas a personas.

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

## 10. Fuente / corroboración de persona

La especificación actual define campos de fuente o corroboración asociados a personas.

### 10.1 Campos

| Campo              | Tipo JSONL | Nullable | Descripción                          |
| ------------------ | ---------: | -------: | ------------------------------------ |
| `source_id`        |     string |       no | UUID v4                              |
| `person_record_id` |     string |       no | Referencia a persona                 |
| `source_url`       |     string |       no | URL donde se encontró el dato        |
| `ext_id`           |     string |       sí | ID del registro en la fuente externa |
| `trust_tier`       |     number |       no | Nivel de confianza                   |
| `fetched_at`       |     string |       no | Fecha/hora en que fue scrapeado      |

---

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

| Campo              |    Tipo JSONL | Nullable | Descripción                    |
| ------------------ | ------------: | -------: | ------------------------------ |
| `acopio_id`        |        string |       no | UUID v4                        |
| `event_id`         |        string |       no | Referencia a `events.event_id` |
| `name`             |        string |       no | Nombre del centro              |
| `location`         |        object |       sí | Objeto de ubicación            |
| `confidence_score` |        number |       no | Score de confianza             |
| `status`           |        string |       no | Estado del centro              |
| `needs`            | array<string> |       sí | Necesidades                    |
| `last_verified_at` |        string |       sí | Última verificación            |
| `managing_org`     |        string |       sí | Organización responsable       |
| `public_contact`   |        string |       sí | Contacto público               |
| `capacity`         |        number |       sí | Capacidad                      |
| `current_load`     |        number |       sí | Carga actual                   |

---

### 11.2 Enums

`status` permite:

```text
active
full
closed
unverified
```

---

## 12. `location_object`

La estructura de ubicación definida es:

```json
{
  "raw": "El Tocuyo, Lara",
  "estado": "Lara",
  "municipio": "Morán",
  "parroquia": null,
  "lat": 9.7834,
  "lng": -69.7921
}
```

Campos:

| Campo       | Tipo JSONL | Nullable |
| ----------- | ---------: | -------: |
| `raw`       |     string |       sí |
| `estado`    |     string |       sí |
| `municipio` |     string |       sí |
| `parroquia` |     string |       sí |
| `lat`       |     number |       sí |
| `lng`       |     number |       sí |

Si la geocodificación falla, `lat` y `lng` deben quedar como `null`.

El registro no debe descartarse por no tener coordenadas.

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

Si el proyecto decide soportar teléfonos en el contrato JSONL, debe actualizar este documento y el schema antes de que los scrapers lo exporten.

---

## 14. Campos ausentes

Si un scraper no puede obtener un campo, debe exportarlo como `null`.

Ejemplo:

```json
{
  "full_name": "JOSE LUIS PEREZ MARIN",
  "age_range": null,
  "last_known_location": null
}
```

No debe omitir el campo si el campo pertenece al contrato.

---

## 15. Campos no definidos

Un scraper no debe agregar campos nuevos al JSONL sin que estén definidos en este contrato o en la especificación de tipos de datos.

Incorrecto:

```json
{
  "person_record_id": "uuid-v4",
  "instagram_username": "@usuario"
}
```

Si una fuente trae datos adicionales que parecen importantes, se debe abrir una discusión para definir:

* Nombre del campo.
* Tipo JSONL.
* Nullable.
* Entidad a la que pertenece.
* Reglas de seguridad.
* Relación con DB/API.

---

## 16. Normalización mínima requerida

Los parsers deben producir datos compatibles con los tipos definidos.

Normalizaciones mínimas:

* Fechas a UTC ISO 8601.
* Booleanos como `true` / `false`.
* Valores desconocidos como `null`.
* Enums mapeados a los valores permitidos.
* IDs internos como UUID v4.
* Scores como número entre `0.000` y `1.000`.
* HMAC como SHA-256 hex cuando aplique.

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
```

---

## 18. Puntos pendientes de definición

Este contrato no resuelve las ambigüedades que todavía existen en las especificaciones actuales.

### 18.1 Nombre exacto de archivo de personas

La especificación de scraping menciona:

```text
Persons.jsonl
```

La especificación de tipos muestra ejemplo como:

```text
persons.jsonl
```

Pendiente definir casing oficial.

Hasta que se defina, los scrapers deben seguir el nombre que el pipeline de ingestión espere en el código.

---

### 18.2 Notas, fotos y fuentes como archivos separados o embebidos

La especificación de scraping define tres streams JSONL:

```text
Persons.jsonl
acopio.jsonl
events.jsonl
```

Pero la especificación de tipos define entidades/tablas separadas para:

```text
person_notes
person_photos
person_sources
```

Pendiente definir si esas entidades deben exportarse como:

```text
person_notes.jsonl
person_photos.jsonl
person_sources.jsonl
```

o si deben ir embebidas dentro de `Persons.jsonl`.

Hasta que se defina, este documento solo lista los campos definidos, sin decidir una estructura nueva.

---

### 18.3 Teléfonos

La especificación de scraping menciona teléfonos como datos sensibles a reemplazar por HMAC.

El schema actual no define campos de teléfono en JSONL.

Pendiente definir si se agregan campos como:

```text
phone_hmac
phone_masked
```

o si los teléfonos quedan fuera del contrato de exportación.

---

### 18.4 Deduplicación de personas

La especificación actual indica que los duplicados probables deben marcarse para revisión y que un voluntario confirma.

Este documento no define todavía un archivo JSONL de candidatos de deduplicación porque no está definido en el schema actual.

Pendiente definir contrato de salida para duplicados probables, si DB/API lo requiere.

---

## 19. Regla final

Los scrapers deben ser estrictos con el contrato y conservadores con los datos.

```text
No inventar datos.
No agregar campos no definidos.
No exportar PII en claro.
No descartar registros incompletos.
No confirmar merges de personas desde el scraper.
```
