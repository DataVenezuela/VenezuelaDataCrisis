# Base de datos (Postgres)

Capa de persistencia de VZLA_DEDUP. Postgres es la **fuente de verdad** del estado
de deduplicación; el JSONL del pipeline queda como export/handoff/auditoría.

Local con Docker hoy; portable a **Supabase** cambiando solo `DATABASE_URL`.

## Modelo (3 capas)

```text
observacion  (crudo por fuente, JSONB, inmutable)   <- la data de cada website
   -> afirmacion  (normalizado; huella UNIQUE = dedup exacto)
        -> entidad  (UUID estable; cluster deduplicado)   [se puebla en PR futuro]
```

Tablas: `observacion`, `afirmacion`, `entidad`, `membresia_afirmacion`, `alias_entidad`.
El schema está en español; el código Python del scraper queda en inglés y `shared/storage.py`
mapea las llaves del pipeline a las columnas. PR1 puebla hasta `afirmacion`.

## Levantar la DB

```bash
cp .env.example .env          # ajustar credenciales/secreto
docker compose up -d          # Postgres 16 + pgvector
```

La migración `db/migrations/0001_init.sql` corre automáticamente en la primera
inicialización del volumen.

> Si cambias el schema, recrea el volumen para re-correr la migración:
> `docker compose down -v && docker compose up -d`

## Entorno Python

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -r scrapers/requirements.txt
```

## Correr el pipeline con persistencia

```bash
# Auto-persiste si DATABASE_URL está en el entorno/.env
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml --persist
```

## Seed desde JSONL existente

```bash
python -m shared.seed --output-dir scrapers/runtime_output
```

## Clustering determinista (asignar afirmaciones a entidades)

Agrupa afirmaciones en entidades por su llave decisiva (zona+tipo+día, o token de
identidad para personas). Determinista e idempotente. Sin señal fuerte => entidad propia.

```bash
python -m scrapers.cli run --config scrapers/config/sources.demo.multi.yaml --persist
python -m shared.clustering
# Dos fuentes que reportan el mismo hecho -> una sola entidad:
psql "$DATABASE_URL" -c "SELECT clave_match, count(*) FROM entidad e JOIN membresia_afirmacion m ON m.entidad_uuid=e.uuid GROUP BY clave_match;"
```

## Verificar dedup exacto cross-run

```bash
# Correr dos veces: la segunda no debe insertar claims nuevos.
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml --persist
python -m scrapers.cli run --config scrapers/config/sources.demo.yaml --persist
psql "$DATABASE_URL" -c "SELECT count(*) FROM afirmacion;"
```

## Tests de DB

```bash
DATABASE_URL=postgresql://vzla:vzla_local_dev@localhost:5432/vzla_dedup \
  pytest scrapers/tests/test_storage.py
```

Sin `DATABASE_URL`, los tests de DB se omiten (la suite offline sigue verde).
