CREATE TABLE rm.database_identity (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    id uuid NOT NULL DEFAULT gen_random_uuid()
);
INSERT INTO rm.database_identity DEFAULT VALUES;
CREATE FUNCTION rm.current_database_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT id FROM database_identity WHERE singleton
$$;
REVOKE ALL ON FUNCTION rm.current_database_id() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.current_database_id() TO rm_human, rm_scheduler, rm_agent, rm_verifier;

INSERT INTO rm.production_checkers (domain, checker_name, checker_version, supports_refutation, requires_formalization)
VALUES ('linear-program', 'exact-linear-program', '1', true, true),
       ('semidefinite-program', 'exact-semidefinite-program', '1', true, true);
