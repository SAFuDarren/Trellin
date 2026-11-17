CREATE TABLE rm.lean_lemma_model_proposals (
    lemma_id uuid NOT NULL REFERENCES rm.local_lean_lemmas(id),
    proposal_attempt_id uuid NOT NULL REFERENCES rm.attempts(id),
    PRIMARY KEY (lemma_id, proposal_attempt_id)
);
CREATE TRIGGER lean_lemma_model_proposals_append_only BEFORE UPDATE OR DELETE ON rm.lean_lemma_model_proposals
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.record_lean_lemma_proposal(lemma_id uuid, attempt_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM local_lean_lemmas lemma
        JOIN lean_statements anchor ON anchor.id = lemma.statement_id
        JOIN attempts a ON (a.claim_id, a.contract_sha256) = (anchor.claim_id, anchor.contract_sha256)
        JOIN jobs j ON j.id = a.job_id
        WHERE lemma.id = $1 AND a.id = $2 AND a.role = 'prove' AND a.finding = 'numeric_observation'
          AND j.payload -> 'lean_search' = 'true'::jsonb
          AND j.payload ->> 'author_statement_id' = anchor.id::text
          AND j.payload ->> 'author_statement_sha256' = anchor.statement_sha256
    ) THEN
        RAISE EXCEPTION 'lemma provenance requires a model proposal for the same frozen Lean scope'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_lemma_model_proposals VALUES ($1, $2) ON CONFLICT DO NOTHING;
END $$;
REVOKE ALL ON FUNCTION rm.record_lean_lemma_proposal(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_lean_lemma_proposal(uuid,uuid) TO rm_verifier;

CREATE FUNCTION rm.lean_dependency_proposals(check_id uuid) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE actual(lemma_id) AS (
        SELECT used.lemma_id FROM verification_local_lemma_use used WHERE used.check_id = $1
        UNION
        SELECT dependency.prerequisite_id FROM actual
        JOIN local_lean_lemma_dependencies dependency ON dependency.lemma_id = actual.lemma_id
    )
    SELECT DISTINCT proposal.proposal_attempt_id FROM actual
    JOIN lean_lemma_model_proposals proposal USING (lemma_id)
$$;
REVOKE ALL ON FUNCTION rm.lean_dependency_proposals(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_dependency_proposals(uuid) TO rm_human;
