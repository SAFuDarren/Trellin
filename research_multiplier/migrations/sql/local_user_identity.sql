-- One local researcher per bound database. Restoring the same research
-- preserves its actor IDs while regenerating the physical workspace binding.
CREATE TABLE rm.local_users (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    display_name text NOT NULL DEFAULT 'Local researcher' CHECK (btrim(display_name) <> ''),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON rm.local_users FROM PUBLIC;
CREATE TRIGGER local_users_append_only BEFORE UPDATE OR DELETE ON rm.local_users
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.initialize_bound_local_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    INSERT INTO local_users DEFAULT VALUES ON CONFLICT (singleton) DO NOTHING;
    RETURN NEW;
END $$;

-- Serialize the initial scan with both the first bind and restore.
LOCK TABLE rm.database_identity IN SHARE ROW EXCLUSIVE MODE;
INSERT INTO rm.local_users (singleton)
SELECT true FROM rm.database_identity b WHERE b.singleton AND b.workspace_id IS NOT NULL;
CREATE TRIGGER seed_bound_local_user AFTER UPDATE OF workspace_id ON rm.database_identity
    FOR EACH ROW WHEN (NEW.workspace_id IS NOT NULL AND NEW.workspace_id IS DISTINCT FROM OLD.workspace_id)
    EXECUTE FUNCTION rm.initialize_bound_local_user();

CREATE FUNCTION rm.current_local_user() RETURNS TABLE (id uuid, display_name text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT u.id, u.display_name FROM local_users u
    JOIN database_identity b ON b.singleton = u.singleton
    WHERE b.workspace_id IS NOT NULL
$$;

CREATE FUNCTION rm.is_local_human_session() RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM pg_roles login WHERE login.rolname = session_user AND login.rolcanlogin
          AND NOT (login.rolsuper OR login.rolcreaterole OR login.rolcreatedb
                   OR login.rolreplication OR login.rolbypassrls)
          AND pg_has_role(login.oid, 'rm_human', 'USAGE')
          AND NOT pg_has_role(login.oid, 'rm_scheduler', 'MEMBER')
          AND NOT pg_has_role(login.oid, 'rm_agent', 'MEMBER')
          AND NOT pg_has_role(login.oid, 'rm_verifier', 'MEMBER')
    )
$$;

CREATE FUNCTION rm.attribute_local_user() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE resolved uuid;
BEGIN
    -- Review copies forwarded from an old verifier receipt and program
    -- decisions retain their original provenance and no new human ID.
    IF TG_TABLE_NAME = 'decisions' THEN
        IF NEW.decided_by <> 'human' OR NEW.authenticated_role IS DISTINCT FROM session_user THEN
            RETURN NEW;
        END IF;
    END IF;
    IF NOT is_local_human_session() THEN
        RETURN NEW;
    END IF;
    SELECT u.id INTO resolved FROM current_local_user() u;
    IF resolved IS NULL OR (NEW.user_id IS NOT NULL AND NEW.user_id IS DISTINCT FROM resolved) THEN
        RAISE EXCEPTION 'resolve the authenticated local researcher before writing'
            USING ERRCODE = '42501';
    END IF;
    NEW.user_id := resolved;
    RETURN NEW;
END $$;

-- Nullable columns preserve immutable pre-0073 records. Existing functions and
-- CLI display-name fields stay compatible while new human writes are attributed.
DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY[
        'decisions', 'formalization_approvals', 'clarification_resolutions', 'research_targets',
        'research_intake_confirmations', 'research_goal_approvals', 'research_goal_selections',
        'research_condition_decisions', 'formal_item_approvals', 'formal_item_retirements',
        'formal_item_public_releases', 'verification_input_retirements', 'lean_statement_approvals',
        'lean_check_reviews', 'private_lean_model_approvals', 'research_progress_approvals',
        'research_progress_stops', 'research_review_policies', 'research_understanding',
        'job_cancellations', 'verifier_report_decisions', 'claim_reference_aliases', 'workbench_guidance'
    ]
    LOOP
        EXECUTE format('ALTER TABLE rm.%I ADD COLUMN user_id uuid REFERENCES rm.local_users(id)', relation);
        EXECUTE format('CREATE TRIGGER attribute_local_user BEFORE INSERT ON rm.%I '
                       'FOR EACH ROW EXECUTE FUNCTION rm.attribute_local_user()', relation);
    END LOOP;
END $$;

CREATE TABLE rm.local_review_requests (
    idempotency_key uuid PRIMARY KEY,
    decision_id uuid NOT NULL UNIQUE REFERENCES rm.decisions(id)
);
REVOKE ALL ON rm.local_review_requests FROM PUBLIC;
CREATE TRIGGER local_review_requests_append_only BEFORE UPDATE OR DELETE ON rm.local_review_requests
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.review_evidence_as(researcher uuid, evidence_id uuid, action text, reason text, key uuid)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE account record;
DECLARE existing decisions;
DECLARE reviewed uuid;
BEGIN
    SELECT * INTO account FROM current_local_user();
    IF NOT is_local_human_session() OR account.id IS NULL OR $1 IS DISTINCT FROM account.id THEN
        RAISE EXCEPTION 'resolve the authenticated local researcher before reviewing'
            USING ERRCODE = '42501';
    END IF;
    IF $2 IS NULL OR $3 IS NULL OR $3 NOT IN ('approve', 'return')
        OR $4 IS NULL OR length(btrim($4)) NOT BETWEEN 1 AND 2000 OR $5 IS NULL THEN
        RAISE EXCEPTION 'a review needs an evidence ID, action, reason and idempotency key'
            USING ERRCODE = '22023';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended($5::text, 9073));
    SELECT d.* INTO existing FROM local_review_requests r
    JOIN decisions d ON d.id = r.decision_id WHERE r.idempotency_key = $5;
    IF FOUND THEN
        IF ROW(existing.user_id, existing.evidence_id, existing.action, existing.reason)
           IS DISTINCT FROM ROW($1, $2, $3, $4) THEN
            RAISE EXCEPTION 'review idempotency key is already bound to a different request'
                USING ERRCODE = '23505';
        END IF;
        RETURN existing.id;
    END IF;

    -- Use the existing proof, freshness, and approval guards, including any
    -- review policy that applies to the canonical decision record.
    reviewed := review_evidence($2, $3, account.display_name, $4);
    INSERT INTO local_review_requests (idempotency_key, decision_id) VALUES ($5, reviewed);
    RETURN reviewed;
END $$;

REVOKE ALL ON FUNCTION rm.initialize_bound_local_user(), rm.is_local_human_session(),
    rm.attribute_local_user(), rm.current_local_user(), rm.review_evidence_as(uuid, uuid, text, text, uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.current_local_user(), rm.review_evidence_as(uuid, uuid, text, text, uuid) TO rm_human;
