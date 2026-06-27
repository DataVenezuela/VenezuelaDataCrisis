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

### 2.1 Fechas

Todas las fechas deben estar en UTC.

Ejemplo:

```json
"2026-06-24T14:32:00Z"
```

---

### 2.2 Booleanos

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

### 2.3 Nulos

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

### 2.4 IDs internos

Los IDs internos deben ser UUID v4 como string.

Ejemplo:

```json
"a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

No usar autoincrement entero para IDs que salgan del sistema.

---

### 2.5 Enums

Los enums deben representarse como strings controlados.

No usar `ENUM` nativo de MySQL.

---

### 2.6 HMAC

Los HMAC deben ser SHA-256 en hexadecimal.

Tipo:

```text
VARCHAR(64)
```

---

### 2.7 Scores

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

### Ejemplo

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
| `cedula_masked`       | `VARCHAR(15)`    | `string`        |       sí | Solo últimos dígitos visibles                             |
| `age_range`           | `JSONB` / `JSON` | `object`        |       sí | Rango de edad                                             |
| `sex`                 | `VARCHAR(10)`    | `string`        |       sí | Enum definido                                             |
| `is_minor`            | `BOOLEAN`        | `boolean`       |       sí | `true` si es menor de 18; `null` si no puede determinarse |
| `last_known_location` | `JSONB` / `JSON` | `object`        |       sí | `location_object`                                         |
| `status`              | `VARCHAR(30)`    | `string`        |       no | Enum definido                                             |
| `verification_status` | `VARCHAR(30)`    | `string`        |       no | Enum definido                                             |
| `confidence_score`    | `NUMERIC(4,3)`   | `number`        |       no | Default `0.000` si no hay datos                           |
| `source_url`          | `TEXT`           | `string`        |       sí | URL primaria del registro                                 |

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

### Ejemplo

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

| Campo              | Tipo SQL         | Tipo JSONL                 | Nullable | Valores / Notas                |
| ------------------ | ---------------- | -------------------------- | -------: | ------------------------------ |
| `acopio_id`        | `VARCHAR(36)` PK | `string`                   |       no | UUID v4                        |
| `event_id`         | `VARCHAR(36)` FK | `string`                   |       no | Referencia a `events.event_id` |
| `name`             | `VARCHAR(300)`   | `string`                   |       no | Nombre del centro              |
| `location`         | `JSONB` / `JSON` | `object` `location_object` |       sí | Ubicación                      |
| `confidence_score` | `NUMERIC(4,3)`   | `number`                   |       no | Default `0.000`                |
| `status`           | `VARCHAR(30)`    | `string`                   |       no | Enum definido                  |
| `needs`            | `JSONB` / `JSON` | `array<string>`            |       sí | Necesidades                    |
| `last_verified_at` | `TIMESTAMPTZ`    | `string` ISO 8601          |       sí | Última verificación            |
| `managing_org`     | `VARCHAR(255)`   | `string`                   |       sí | Organización responsable       |
| `public_contact`   | `VARCHAR(300)`   | `string`                   |       sí | Contacto público               |
| `capacity`         | `INTEGER`        | `number` integer           |       sí | Capacidad                      |
| `current_load`     | `INTEGER`        | `number` integer           |       sí | Carga actual                   |

### Enums

`acopio_centers.status`:

```text
active
full
closed
unverified
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

## `needs` en acopio como array

Los centros de acopio pueden tener necesidades múltiples y cambiantes.

Un array de strings normalizado es más flexible que columnas individuales.

---

## Campos sparse en `person_notes`

La especificación recomienda una sola tabla con columnas nullable por tipo, en lugar de cuatro tablas separadas.

Esto simplifica consultas del tipo:

```text
dame todas las notas de esta persona
```
