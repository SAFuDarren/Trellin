ALTER TABLE rm.database_identity ADD COLUMN workspace_id uuid;

CREATE FUNCTION rm.current_workspace_binding()
RETURNS TABLE (database_id uuid, workspace_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT identity.id, identity.workspace_id FROM database_identity identity WHERE singleton
$$;

CREATE FUNCTION rm.bind_research_workspace(target_workspace_id uuid, expected_database_id uuid)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE identity database_identity;
BEGIN
    SELECT * INTO STRICT identity FROM database_identity WHERE singleton FOR UPDATE;
    IF $1 IS NULL OR identity.id IS DISTINCT FROM $2 THEN
        RAISE EXCEPTION 'The selected database identity differs from the workspace binding.' USING ERRCODE = '23514';
    END IF;
    IF identity.workspace_id IS NOT NULL AND identity.workspace_id <> $1 THEN
        RAISE EXCEPTION 'This database is already bound to another research workspace.' USING ERRCODE = '23514';
    END IF;
    IF identity.workspace_id IS NULL THEN
        UPDATE database_identity SET workspace_id = $1 WHERE singleton;
    END IF;
    RETURN identity.id;
END $$;

CREATE FUNCTION rm.reidentify_restored_database(expected_database_id uuid, restored_workspace_id uuid)
RETURNS uuid
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE fresh_id uuid;
BEGIN
    IF $2 IS NULL THEN
        RAISE EXCEPTION 'The restored workspace needs a new identity.' USING ERRCODE = '22023';
    END IF;
    UPDATE database_identity SET id = gen_random_uuid(), workspace_id = $2
    WHERE singleton AND id = $1 AND workspace_id IS DISTINCT FROM $2 RETURNING id INTO fresh_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'The archived database identity differs or the restored workspace is not new.'
            USING ERRCODE = '23514';
    END IF;
    RETURN fresh_id;
END $$;

REVOKE ALL ON FUNCTION rm.current_workspace_binding(), rm.bind_research_workspace(uuid, uuid),
    rm.reidentify_restored_database(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.current_workspace_binding() TO rm_human, rm_scheduler, rm_agent, rm_verifier;
GRANT EXECUTE ON FUNCTION rm.bind_research_workspace(uuid, uuid) TO rm_human;
