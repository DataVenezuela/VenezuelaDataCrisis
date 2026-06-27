# Arquitectura: deduplicación y normalización

Este documento explica la capa de dedup/normalización que se agregó al proyecto y
cómo encajan las piezas. El código del scraper está en inglés; el schema de la base
de datos y la documentación están en español.

## Problema

Muchas fuentes descentralizadas reportan los mismos hechos del terremoto con texto
distinto. Hay que **enlazar reportes similares en un registro estable (con UUID) sin
perder la data de ninguna fuente**, y sin fusionar por error hechos reales distintos
(la etapa más peligrosa según el README).

## Modelo de 3 capas

```text
observacion  (crudo por fuente, JSONB, inmutable)   <- la data de cada website
   -> afirmacion  (aserción atómica normalizada; huella UNIQUE = dedup exacto)
        -> entidad  (cosa del mundo real deduplicada; UUID estable)
```

- **observacion**: lo que dijo cada fuente, sin tocar. Procedencia. Nunca se borra.
- **afirmacion**: una aserción ("se necesita agua en Caracas"). Su `huella`
  (SHA-256 de `evento|tipo|ubicación|descripción`) es UNIQUE → impide duplicados
  exactos, incluso entre corridas distintas.
- **entidad**: el hecho del mundo real. Agrupa afirmaciones similares de varias
  fuentes. Porta el UUID que consume la API.

Tablas auxiliares: `membresia_afirmacion` (vínculo afirmación↔entidad, con score y
razón → auditable y reversible) y `alias_entidad` (tras un merge, el UUID viejo
apunta al superviviente → no se rompen referencias externas).

## Flujo

```text
fuentes -> fetch -> extract -> normalize (texto + geo) -> sanitize (PII) -> dedup exacto
   -> export JSONL  (handoff/offline/auditoría)
   -> persist Postgres (fuente de verdad)  -> clustering determinista -> entidades
```

El JSONL NO desaparece: queda como formato de intercambio/offline/auditoría. Postgres
es la fuente de verdad del estado de dedup.

## Qué se agregó (por PR)

### PR1 — Persistencia + dedup exacto
- Postgres en Docker (`docker-compose.yml`, imagen pgvector), portable a Supabase
  cambiando solo `DATABASE_URL`.
- `db/migrations/0001_init.sql`: las 5 tablas del modelo.
- `shared/`: `config` (DATABASE_URL), `hashing` (HMAC para `cedula_hmac`),
  `storage.ClaimStore` (upsert con `ON CONFLICT (huella)`), `seed` (JSONL → DB).
- Pipeline/CLI: flag `--persist`. **Resultado:** correr 2 veces el mismo input ya no
  duplica (lo garantiza el UNIQUE sobre `afirmacion.huella`).

### PR2 — Normalización
- `scrapers/config/gazetteer.ve.json` + `scrapers/normalizers/geo.py`: mapean texto
  libre a una **zona canónica** (estado>municipio>zona). Generaliza a parroquia/zona
  → cumple la política de no guardar dirección exacta. Guarda lat/lon de
  enriquecimiento (links de mapa / desempate por distancia), que NO es la llave de match.
- `scrapers/normalizers/person.py`: normaliza nombres y arma una llave de blocking.
- `db/migrations/0002_geo.sql`: columnas geo en `afirmacion`.
- **Resultado:** "petare", "PETARE, Miranda", etc. resuelven a la misma zona.

### PR3 — Matching determinista + Entity
- `scrapers/config/matching_domains.yaml` + `scrapers/dedup/matcher.py`: llave
  decisiva por dominio (`tipo|zona|día`, o token de identidad para personas). Sin
  señal fuerte → la afirmación NO se fusiona (entidad propia). Postura: precisión > recall.
- `shared/clustering.py`: asigna afirmaciones a entidades de forma **idempotente**
  (`entidad.clave_match` con índice UNIQUE parcial).
- **Resultado:** dos fuentes que reportan agua en Caracas con texto distinto (huellas
  distintas) caen en **una sola entidad**, conservando ambas fuentes.

### PR4 — Hardening + docs
- Persistencia **atómica** (observaciones + afirmaciones en una sola transacción).
- `db/migrations/0004_membresia_unica.sql`: `UNIQUE(afirmacion_id)` → una afirmación
  pertenece a exactamente una entidad.
- Test e2e de enriquecimiento geo + este documento.

## Cómo correr

Ver `db/README.md`. Resumen:

```bash
cp .env.example .env
docker compose up -d
uv venv .venv && source .venv/bin/activate && uv pip install -r scrapers/requirements.txt
python -m scrapers.cli run --config scrapers/config/sources.demo.multi.yaml --persist
python -m shared.clustering
```

## Limitaciones conocidas (deuda explícita)

1. **Dominio `person` inerte.** La señal decisiva de personas es `cedula_hmac`, pero
   el pipeline todavía no tokeniza identidad en cuarentena antes de redactar. Hoy toda
   afirmación de persona desaparecida queda standalone. Pendiente: PR de tokenización.
2. **El day-bucket puede sobre-dividir.** Un mismo hecho reportado en días distintos
   genera entidades distintas. La ventana temporal debería ser configurable por dominio.
3. **Zona horaria.** `matcher._day` corta el ISO a 10 chars; un offset local vs UTC
   puede correr el bucket un día. Normalizar a UTC al persistir.
4. **Gazetteer mínimo.** ~10 zonas y match por alias más largo (ej. "Petare, Caracas"
   resuelve a Caracas, la zona más amplia). Falta jerarquía de especificidad y más zonas.
5. **Sin fuzzy todavía.** El matching es 100% determinista. El fuzzy (con notebooks de
   evidencia precisión/recall) es el siguiente paso, junto con la obsolescencia
   (estados active/stale/resolved + decaimiento de confianza).
