# Architecture Decision Records (ADR)

Este directorio contiene las decisiones de arquitectura del proyecto VZLA_DEDUP.

Cada ADR documenta una decisión significativa: el contexto que la motivó, las
alternativas consideradas, la decisión tomada y sus consecuencias.

Una ADR no se modifica una vez aceptada. Si la decisión cambia, se crea una nueva
ADR que reemplaza a la anterior.

## Índice

| ADR | Título | Estado |
|-----|--------|--------|
| [0001](./0001-arquitectura-serving-publico.md) | Arquitectura del plano de serving público | Aceptada (§5 reemplazada por 0007) |
| [0002](./0002-endurecimiento-seguridad-cloudflare.md) | Endurecimiento de seguridad del plano público con Cloudflare | Propuesta |
| [0003](./0003-reestructuracion-repos-deployment.md) | Reestructuración de repos: pipeline público, deployment y web privados | Propuesta |
| [0004](./0004-versionado-de-contrato.md) | Versionado del contrato DB/scrapers | Propuesta |
| [0005](./0005-gobernabilidad.md) | Gobernabilidad del repositorio y quórum de mantenedores | Propuesta |
| [0006](./0006-proteccion-pii-ingesta.md) | Protección de PII en la ingesta | Propuesta |
| [0007](./0007-modelo-consolidacion-gold.md) | Modelo de consolidación y capa gold | Aceptada |
| [0008](./0008-retencion-pii-bronze-raw-artifacts.md) | Retención de PII en claro en Bronze (raw_artifacts) y reaper de 12h | Propuesta |
