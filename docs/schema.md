# VZLA_DEDUP — Schema

Este documento define las entidades, campos, tipos y enums usados por VZLA_DEDUP.

La especificación actual está pensada para ser compatible con:

```text
PostgreSQL 14+
MySQL 8.0+
JSONL
```

---

## 1. Convenciones globales

| Decisión             | Valor                                                                       |
| -------------------- | --------------------------------------------------------------------------- |
| Zona horaria         | UTC siempre                                                                 |
| Fechas               | `TIMESTAMPTZ` en PostgreSQL / `DATETIME` en MySQL, asumido UTC en app layer |
| Fechas en JSONL      | `string` en formato ISO 8601                                                |
| Booleanos            | `BOOLEAN` nativo. En JSONL: `true` / `false`                                |
| Nulos                | `null` explícito                                                            |
| Strings cortos       | `VARCHAR(n)` con límite razonable                                           |
| Texto libre / listas | `TEXT` o `JSONB` en PostgreSQL / `JSON` en MySQL 8+                         |
| IDs internos         | UUID v4 como string `VARCHAR(36)`                                           |
| Enums                | String con valores controlados                                              |
| HMAC                 | `VARCHAR(64)` — SHA-256 en hex                                              |
| Confianza / scores   | `NUMERIC(4,3)` — rango `0.000` a `1.000`                                    |

---

## 2. Reglas globales JSONL

### Fechas

Todas las fechas deben estar en UTC.

Ejemplo:

```json
"2026-06-24T14:32:00Z"
```

---

### Booleanos

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

---

### Nulos

Correcto:

```json
null
```

Incorrecto:

```json
""
```

```json
"N/A"
```

```json
0
```

---

### IDs internos

Los IDs internos deben ser UUID v4 como string.

Ejemplo:

