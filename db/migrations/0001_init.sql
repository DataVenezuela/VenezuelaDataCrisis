-- Migración inicial VZLA_DEDUP.
-- Modelo de 3 capas: observacion (crudo) -> afirmacion (normalizado) -> entidad (deduplicada).
-- PR1 puebla hasta `afirmacion`. Las tablas de clustering (entidad / membresia_afirmacion /
-- alias_entidad) se crean aquí pero las llena un PR posterior.

CREATE EXTENSION IF NOT EXISTS vector;  -- listo para near-dup semántico (pgvector) futuro

-- ---------------------------------------------------------------------------
-- observacion: captura cruda y literal de una fuente. Inmutable, procedencia.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS observacion (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fuente_id       TEXT        NOT NULL,
    fuente_nombre   TEXT,
    fuente_url      TEXT,
    nivel_confianza TEXT,
    crudo           JSONB       NOT NULL,   -- "la data de cada website", sin modificar
    hash_crudo      TEXT        NOT NULL,
    capturado_en    TIMESTAMPTZ,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Evita reinsertar la misma captura de la misma fuente.
    UNIQUE (fuente_id, hash_crudo)
);

-- ---------------------------------------------------------------------------
-- afirmacion: aserción atómica normalizada. `huella` UNIQUE = dedup EXACTO.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS afirmacion (
    id                  TEXT PRIMARY KEY,                  -- claim_id (claim_<huella[:16]>)
    huella              TEXT        NOT NULL UNIQUE,        -- fingerprint: guarda de duplicado exacto
    evento_id           TEXT,
    fuente_id           TEXT,
    fuente_nombre       TEXT,
    fuente_url          TEXT,
    tipo                TEXT,                               -- claim_type
    descripcion         TEXT,
    ubicacion_texto     TEXT,
    confianza           DOUBLE PRECISION,                  -- confidence_score
    estado_verificacion TEXT        NOT NULL DEFAULT 'new',
    evidencia_texto     TEXT,
    nivel_confianza     TEXT,                               -- trust_tier
    hash_crudo          TEXT,
    observacion_id      BIGINT REFERENCES observacion (id),
    capturado_en        TIMESTAMPTZ,                        -- fetched_at
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadatos           JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_afirmacion_evento    ON afirmacion (evento_id);
CREATE INDEX IF NOT EXISTS idx_afirmacion_tipo      ON afirmacion (tipo);
CREATE INDEX IF NOT EXISTS idx_afirmacion_ubicacion ON afirmacion (ubicacion_texto);

-- ---------------------------------------------------------------------------
-- entidad: cosa del mundo real deduplicada. Porta el UUID estable.
-- (Creada en PR1, poblada por el PR de clustering.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entidad (
    uuid          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dominio       TEXT,                                  -- matching domain
    estado        TEXT        NOT NULL DEFAULT 'active', -- active/stale/resolved/conflict
    consolidado   JSONB       NOT NULL DEFAULT '{}'::jsonb, -- vista consolidada (recomputable)
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entidad_dominio_estado ON entidad (dominio, estado);

-- ---------------------------------------------------------------------------
-- membresia_afirmacion: vínculo afirmacion <-> entidad, auditable y reversible.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS membresia_afirmacion (
    entidad_uuid  UUID NOT NULL REFERENCES entidad (uuid) ON DELETE CASCADE,
    afirmacion_id TEXT NOT NULL REFERENCES afirmacion (id) ON DELETE CASCADE,
    puntaje       DOUBLE PRECISION,   -- score de la coincidencia
    razon         TEXT,               -- qué señal disparó la unión
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (entidad_uuid, afirmacion_id)
);

CREATE INDEX IF NOT EXISTS idx_membresia_afirmacion ON membresia_afirmacion (afirmacion_id);

-- ---------------------------------------------------------------------------
-- alias_entidad: tras un merge, el UUID fusionado apunta al superviviente.
-- Mantiene vivas las referencias externas viejas.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alias_entidad (
    alias_uuid    UUID PRIMARY KEY,
    canonico_uuid UUID        NOT NULL REFERENCES entidad (uuid) ON DELETE CASCADE,
    razon         TEXT,
    fusionado_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);
