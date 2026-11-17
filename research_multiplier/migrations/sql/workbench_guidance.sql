CREATE TABLE rm.workbench_guidance (
    id uuid PRIMARY KEY,
    claim_id uuid NOT NULL REFERENCES rm.claims(id),
    statement_id uuid NOT NULL REFERENCES rm.lean_statements(id),
    node_id text NOT NULL CHECK (length(node_id) BETWEEN 1 AND 128),
    text text NOT NULL CHECK (length(btrim(text)) BETWEEN 1 AND 1500),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX workbench_guidance_stmt ON rm.workbench_guidance (statement_id, created_at, id);
CREATE INDEX workbench_guidance_claim ON rm.workbench_guidance (claim_id, created_at DESC, id);
CREATE TABLE rm.workbench_guidance_submissions (
    guidance_id uuid PRIMARY KEY REFERENCES rm.workbench_guidance(id),
    attempt_id uuid NOT NULL REFERENCES rm.attempts(id),
    submitted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER workbench_guidance_append_only BEFORE UPDATE OR DELETE ON rm.workbench_guidance
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER workbench_guidance_submissions_append_only BEFORE UPDATE OR DELETE ON rm.workbench_guidance_submissions
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
GRANT SELECT ON rm.workbench_guidance, rm.workbench_guidance_submissions TO rm_human;

CREATE FUNCTION rm.add_workbench_guidance(target_id uuid, subject_id uuid, anchor_id uuid, node text, note text)
RETURNS rm.workbench_guidance
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved workbench_guidance;
BEGIN
    SELECT * INTO saved FROM workbench_guidance WHERE id = $1;
    IF NOT FOUND THEN
        IF NOT EXISTS (
            SELECT FROM lean_statement_status s JOIN claims c ON c.id = s.claim_id
            WHERE s.id = $3 AND c.id = $2 AND c.sensitivity = 'restricted'
                AND s."current" AND s.approved AND s.check_ready
        ) THEN
            RAISE EXCEPTION 'guidance requires a current approved Lean statement' USING ERRCODE = '23514';
        END IF;
        INSERT INTO workbench_guidance (id, claim_id, statement_id, node_id, text)
        VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING;
        SELECT * INTO STRICT saved FROM workbench_guidance WHERE id = $1;
    END IF;
    IF (saved.claim_id, saved.statement_id, saved.node_id, saved.text) IS DISTINCT FROM ($2, $3, $4, $5) THEN
        RAISE EXCEPTION 'guidance request already identifies different content' USING ERRCODE = '23514';
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.pending_workbench_guidance(anchor_id uuid)
RETURNS TABLE (id uuid, node_id text, text text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT n.id, n.node_id, n.text
    FROM workbench_guidance n
    JOIN lean_statement_status s ON s.id = n.statement_id AND s."current" AND s.check_ready AND s.approved
    WHERE n.statement_id = $1
        AND NOT EXISTS (SELECT FROM workbench_guidance_submissions g WHERE g.guidance_id = n.id)
    ORDER BY n.created_at, n.id
    LIMIT 3
$$;

CREATE FUNCTION rm.record_workbench_guidance_submission(anchor_id uuid, notes uuid[], model_attempt uuid)
RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE inserted bigint;
BEGIN
    IF cardinality($2) NOT BETWEEN 1 AND 3 OR NOT EXISTS (
        SELECT FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = $3 AND a.role = 'prove' AND a.model_provider IS NOT NULL
            AND j.payload ->> 'author_statement_id' = $1::text
            AND j.payload ? 'private_model'
            AND j.payload -> 'workbench_guidance_ids' @> to_jsonb($2)
            AND EXISTS (
                SELECT FROM research_progress_status p WHERE p.claim_id = j.claim_id
                    AND p.contract_sha256 = j.contract_sha256 AND p.approved_at IS NOT NULL
                    AND p.policy #>> '{lean,statement_id}' = $1::text
                    AND p.policy #>> '{lean,statement_sha256}' = j.payload ->> 'author_statement_sha256'
                    AND p.policy #> '{lean,private_model}' = j.payload -> 'private_model'
            )
    ) OR EXISTS (
        SELECT FROM unnest($2) AS expected(id)
        WHERE NOT EXISTS (SELECT FROM workbench_guidance n WHERE n.id = expected.id AND n.statement_id = $1)
    ) THEN
        RAISE EXCEPTION 'guidance requires a recorded local model attempt for the same statement' USING ERRCODE = '23514';
    END IF;
    INSERT INTO workbench_guidance_submissions (guidance_id, attempt_id)
    SELECT id, $3 FROM workbench_guidance WHERE statement_id = $1 AND id = ANY($2)
    ON CONFLICT (guidance_id) DO NOTHING;
    GET DIAGNOSTICS inserted = ROW_COUNT;
    RETURN inserted;
END $$;

REVOKE ALL ON FUNCTION rm.add_workbench_guidance(uuid, uuid, uuid, text, text),
    rm.pending_workbench_guidance(uuid), rm.record_workbench_guidance_submission(uuid, uuid[], uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.add_workbench_guidance(uuid, uuid, uuid, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.pending_workbench_guidance(uuid),
    rm.record_workbench_guidance_submission(uuid, uuid[], uuid) TO rm_verifier;
