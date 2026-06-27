-- PR3: clustering determinista.
-- `clave_match` guarda la llave decisiva que agrupa afirmaciones en una entidad.
-- UNIQUE permite múltiples NULL (entidades standalone sin señal fuerte) pero
-- una sola entidad por llave concreta => idempotente entre runs.

ALTER TABLE entidad
    ADD COLUMN IF NOT EXISTS clave_match TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_entidad_clave_match
    ON entidad (clave_match)
    WHERE clave_match IS NOT NULL;
