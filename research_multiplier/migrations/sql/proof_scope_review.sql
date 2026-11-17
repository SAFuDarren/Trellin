CREATE TABLE rm.proof_scope_extractions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    attempt_id uuid NOT NULL REFERENCES rm.attempts(id),
    proof_sha256 text NOT NULL CHECK (proof_sha256 ~ '^[0-9a-f]{64}$'),
    model_family text NOT NULL CHECK (btrim(model_family) <> ''),
    report_sha256 text NOT NULL CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (evidence_id, report_sha256)
);
REVOKE ALL ON rm.proof_scope_extractions FROM PUBLIC;
GRANT SELECT ON rm.proof_scope_extractions TO rm_human, rm_verifier;

CREATE FUNCTION rm.record_scope_extraction(target uuid, author_attempt uuid, proof_digest text,
    family text, report_digest text)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE extraction_id uuid;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM evidence e JOIN claims c ON (c.id, c.contract_sha256) = (e.claim_id, e.contract_sha256)
        JOIN attempts a ON a.id = e.attempt_id
        WHERE e.id = target AND a.id = author_attempt AND a.artifact_sha256 = proof_digest
          AND c.sensitivity = 'public' AND lower(a.model_family) IS DISTINCT FROM lower(btrim(family))
    ) THEN
        RAISE EXCEPTION 'blind extraction requires the exact public proof and independent model family'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO proof_scope_extractions (evidence_id, attempt_id, proof_sha256, model_family, report_sha256)
    VALUES (target, author_attempt, proof_digest, lower(btrim(family)), report_digest)
    ON CONFLICT (evidence_id, report_sha256) DO NOTHING RETURNING id INTO extraction_id;
    IF extraction_id IS NULL THEN
        SELECT id INTO extraction_id FROM proof_scope_extractions
        WHERE evidence_id = target AND report_sha256 = report_digest
          AND proof_sha256 = proof_digest AND model_family = lower(btrim(family));
        IF extraction_id IS NULL THEN
            RAISE EXCEPTION 'the report hash is bound to a different extraction' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN extraction_id;
END $$;
REVOKE ALL ON FUNCTION rm.record_scope_extraction(uuid,uuid,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_scope_extraction(uuid,uuid,text,text,text) TO rm_verifier;
