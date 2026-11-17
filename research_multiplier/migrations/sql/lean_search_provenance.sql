CREATE TABLE rm.lean_search_provenance (
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    trace_sha256 rm.sha256_hex NOT NULL,
    proposal_attempt_ids uuid[] NOT NULL CHECK (cardinality(proposal_attempt_ids) BETWEEN 1 AND 8),
    PRIMARY KEY (evidence_id, trace_sha256)
);
CREATE TRIGGER lean_search_provenance_append_only BEFORE UPDATE OR DELETE ON rm.lean_search_provenance
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.record_lean_search_provenance(evidence_id uuid, trace_sha256 rm.sha256_hex,
    proposal_attempt_ids uuid[]) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
BEGIN
    SELECT statement.* INTO STRICT anchor FROM evidence e
    JOIN lean_checks checked ON checked.id = e.lean_check_id
    JOIN lean_statements statement ON statement.id = checked.statement_id
    WHERE e.id = $1 AND e.verdict = 'verified' AND e.diff_status = 'exact';
    IF (SELECT count(DISTINCT a.id)
        FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = ANY($3) AND a.claim_id = anchor.claim_id
          AND a.contract_sha256 = anchor.contract_sha256 AND a.role = 'prove'
          AND j.payload -> 'lean_search' = 'true'::jsonb
          AND j.payload ->> 'author_statement_id' = anchor.id::text
          AND j.payload ->> 'author_statement_sha256' = anchor.statement_sha256) <> cardinality($3) THEN
        RAISE EXCEPTION 'search provenance must reference distinct proposal attempts for the same frozen statement'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_search_provenance VALUES ($1, $2, $3)
    ON CONFLICT DO NOTHING;
END $$;

REVOKE ALL ON FUNCTION rm.record_lean_search_provenance(uuid,rm.sha256_hex,uuid[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_lean_search_provenance(uuid,rm.sha256_hex,uuid[]) TO rm_verifier;
GRANT SELECT ON rm.lean_search_provenance TO rm_human;

INSERT INTO rm.harness_components(name, version, layer, purpose, metric, retirement_condition, enabled)
VALUES ('bounded-lean-search', '1', 'scaffolding',
        'Let a model adapt subsequent proposals to checked local lemmas and rejected proof terms',
        '{"measure":"matched_expected_outcomes_per_model_call","gates":["scope_drift","invalid_proof"]}',
        'A single proposal matches held-out outcomes and trust gates with less model work.', true)
ON CONFLICT DO NOTHING;