```json
"a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

No usar autoincrement entero para IDs que salgan del sistema.

---

### Enums

Los enums deben representarse como strings controlados.

No usar `ENUM` nativo de MySQL.

---

### HMAC

Los HMAC deben ser SHA-256 en hexadecimal.

Tipo:

```text
VARCHAR(64)
```

---

### Scores

Los scores deben estar entre:

```text
0.000 y 1.000
```

---

# Entidades

---

## 3. Entidad: `EVENT`

Tabla:

```text
events
```

### Campos

| Campo             | Tipo SQL          | Tipo JSONL        | Nullable | Valores / Notas                        |
| ----------------- | ----------------- | ----------------- | -------: | -------------------------------------- |
| `event_id`        | `VARCHAR(36)` PK  | `string`          |       no | UUID v4 generado por el sistema        |
| `name`            | `VARCHAR(255)`    | `string`          |       no | Nombre legible del evento              |
| `event_type`      | `VARCHAR(50)`     | `string`          |       no | Enum definido                          |
| `occurred_at`     | `TIMESTAMPTZ`     | `string` ISO 8601 |       no | Fecha/hora del evento                  |
| `affected_states` | `TEXT[]` / `JSON` | `array<string>`   |       sí | Lista de estados venezolanos afectados |
| `magnitude`       | `NUMERIC(4,2)`    | `number`          |       sí | Magnitud                               |
| `depth_km`        | `NUMERIC(6,2)`    | `number`          |       sí | Profundidad en kilómetros              |
| `status`          | `VARCHAR(30)`     | `string`          |       no | Enum definido                          |
| `external_ids`    | `JSONB` / `JSON`  | `object`          |       sí | IDs externos asociados                 |

### Enums

`events.event_type`:

```text
earthquake
flood
landslide
other
```

`events.status`:

```text
active
monitoring
closed
```

### Ejemplo JSONL

```json
{
  "event_id": "f0e1d2c3-b4a5-6789-0fed-cba987654321",
  "name": "Terremoto Yaracuy 24-06-2026",
  "event_type": "earthquake",
  "occurred_at": "2026-06-24T14:32:00Z",
  "affected_states": ["Yaracuy", "Lara", "Portuguesa"],
  "magnitude": 7.30,
  "depth_km": 12.50,
  "status": "active",
  "external_ids": {
    "usgs": "us7000n4xy",
    "funvisis": "VEN-2026-001"
  }
}
```

---

## 4. Entidad: `PERSON` — Identidad

Tabla:

```text
persons
```

### Campos

| Campo                 | Tipo SQL         | Tipo JSONL      | Nullable | Valores / Notas                                           |
| --------------------- | ---------------- | --------------- | -------: | --------------------------------------------------------- |
| `person_record_id`    | `VARCHAR(36)` PK | `string`        |       no | UUID v4                                                   |
| `event_id`            | `VARCHAR(36)` FK | `string`        |       no | Referencia a `events.event_id`                            |
| `full_name`           | `VARCHAR(300)`   | `string`        |       sí | Nombre normalizado                                        |
| `alternate_names`     | `JSONB` / `JSON` | `array<string>` |       sí | Nombres alternativos encontrados                          |
| `cedula_hmac`         | `VARCHAR(64)`    | `string`        |       sí | HMAC SHA-256 de la cédula                                 |
| `cedula_masked`       | `VARCHAR(15)`    | `string`        |       sí | Cédula parcialmente enmascarada                           |
| `age_range`           | `JSONB` / `JSON` | `object`        |       sí | Rango de edad                                             |
| `sex`                 | `VARCHAR(10)`    | `string`        |       sí | Enum definido                                             |
| `is_minor`            | `BOOLEAN`        | `boolean`       |       sí | `true` si es menor de 18; `null` si no puede determinarse |
| `last_known_location` | `JSONB` / `JSON` | `object`        |       sí | `location_object`                                         |
| `status`              | `VARCHAR(30)`    | `string`        |       no | Enum definido                                             |
| `verification_status` | `VARCHAR(30)`    | `string`        |       no | Enum definido                                             |
| `confidence_score`    | `NUMERIC(4,3)`   | `number`        |       no | Default `0.000` si no hay datos                           |
| `source_url`          | `TEXT`           | `string`        |       sí | URL primaria del registro                                 |
| `deterministic_id`    | `VARCHAR(16)`    | `string`        |       sí | ID determinístico basado en hash fonético y ubicación     |

### Enums

`persons.sex`:

```text
M
F
unknown
```

`persons.status`:

```text
missing
found
injured
deceased
unknown
```

`persons.verification_status`:

```text
unverified
pending
verified
conflicting
```

### `age_range`

Ejemplo:

```json
{
  "min": 30,
  "max": 40
}
```

Se usa `age_range` en lugar de edad exacta.

### Ejemplo JSONL

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

## 5. Estructura: `location_object`

Estructura usada por campos de ubicación.

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

### Campos

| Campo       | Tipo JSONL | Nullable | Descripción                                  |
| ----------- | ---------- | -------: | -------------------------------------------- |
| `raw`       | `string`   |       sí | Valor original o descriptivo de la ubicación |
| `estado`    | `string`   |       sí | Estado                                       |
| `municipio` | `string`   |       sí | Municipio                                    |
| `parroquia` | `string`   |       sí | Parroquia                                    |
| `lat`       | `number`   |       sí | Latitud                                      |
| `lng`       | `number`   |       sí | Longitud                                     |

Si la geocodificación falla, `lat` y `lng` deben quedar como `null`.

El registro no debe descartarse por no tener coordenadas.

---

## 6. Entidad: `PERSON_NOTE` — Nota / Información adicional

Tabla:

```text
person_notes
```

### Campos base

| Campo                 | Tipo SQL         | Tipo JSONL        | Nullable | Valores / Notas                                  |
| --------------------- | ---------------- | ----------------- | -------: | ------------------------------------------------ |
| `note_record_id`      | `VARCHAR(36)` PK | `string`          |       no | UUID v4                                          |
| `person_record_id`    | `VARCHAR(36)` FK | `string`          |       no | Referencia a `persons.person_record_id`          |
| `note_type`           | `VARCHAR(30)`    | `string`          |       no | Enum definido                                    |
| `found_by`            | `VARCHAR(300)`   | `string`          |       sí | Nombre de quien encontró                         |
| `status`              | `VARCHAR(30)`    | `string`          |       no | Enum definido                                    |
| `source_date`         | `TIMESTAMPTZ`    | `string` ISO 8601 |       sí | Fecha en que ocurrió o fue publicado el hecho    |
| `entry_date`          | `TIMESTAMPTZ`    | `string` ISO 8601 |       no | Fecha en que entra el registro                   |
| `found`               | `BOOLEAN`        | `boolean`         |       sí | `true` si fue localizada; `null` si se desconoce |
| `last_known_location` | `JSONB` / `JSON` | `object`          |       sí | Mismo `location_object` que en `persons`         |

### Enums

`person_notes.note_type`:

```text
missing
injured
found
deceased
```

`person_notes.status`:

```text
active
superseded
retracted
```

---

## 7. Sub-campos de `person_notes` por `note_type`

Estos campos viven en la misma tabla con columnas sparse nullable.

La especificación recomienda columnas sparse en una sola tabla para simplificar consultas.

---

### 7.1 Si `note_type = "missing"`

| Campo                | Tipo SQL         | Tipo JSONL                 | Nullable |
| -------------------- | ---------------- | -------------------------- | -------: |
| `last_seen_at`       | `TIMESTAMPTZ`    | `string` ISO 8601          |       sí |
| `last_seen_location` | `JSONB` / `JSON` | `object` `location_object` |       sí |

---

### 7.2 Si `note_type = "injured"`

| Campo                | Tipo SQL       | Tipo JSONL        | Nullable |
| -------------------- | -------------- | ----------------- | -------: |
| `hospital_name`      | `VARCHAR(255)` | `string`          |       sí |
| `hospital_municipio` | `VARCHAR(100)` | `string`          |       sí |
| `severity`           | `VARCHAR(20)`  | `string`          |       sí |
| `admitted_time`      | `TIMESTAMPTZ`  | `string` ISO 8601 |       sí |

`person_notes.severity`:

```text
leve
moderado
grave
critico
unknown
```

---

### 7.3 Si `note_type = "found"`

| Campo      | Tipo SQL      | Tipo JSONL        | Nullable |
| ---------- | ------------- | ----------------- | -------: |
| `found_at` | `TIMESTAMPTZ` | `string` ISO 8601 |       sí |

---

### 7.4 Si `note_type = "deceased"`

| Campo                   | Tipo SQL         | Tipo JSONL                 | Nullable |
| ----------------------- | ---------------- | -------------------------- | -------: |
| `deceased_at`           | `TIMESTAMPTZ`    | `string` ISO 8601          |       sí |
| `recovery_location`     | `JSONB` / `JSON` | `object` `location_object` |       sí |
| `identification_status` | `VARCHAR(30)`    | `string`                   |       sí |
| `confirmed_by`          | `VARCHAR(300)`   | `string`                   |       sí |

`person_notes.identification_status`:

```text
identified
unidentified
pending
```

---

## 8. Entidad: `PERSON_PHOTO`

Tabla:

```text
person_photos
```

### Campos

| Campo              | Tipo SQL         | Tipo JSONL        | Nullable | Valores / Notas                                |
| ------------------ | ---------------- | ----------------- | -------: | ---------------------------------------------- |
| `photo_id`         | `VARCHAR(36)` PK | `string`          |       no | UUID v4                                        |
| `person_record_id` | `VARCHAR(36)` FK | `string`          |       no | Referencia a persona                           |
| `url`              | `TEXT`           | `string`          |       no | URL de la imagen                               |
| `caption`          | `TEXT`           | `string`          |       sí | Texto asociado a la foto en la fuente original |
| `source_id`        | `VARCHAR(36)` FK | `string`          |       sí | Referencia a `person_sources.source_id`        |
| `uploaded_at`      | `TIMESTAMPTZ`    | `string` ISO 8601 |       no | Cuándo fue ingestada en el sistema             |

---

## 9. Entidad: `PERSON_SOURCE` — Fuente / Corroboración

Tabla:

```text
person_sources
```

### Campos

| Campo              | Tipo SQL         | Tipo JSONL        | Nullable | Valores / Notas                         |
| ------------------ | ---------------- | ----------------- | -------: | --------------------------------------- |
| `source_id`        | `VARCHAR(36)` PK | `string`          |       no | UUID v4                                 |
| `person_record_id` | `VARCHAR(36)` FK | `string`          |       no | Referencia a `persons.person_record_id` |
| `source_url`       | `TEXT`           | `string`          |       no | URL donde se encontró el dato           |
| `ext_id`           | `VARCHAR(255)`   | `string`          |       sí | ID del registro en la fuente externa    |
| `trust_tier`       | `SMALLINT`       | `number` integer  |       no | Nivel de confianza                      |
| `fetched_at`       | `TIMESTAMPTZ`    | `string` ISO 8601 |       no | Cuándo fue scrapeado este dato          |

### `trust_tier`

```text
1 = oficial
2 = ONG
3 = social/anónimo
```

---

## 10. Entidad: `ACOPIO_CENTER`

Tabla:

```text
acopio_centers
```

### Campos

| Campo              | Tipo SQL         | Tipo JSONL                 | Nullable | Valores / Notas                   |
| ------------------ | ---------------- | -------------------------- | -------: | --------------------------------- |
| `acopio_id`        | `VARCHAR(36)` PK | `string`                   |       no | UUID v4                           |
| `event_id`         | `VARCHAR(36)` FK | `string`                   |       no | Referencia a `events.event_id`    |
| `name`             | `VARCHAR(300)`   | `string`                   |       no | Nombre del centro                 |
| `location`         | `JSONB` / `JSON` | `object` `location_object` |       sí | Ubicación                         |
| `confidence_score` | `NUMERIC(4,3)`   | `number`                   |       no | Default `0.000`                   |
| `status`           | `VARCHAR(30)`    | `string`                   |       no | Enum definido                     |
| `needs`            | `JSONB` / `JSON` | `array<string>`            |       sí | Array de `need_keyword`           |
| `last_verified_at` | `TIMESTAMPTZ`    | `string` ISO 8601          |       sí | Última verificación               |
| `managing_org`     | `VARCHAR(255)`   | `string`                   |       sí | Organización responsable          |
| `contact_hmac`     | `VARCHAR(64)`    | `string`                   |       sí | HMAC SHA-256 del contacto         |
| `contact_masked`   | `VARCHAR(30)`    | `string`                   |       sí | Contacto parcialmente enmascarado |
| `capacity`         | `INTEGER`        | `number` integer           |       sí | Capacidad                         |
| `current_load`     | `INTEGER`        | `number` integer           |       sí | Carga actual                      |

### Enums

`acopio_centers.status`:

```text
active
full
closed
unverified
```

`acopio_centers.needs[]`:

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

### Ejemplo JSONL

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

# Resumen de enums

## `events.event_type`

```text
earthquake
flood
landslide
other
```

## `events.status`

```text
active
monitoring
closed
```

## `persons.sex`

```text
M
F
unknown
```

## `persons.status`

```text
missing
found
injured
deceased
unknown
```

## `persons.verification_status`

```text
unverified
pending
verified
conflicting
```

## `person_notes.note_type`

```text
missing
injured
found
deceased
```

## `person_notes.status`

```text
active
superseded
retracted
```

## `person_notes.severity`

```text
leve
moderado
grave
critico
unknown
```

## `person_notes.identification_status`

```text
identified
unidentified
pending
```

## `person_sources.trust_tier`

```text
1 = oficial
2 = ONG
3 = social/anónimo
```

## `acopio_centers.status`

```text
active
full
closed
unverified
```

## `acopio_centers.needs[]`

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

---

# Notas de implementación

## UUIDs vs autoincrement

Los UUIDs permiten generar IDs en el scraper sin necesidad de consultar la DB.

Esto es importante para JSONL que se ingesta en batch.

---

## `age_range` en lugar de `age`

Se usa `age_range` porque las fuentes de crisis no siempre dan edades exactas confiables.

Guardar un rango es más honesto y evita falsos matches en deduplicación.

---

## `trust_tier` como entero

`trust_tier` se guarda como entero porque es más fácil de indexar y comparar que un string.

El mapeo semántico vive en documentación y en app layer.

---

## `needs` en acopio como array de keywords

`needs` se mantiene como array porque un centro de acopio puede tener múltiples necesidades.

Sin embargo, los valores deben ser keywords controladas por el enum `need_keyword`.

El parser es responsable de mapear texto libre al keyword antes de exportar.

Ejemplos:

```text
"H2O" -> "agua"
"agua potable" -> "agua"
"AGUA" -> "agua"
```

Para necesidades que no tengan keyword, se usa:

```text
otro
```

El enum es extensible: agregar un keyword nuevo no rompe registros existentes.

---

## `contact_hmac` / `contact_masked` en acopio

El contacto de un centro de acopio puede ser el teléfono personal de un voluntario, no necesariamente un número institucional.

Por defecto se aplica el mismo patrón que `cedula_hmac`:

```text
contact_hmac   -> HMAC para lookup
contact_masked -> versión enmascarada para display
```

Si el equipo de Validation confirma que un contacto es genuinamente público, se puede exponer desde app layer.

El default es protegerlo.

---

## Campos sparse en `person_notes`

La especificación recomienda una sola tabla con columnas nullable por tipo, en lugar de cuatro tablas separadas.

Esto simplifica consultas del tipo:

```text
dame todas las notas de esta persona
```

---

# Vista pública (ADR 0001 §5)

El artefacto D1 (plano público de serving) contiene **únicamente** los campos
listados aquí. Ningún otro campo es exportado al plano público, por diseño.

La proyección se aplica en la base de datos mediante vistas de solo-lectura
(`tools/sql/public_views.sql`), no en el código del build job, para que sea
imposible filtrar un campo sensible por descuido.

**Campos prohibidos en D1:** cédula en claro, teléfono en claro, contacto
familiar, fotos reales, datos médicos identificables, `raw_content`, y cualquier
campo marcado sensible en `base-standards.md §10`.

## Vista pública: `persons`

Vista SQL: `public_persons`

| Campo | Tipo JSONL | Notas |
|-------|-----------|-------|
| `person_record_id` | `string` UUID v4 | PK |
| `event_id` | `string` UUID v4 | FK → events |
| `full_name` | `string` | normalizado (mayúsculas) |
| `alternate_names` | `array<string>` | |
| `cedula_hmac` | `string` HEX 64 | sin prefijo — solo para lookup |
| `cedula_masked` | `string` | últimos 4 dígitos |
| `age_range` | `object` `{min, max}` | |
| `sex` | `string` enum | |
| `last_known_location` | `object` `location_object` | solo estado/municipio/parroquia/lat/lng |
| `status` | `string` enum | |
| `verification_status` | `string` enum | |
| `confidence_score` | `number` | 0.000–1.000 |
| `source_url` | `string` | trazabilidad |
| `deterministic_id` | `string` | ID fonético precomputado para FTS5 blocking |

## Vista pública: `acopio_centers`

Vista SQL: `public_acopio`

| Campo | Tipo JSONL | Notas |
|-------|-----------|-------|
| `acopio_id` | `string` UUID v4 | PK |
| `event_id` | `string` UUID v4 | FK → events |
| `name` | `string` | |
| `location` | `object` `location_object` | |
| `confidence_score` | `number` | |
| `status` | `string` enum | |
| `needs` | `array<string>` | keywords normalizadas |
| `last_verified_at` | `string` ISO 8601 | |
| `managing_org` | `string` | |
| `contact_masked` | `string` | enmascarado |
| `capacity` | `number` | |
| `current_load` | `number` | |

## Vista pública: `events`

Vista SQL: `public_events`

| Campo | Tipo JSONL | Notas |
|-------|-----------|-------|
| `event_id` | `string` UUID v4 | PK |
| `name` | `string` | |
| `event_type` | `string` enum | |
| `occurred_at` | `string` ISO 8601 | |
| `affected_states` | `array<string>` | |
| `magnitude` | `number` | nullable |
| `depth_km` | `number` | nullable |
| `status` | `string` enum | |
| `external_ids` | `object` | nullable |
