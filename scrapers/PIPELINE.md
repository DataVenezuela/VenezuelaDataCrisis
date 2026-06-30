# Pipeline de Limpieza
> Este documento explica qué le pasa a un registro desde que lo recolectamos de una fuente hasta que llega a la base de datos.

No hace falta saber Python para entender el pipeline. Si después quieres ver el código te guiamos.

---

## La Idea Simplificada

> Tomamos datos sucios, dispersos y contradictorios de varias fuentes, y los convertimos en un registro limpio, seguro y comparable. Sin perder nunca el rastro de dónde salió cada dato.

---

## El viaje de un Registro

### Introducción

Antes de empezar hace falta entender que este pipeline tiene auditorias de datos, es decir, guardamos copias antes de procesar. Hay tres bases de datos que usamos:
- Bronze (en desarrollo): Datos sucios sin procesado.
- Silver (existente): Datos estructurados después de procesar.
- Gold (en desarrollo): Datos finales, deduplicados, limpios y consistentes.

### 1. Adaptadores

Lo primero que hacemos es conseguir un registro de una fuente de datos, por ejemplo la página web `encuentralos.tecnosoft.dev`. Esto lo hace el módulo [adapters](adapters/base.py). 

El dato que nos da `encuentralos.tecnosoft.dev` de su API es por ejemplo:

```json
{
  "id": "1fc63e54-838e-4daa-9a6a-346cf34706b5",
  "nombre": "Stefani Lugo",
  "edad": 9,
  "sexo": "Femenino",
  "ultima_ubicacion": "La Guaira edificio Caraballeda",
  "status": "desaparecido",
  "cedula": "V-29384751"
}
```

> Esto es lo que llamamos el **dato crudo** (raw). 

Tiene los problemas típicos de cualquier fuente externa: nombres con mayúsculas inconsistentes, ubicaciones escritas en texto libre, estados no descriptivos, y el problema más serio, una cédula en texto plano (PII).

> Estos registros van a la BD bronze y pasan por parsers.

### 2. Parsing

Aquí recibimos los datos sucios, raw, y los transformamos en objetos consistentes, predecibles y con un formato espécifico. Cada fuente de datos tiene su propio parser, curado por nosotros, asegurandonos así de que los datos salen bien formateados.

Lo que les hacemos en general:
- Extrae cada registro individual de los datos raw.
- Mapea los campos de variables a nuestros nombres internos (ej. `nombre` → `full_name`, `ultima_ubicacion` → `last_known_location`).
- Traduce los valores a nuestro vocabulario en inglés (`"desaparecido"` → `missing`, `"encontrada"` → `found`, etc.).
- Protege la cédula, nunca la deja pasar a la siguiente capa sin enmascarar (link).
- Si un campo no viene en la fuente, lo deja como `null`, no imputamos.
- Si un registro falla al procesarse (dato corrupto, campo inesperado), lo registra como error y sigue con el resto.
- Asocia cada registro con su fuente de origen, para que nunca se pierda el rastro de dónde vino el dato.

> Al final, después de parsear, obtenemos tres tipos: Evento, Acopio y Persona.

#### Protección de datos sensibles

La cédula nunca se guarda en texto plano en ningún lado. Apenas el parser la toca, pasa por una función matemática de un solo sentido (HMAC-SHA256) junto con una clave secreta:
```
V-29384751  →  cedula_hmac: "a3f8e2d91b4c..." (64 caracteres)
```

Esto tiene dos propiedades clave:
- No se puede revertir: No hay forma de recuperar la cédula a partir de ese código.
- Es consistente: La misma cédula siempre produce el mismo código, permitiendonos detectar que dos registros de fuentes distintas referencian a la misma persona.

También guardamos una versión parcial para mostrar en pantalla, tipo `V-****8751` — solo los últimos 4 dígitos.

Si la persona es menor de edad, aplicamos protección adicional. Anulamos la foto y recortamos la ubicación a solo el estado (en vez de la dirección exacta del reporte). Un menor desaparecido es información extremadamente sensible, y reducimos lo que cualquiera puede ver sobre su paradero exacto.

### 3. Normalización

Antes de comparar registros entre fuentes, necesitamos que el mismo dato esté siempre escrito de la misma forma. Si una fuente dice `"José Luis Pérez"` y otra `"jose luis perez"`, para una computadora son cadenas distintas.

Lo que normalizamos:

- Nombres: En mayúsculas, sin tildes, sin espacios dobles --> `"  José   Luis Pérez  "` → `"JOSE LUIS PEREZ"`.
- Fechas: Se convierten a un formato universal (ISO 8601 UTC): `"24/06/2026"` → `"2026-06-24T00:00:00Z"`.
- Ubicaciones: Se limpian y, cuando es posible, se le agregan coordenadas usando un servicio de geocodificación. Si ese servicio falla o no encuentra la ubicación, el registro no se descarta, queda sin coordenadas.

Con los nombres, fechas y ubicaciones ya normalizados, calculamos las huellas de comparación que se usan en el siguiente paso. Para Eventos y Centros de Acopio, un hash determinista; para Personas, un conjunto de pistas (fonética del nombre, ubicación, cédula) que sirven para agrupar candidatos.

Estos datos van a Silver BD que ya está implementada.


### 4. Limpieza

Ya con los datos en Silver, lo que hacemos es compararlos entre ellos mismos y con los datos que ya estan presentes en Gold BD.

Como funciona:
- Tomamos los aportes nuevos en Silver y los agrupamos usando sus huellas de comparación.
  - Para Eventos y Centros de Acopio: Dos registros que comparten el mismo `dedup_hash` se consideran como el mismo objeto. Se fusionan y gana la fuente con mayor `trust_tier`. La fusión queda registrada para trazabilidad.
  - Para Personas: No fusionamos automático. Usamos las `block_keys` (fonética del nombre, ubicación, cédula si está disponible) para agrupar candidatos probables, calculamos un puntaje de similitud entre ellos, y generamos un candidato de revisión.
    - Cada candidato de Persona queda pendiente hasta que un humano lo apruebe o lo rechace. Solo después de esa aprobación el registro pasa a Gold.
    - Si un aporte nuevo no tiene ningún candidato parecido en Gold, se promueve directo como un registro nuevo.
- El proceso es incremental, corre cada 20 minutos, procesa lo que no fue consolidado, y si se interrumpe, retoma desde donde quedó.
- Ningún dato se pierde. Si dos fuentes dicen cosas distintas sobre la misma persona, ambas versiones quedan guardadas, el sistema no sobrescribe, conserva el conflicto para que un humano lo resuelva.

> Esto está por finalizar.

---

## El Pipeline Final
![pipeline_completo.png](../api/pipeline_completo.png)

---

## Los Tres Axiómas

1. La cédula en claro nunca se guarda en ningún lado.
2. Ningún registro se descarta por estar incompleto.
3. Ninguna persona se fusiona automáticamente con otra.