-- A draft belongs to one researcher and node inside the bound project
-- database. Keeping its scope in this database also preserves it when a
-- verified project archive is restored under a new workspace identity.
CREATE TABLE rm.workbench_proof_drafts (
    user_id uuid NOT NULL REFERENCES rm.local_users(id),
    claim_id uuid NOT NULL REFERENCES rm.claims(id),
    node_id text NOT NULL CHECK (length(node_id) BETWEEN 1 AND 128),
    revision uuid NOT NULL DEFAULT gen_random_uuid(),
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 1500 AND text ~ '[^[:space:]]'),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (user_id, claim_id, node_id)
);
REVOKE ALL ON rm.workbench_proof_drafts FROM PUBLIC, rm_human, rm_agent, rm_scheduler, rm_verifier;

-- Use the existing activity refresh hint. The event contains the claim ID
-- and area, while the draft and node ID stay in this relation.
CREATE CONSTRAINT TRIGGER publish_workspace_change
    AFTER INSERT OR UPDATE OR DELETE ON rm.workbench_proof_drafts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_workspace_change_event('activity', 'claim');

CREATE FUNCTION rm.read_workbench_proof_draft(target_workspace uuid, target_claim uuid, target_node text)
RETURNS TABLE (revision uuid, text text, updated_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_workspace_event_reader($1);
    RETURN QUERY
        SELECT draft.revision, draft.text, draft.updated_at
        FROM workbench_proof_drafts draft
        JOIN current_local_user() author ON author.id = draft.user_id
        WHERE draft.claim_id = $2 AND draft.node_id = $3;
END $$;

CREATE FUNCTION rm.save_workbench_proof_draft(target_workspace uuid, target_claim uuid, target_node text,
    expected_revision uuid, draft_text text)
RETURNS TABLE (revision uuid, text text, updated_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE author_id uuid;
BEGIN
    PERFORM require_workspace_event_reader($1);
    SELECT author.id INTO STRICT author_id FROM current_local_user() author;
    IF $2 IS NULL OR $3 IS NULL OR length($3) NOT BETWEEN 1 AND 128
        OR $5 IS NULL OR length($5) NOT BETWEEN 1 AND 1500 OR $5 !~ '[^[:space:]]' THEN
        RAISE EXCEPTION 'a proof draft needs a claim, node, and 1 to 1500 characters' USING ERRCODE = '22023';
    END IF;

    IF $4 IS NULL THEN
        RETURN QUERY
            INSERT INTO workbench_proof_drafts AS draft (user_id, claim_id, node_id, text)
            VALUES (author_id, $2, $3, $5)
            ON CONFLICT (user_id, claim_id, node_id) DO NOTHING
            RETURNING draft.revision, draft.text, draft.updated_at;
    ELSE
        RETURN QUERY
            UPDATE workbench_proof_drafts draft
            SET revision = gen_random_uuid(), text = $5, updated_at = clock_timestamp()
            WHERE draft.user_id = author_id AND draft.claim_id = $2 AND draft.node_id = $3
                AND draft.revision = $4
            RETURNING draft.revision, draft.text, draft.updated_at;
    END IF;
END $$;

CREATE FUNCTION rm.discard_workbench_proof_draft(target_workspace uuid, target_claim uuid, target_node text,
    expected_revision uuid, required_text text DEFAULT NULL)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_workspace_event_reader($1);
    DELETE FROM workbench_proof_drafts draft USING current_local_user() author
    WHERE draft.user_id = author.id AND draft.claim_id = $2 AND draft.node_id = $3
        AND draft.revision = $4 AND ($5 IS NULL OR draft.text = $5);
    RETURN FOUND;
END $$;

REVOKE ALL ON FUNCTION rm.read_workbench_proof_draft(uuid, uuid, text),
    rm.save_workbench_proof_draft(uuid, uuid, text, uuid, text),
    rm.discard_workbench_proof_draft(uuid, uuid, text, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.read_workbench_proof_draft(uuid, uuid, text),
    rm.save_workbench_proof_draft(uuid, uuid, text, uuid, text),
    rm.discard_workbench_proof_draft(uuid, uuid, text, uuid, text) TO rm_human;
