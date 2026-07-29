# PFIF 1.5 import

PFIF (People Finder Interchange Format) permite intercambiar registros de
personas entre organizaciones. Este repo soporta una primera capa de import:
parsear XML PFIF ya descargado y convertirlo en `Person` para el pipeline.

Esta capa no activa fuentes reales, no maneja credenciales y no hace llamadas de
red. El adapter para endpoints PFIF y la configuracion de fuentes reales deben
coordinarse por separado con cada organizacion.

## Alcance actual

`scrapers/parsers/pfif_parser.py` implementa `ParserProtocol` para payloads XML.
El parser:

- lee `pfif:person` / `pfif:Person`;
- omite registros sin `full_name`;
- normaliza `full_name` con las reglas de nombres del repo;
- combina `home_city` y `home_state` en `last_known_location`;
- deriva `age_range` desde `person_record/date_of_birth` sin guardar la fecha;
- mapea status PFIF a `Person.status`;
- usa `source_name` como `fuente`;
- agrega `notes`, `description` y URL de fuente/perfil a `nota`, con texto de
  contacto redactado.

## Mapeo soportado

| PFIF | `Person` | Nota |
| --- | --- | --- |
| `full_name` | `full_name` | Obligatorio; sin nombre el registro se omite |
| `home_city`, `home_state` | `last_known_location` | Texto normalizado, sin geocoding |
| `source_name` | `fuente` | Si falta, usa `pfif` |
| `source_url`, `profile_url` | `nota` | Trazabilidad textual mientras el modelo no tenga `source_url` |
| `notes` | `nota` | Se redactan emails, telefonos e IDs con forma de documento |
| `person_record/description` | `nota` | Se agrega a la nota redactada |
| `person_record/status` | `status` | Ver tabla abajo |
| `person_record/date_of_birth` | `age_range` | Solo edad calculada; no se guarda fecha exacta |

Status:

| PFIF | `Person.status` |
| --- | --- |
| `missing` | `missing` |
| `alive` | `found` |
| `injured` | `injured` |
| `deceased` | `deceased` |
| `unknown` | `unknown` |
| `inaccessible` | `unknown` |

## Fuera de alcance actual

- Adapter de red para Google Person Finder, Cruz Roja u otra fuente PFIF.
- Autenticacion, paginacion o discovery de endpoints PFIF.
- Activar fuentes PFIF en `sources.venezuela.starter.yaml`.
- Export PFIF; eso pertenece a #83.
- Guardar `photo_url`, contactos directos, telefonos, emails o IDs externos como
  campos persistentes.

## Tests

La cobertura vive en `scrapers/tests/test_pfif_parser.py` con fixture sintetico
en `scrapers/tests/fixtures/pfif_sample.xml`. No contiene datos reales ni hace
requests externos.
