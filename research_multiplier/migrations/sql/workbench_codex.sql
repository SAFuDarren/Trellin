-- Releases are per note and are bound to its reviewed statement.
ALTER TABLE rm.workbench_guidance
    ADD COLUMN audience text NOT NULL DEFAULT 'local',
    ADD COLUMN released_at timestamptz,
    ADD CONSTRAINT workbench_guidance_release_scope CHECK (
        (audience = 'local' AND released_at IS NULL)
        OR (audience = 'codex' AND released_at IS NOT NULL)
    );

CREATE FUNCTION rm.workbench_codex_available(anchor_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM lean_statement_status s JOIN claims c ON c.id = s.claim_id
        WHERE s.id = $1 AND c.sensitivity = 'public' AND s."current" AND s.approved AND s.check_ready
            AND lean_model_packet_is_public(s.packet_id)
            AND EXISTS (
                SELECT FROM research_progress_policies p
                JOIN research_progress_approvals a ON a.policy_id = p.id
                WHERE p.packet_id = s.packet_id AND p.claim_id = s.claim_id
                    AND p.contract_sha256 = s.contract_sha256
                    AND p.policy #>> '{lean,statement_id}' = s.id::text
                    AND p.policy #>> '{lean,statement_sha256}' = s.statement_sha256
                    AND p.policy #> '{lean,private_model}' IS NULL
                    AND (p.policy #>> '{lean,model_rounds}')::integer > 0
                    AND NOT EXISTS (SELECT FROM research_progress_stops stop WHERE stop.policy_id = p.id)
            )
    )
$$;

CREATE FUNCTION rm.add_workbench_guidance(target_id uuid, subject_id uuid, anchor_id uuid,
                                          node text, note text, audience text, confirm_release boolean)
RETURNS rm.workbench_guidance
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved workbench_guidance;
BEGIN
    IF NOT coalesce(($6 = 'local' AND NOT $7) OR ($6 = 'codex' AND $7), false) THEN
        RAISE EXCEPTION 'guidance release requires explicit authorization for the selected audience' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO saved FROM workbench_guidance WHERE id = $1;
    IF NOT FOUND THEN
        IF NOT EXISTS (
            SELECT FROM lean_statement_status s JOIN claims c ON c.id = s.claim_id
            WHERE s.id = $3 AND c.id = $2 AND s."current" AND s.approved AND s.check_ready
                AND (($6 = 'local' AND c.sensitivity = 'restricted')
                    OR ($6 = 'codex' AND c.sensitivity = 'public' AND lean_model_packet_is_public(s.packet_id)))
        ) THEN
            RAISE EXCEPTION 'guidance requires a reviewed statement and a permitted audience' USING ERRCODE = '23514';
        END IF;
        INSERT INTO workbench_guidance (id, claim_id, statement_id, node_id, text, audience, released_at)
        VALUES ($1, $2, $3, $4, $5, $6, CASE WHEN $7 THEN clock_timestamp() END)
        ON CONFLICT (id) DO NOTHING;
        SELECT * INTO STRICT saved FROM workbench_guidance WHERE id = $1;
    END IF;
    IF (saved.claim_id, saved.statement_id, saved.node_id, saved.text, saved.audience)
            IS DISTINCT FROM ($2, $3, $4, $5, $6) THEN
        RAISE EXCEPTION 'guidance request already identifies different content' USING ERRCODE = '23514';
    END IF;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.add_workbench_guidance(target_id uuid, subject_id uuid, anchor_id uuid, node text, note text)
RETURNS rm.workbench_guidance
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT add_workbench_guidance($1, $2, $3, $4, $5, 'local', false)
$$;

CREATE FUNCTION rm.pending_workbench_guidance(anchor_id uuid, audience text)
RETURNS TABLE (id uuid, node_id text, text text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT n.id, n.node_id, n.text
    FROM workbench_guidance n
    JOIN lean_statement_status s ON s.id = n.statement_id AND s.claim_id = n.claim_id
    JOIN claims c ON c.id = n.claim_id
    WHERE n.statement_id = $1 AND n.audience = $2 AND s."current" AND s.check_ready AND s.approved
        AND (($2 = 'local' AND c.sensitivity = 'restricted')
            OR ($2 = 'codex' AND n.released_at IS NOT NULL AND workbench_codex_available($1)))
        AND NOT EXISTS (SELECT FROM workbench_guidance_submissions g WHERE g.guidance_id = n.id)
    ORDER BY n.created_at, n.id
    LIMIT 3
$$;

CREATE OR REPLACE FUNCTION rm.pending_workbench_guidance(anchor_id uuid)
RETURNS TABLE (id uuid, node_id text, text text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT * FROM pending_workbench_guidance($1, 'local')
$$;

CREATE FUNCTION rm.released_workbench_guidance(anchor_id uuid, notes uuid[])
RETURNS TABLE (id uuid, node_id text, text text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT n.id, n.node_id, n.text
    FROM unnest($2) WITH ORDINALITY AS selected(id, position)
    JOIN workbench_guidance n ON n.id = selected.id AND n.statement_id = $1
    WHERE cardinality($2) BETWEEN 1 AND 3 AND n.audience = 'codex' AND n.released_at IS NOT NULL
        AND workbench_codex_available($1)
    ORDER BY selected.position
$$;

CREATE OR REPLACE FUNCTION rm.record_workbench_guidance_submission(anchor_id uuid, notes uuid[], model_attempt uuid)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE inserted bigint;
BEGIN
    IF cardinality($2) NOT BETWEEN 1 AND 3 OR NOT EXISTS (
        SELECT FROM attempts a JOIN jobs j ON j.id = a.job_id
        JOIN lean_statements s ON s.id = $1 AND s.claim_id = j.claim_id
        JOIN claims c ON c.id = j.claim_id
        WHERE a.id = $3 AND a.role = 'prove' AND a.model_provider IS NOT NULL
            AND j.payload ->> 'author_statement_id' = s.id::text
            AND j.payload ->> 'author_statement_sha256' = s.statement_sha256
            AND j.payload -> 'workbench_guidance_ids' @> to_jsonb($2)
            AND (SELECT count(DISTINCT n.id) FROM workbench_guidance n
                 WHERE n.id = ANY($2) AND n.statement_id = s.id AND n.claim_id = c.id
                    AND ((n.audience = 'local' AND c.sensitivity = 'restricted' AND j.payload ? 'private_model')
                        OR (n.audience = 'codex' AND n.released_at <= a.submitted_at AND c.sensitivity = 'public'
                            AND NOT j.payload ? 'private_model' AND a.model_provider = 'codex-cli'
                            AND a.model_parameters ->> 'requested_model' = j.payload ->> 'workbench_guidance_model')))
                = cardinality($2)
            AND EXISTS (
                SELECT FROM research_progress_policies p
                JOIN research_progress_approvals approval ON approval.policy_id = p.id
                WHERE p.packet_id = s.packet_id AND p.claim_id = j.claim_id AND p.contract_sha256 = j.contract_sha256
                    AND approval.approved_at <= a.submitted_at
                    AND p.policy #>> '{lean,statement_id}' = s.id::text
                    AND p.policy #>> '{lean,statement_sha256}' = s.statement_sha256
                    AND (p.policy #>> '{lean,model_rounds}')::integer > 0
                    AND p.policy #> '{lean,private_model}' IS NOT DISTINCT FROM j.payload -> 'private_model'
            )
    ) THEN
        RAISE EXCEPTION 'guidance requires a recorded authorized model attempt for the same statement' USING ERRCODE = '23514';
    END IF;
    INSERT INTO workbench_guidance_submissions (guidance_id, attempt_id)
    SELECT id, $3 FROM workbench_guidance WHERE statement_id = $1 AND id = ANY($2)
    ON CONFLICT (guidance_id) DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted;
END $$;

REVOKE ALL ON FUNCTION rm.add_workbench_guidance(uuid, uuid, uuid, text, text, text, boolean),
    rm.workbench_codex_available(uuid), rm.pending_workbench_guidance(uuid, text),
    rm.released_workbench_guidance(uuid, uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.add_workbench_guidance(uuid, uuid, uuid, text, text, text, boolean) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.workbench_codex_available(uuid) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.pending_workbench_guidance(uuid, text),
    rm.released_workbench_guidance(uuid, uuid[]) TO rm_verifier;
