# Spec: Contrato <nombre> (#issue)

> Plantilla de spec de contrato para VZLA_DEDUP. Copiar a
> `docs/specs/contracts/<slug>.md` y borrar este bloque de nota al terminar.
>
> Convenciones (obligatorias):
> - Prosa en **español**; nombres de campos, columnas, enums y estados en **inglés**.
> - **Sin em-dashes** en la prosa. Usar dos puntos, comas, paréntesis o partir la frase.
> - Una spec **describe** cómo funciona un contrato hoy, no decide. La decisión de
>   versionarlo vive en `docs/adr/0004-versionado-de-contrato.md`.
> - `CONTRACT_VERSION` sigue semver (breaking -> major); ver ADR 0004. Subir la
>   versión en el mismo PR que cambia la forma del contrato.

> **Estado:** Propuesta
> **CONTRACT_VERSION:** 0.0.0
> **Issue:** #NNN
> **Origen:** (ADR, issue o spec que lo motiva)
> **Fecha:** AAAA-MM-DD

---

## 1. Alcance

Qué acopla este contrato (productor -> consumidor) y qué queda explícitamente
fuera. Enlazar a specs o contratos vecinos en vez de reproducirlos.

---

## 2. Precondiciones

Qué debe ser cierto antes de que el contrato aplique (filas previas, variables de
entorno, permisos, auth). Distinguir lo que garantiza el productor de lo que es
config de despliegue.

---

## 3. Interfaz: auth y transporte

Cómo viajan los datos: protocolo, rutas o endpoints, headers, credenciales.

---

## 4. Payload

Las columnas o campos que produce el contrato, cuáles son obligatorios y de dónde
salen. Una tabla `| Campo | Obligatorio | Origen |` suele bastar.

---

## 5. Enums y mapeos

Traducciones de valores entre productor y consumidor (por ejemplo PascalCase del
parser a slug de la DB). Enumerar los valores válidos.

---

## 6. Postcondiciones / garantías

Qué garantiza el contrato tras una operación exitosa: idempotencia, reintentos,
avance de watermark, comportamiento de merge. Describir el comportamiento **real**
verificado en el código, no el aspiracional.

---

## 7. Downstream (contexto)

Qué pasa aguas abajo, fuera del alcance de este contrato. Solo contexto.

---

## 8. Ejemplo de payload (datos ficticios)

```json
{ }
```

---

## 9. Lo que NO garantiza este contrato

Límites explícitos: accesos que no da, schema que no cubre, garantías que no hace.

---

## 10. Referencias

- Módulos y tests que implementan el contrato.
- ADR y specs relacionadas (incluida ADR 0004 para el versionado).
