-- PR4 (hardening): una afirmación pertenece a EXACTAMENTE una entidad.
-- Defensa de integridad: aunque la lógica ya lo garantiza, el constraint impide
-- que una regresión asigne la misma afirmación a dos entidades.

ALTER TABLE membresia_afirmacion
    ADD CONSTRAINT uq_membresia_afirmacion_unica UNIQUE (afirmacion_id);
