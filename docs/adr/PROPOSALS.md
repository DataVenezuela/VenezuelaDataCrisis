# Propuestas de ADR (no vinculantes)

> Este documento reúne ideas de decisiones futuras que todavía no ameritan una
> ADR: trabajo operativo aún no construido, sin diseño cerrado ni compromiso del
> equipo. **No es normativo.** Nada aquí está decidido ni implementado; es un
> estacionamiento de ideas y puede borrarse o reescribirse sin proceso de
> reemplazo de ADR. Cuando una propuesta madura hacia una decisión, se promueve a
> una ADR numerada (`docs/adr/NNNN-*.md`) y se quita de aquí.
>
> Convención: prosa en español, enums y campos en inglés, sin em-dashes (igual
> que las ADR).

---

## P4. Telemetría del pipeline

Hoy una corrida del pipeline no deja rastro estructurado: si un scraper falla a
la mitad o se cae el proceso, no hay una fila que lo diga. La idea es una tabla
`pipeline_runs` en Supabase (una fila por corrida: fuente, inicio, fin, filas
leídas, insertadas, cuarentenadas, estado) más un centinela de caída que marque
la corrida como fallida si el proceso muere sin cerrarla. Daría visibilidad a la
cuarentena que ADR 0004 crea (una cuarentena que crece sin observarse es un
riesgo anotado ahí) y una señal para saber si una fuente dejó de producir. No
existe `pipeline_runs` en el código.

---

## P5. (vacante)

Número sin propuesta activa. El hueco es intencional, no una propuesta que falte.

---

## P6. Recuperación ante desastre

El plano interno (Supabase/Postgres) es la fuente de verdad, pero no hay una copia
fría fuera de Supabase ni un procedimiento escrito para reconstruir. La idea es un
export periódico a almacenamiento de objetos (por ejemplo R2) más un runbook de
restauración: cada cuánto se exporta, qué contiene, cómo se valida y cómo se
recupera el estado ante pérdida del proveedor. Debe respetar la regla de oro: la
copia lleva PII, así que vive en almacenamiento privado, nunca en el plano
público. No existe ningún export de este tipo hoy.

---

## P7. Cola de revisión humana

La regla de dominio es que las personas nunca se auto-mergean: un candidato de
duplicado dudoso necesita ojo humano. Falta la cola que haga eso operable. La idea
es una tabla `merge_candidates` (pares o grupos propuestos, con su score y motivo)
priorizada, con un SLA de revisión para que la cola no crezca sin atender. Se
apoyaría en la evidencia que ya produce el dedup. No existe `merge_candidates` en
el código.
