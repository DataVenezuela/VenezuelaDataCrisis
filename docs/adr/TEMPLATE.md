# ADR NNNN — Título corto de la decisión

> Plantilla de ADR para VZLA_DEDUP. Copiar este archivo a
> `docs/adr/NNNN-slug-en-kebab-case.md`, numerando de forma secuencial, y borrar
> este bloque de nota al terminar.
>
> Convenciones (obligatorias):
> - Prosa en **español**; nombres de estados, enums y campos en **inglés**.
> - **Sin em-dashes** en la prosa. Usar dos puntos, comas, paréntesis o partir la
>   frase. Únicas excepciones: el encabezado `#` del título y celdas de tabla
>   vacías.
> - **Una página** como objetivo. Las ADR largas se ignoran; si necesita más,
>   probablemente son dos decisiones.
> - Una ADR no se muta una vez `Aceptada`: se crea otra que la reemplaza.
> - Actualizar el índice en `docs/adr/README.md` al agregar una ADR.

| Campo | Valor |
|---|---|
| Estado | Propuesta |
| Fecha | AAAA-MM-DD |
| Decisores | (roles o personas que deciden) |
| Reemplaza a | (ninguno) |
| Complementa | (ninguno) |
| Relacionado con | (docs, specs u otras ADR) |

---

## 1. Contexto

Qué problema o fuerza motiva la decisión. Restricciones relevantes (costo,
seguridad, PII, tiempo, equipo). Solo lo necesario para entender por qué se
decide ahora. Enlazar a specs o código en vez de reproducirlos.

---

## 2. Decisión

Qué se decide, en voz activa y afirmativa ("Se adopta...", "El contrato se
versiona..."). Si ayuda, subsecciones cortas o una lista. Un diagrama mermaid
`flowchart` es opcional cuando aclara la estructura.

---

## 3. Consecuencias

**Positivas**

- Qué mejora o se desbloquea.

**Negativas / costos asumidos**

- Qué se paga o se pierde a cambio.

**Riesgos y mitigaciones**

- Qué podría salir mal y cómo se acota.

---

## 4. Estado / implementación

Qué existe ya en el código y qué es aspiracional. Una ADR `Propuesta` no debe
leerse como código en producción: dejar claro qué está construido, qué está
parcial y qué falta. Nombrar los símbolos o tablas ausentes cuando aplique.

---

## 5. Enlaces

- Issues, PRs, specs y ADR relacionadas.
- Fuentes o discusiones que respaldan la decisión.
