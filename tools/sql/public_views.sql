-- public_views.sql
-- Vistas de solo-lectura para la API pública (ADR 0001 §5).
-- Las reglas de reducción para menores se aplican aquí,
-- no en los build jobs, para prevenir exposición accidental.

-- =============================================================
-- Vista: public_persons
-- Proyecta la tabla interna `persons` al schema público,
-- aplicando reducción de campos identificables cuando
-- is_minor = true (issue #103).
-- =============================================================

CREATE OR REPLACE VIEW public_persons AS
SELECT
    person_record_id,
    event_id,

    -- Campos identificables: NULL cuando is_minor = true
    CASE WHEN is_minor = true THEN NULL ELSE full_name END          AS full_name,
    CASE WHEN is_minor = true THEN NULL ELSE alternate_names END    AS alternate_names,
    CASE WHEN is_minor = true THEN NULL ELSE cedula_hmac END        AS cedula_hmac,
    CASE WHEN is_minor = true THEN NULL ELSE cedula_masked END      AS cedula_masked,

    -- Ubicación: solo estado cuando is_minor = true
    CASE
        WHEN is_minor = true THEN split_part(last_known_location, ',', -1)
        ELSE last_known_location
    END AS last_known_location,

    -- Campos no identificables: siempre visibles
    age_range,
    sex,
    status,
    verification_status,
    confidence_score,
    is_minor,
    source_url,
    deterministic_id

FROM persons;
