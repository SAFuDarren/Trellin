-- New operations use a stable claim/stage key. Existing jobs remain addressable by ID.
ALTER TABLE rm.jobs ADD COLUMN operation_key text;
ALTER TABLE rm.jobs ADD CONSTRAINT jobs_operation_key_unique UNIQUE (claim_id, kind, operation_key);
ALTER TABLE rm.evidence ADD COLUMN verification_attempt_id uuid;
ALTER TABLE rm.evidence ADD COLUMN issuance_sha256 rm.sha256_hex UNIQUE;
ALTER TABLE rm.evidence ADD FOREIGN KEY (verification_attempt_id, claim_id, contract_sha256)
    REFERENCES rm.attempts(id, claim_id, contract_sha256);
CREATE UNIQUE INDEX verified_certificate_once ON rm.evidence (attempt_id, audit_attempt_id, verification_attempt_id)
    WHERE kind = 'exact_certificate' AND verdict = 'verified' AND verification_attempt_id IS NOT NULL;

CREATE FUNCTION rm.guard_candidate_job() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM attempts a WHERE a.id = (NEW.payload ->> 'candidate_attempt_id')::uuid
          AND a.claim_id = NEW.claim_id AND a.contract_sha256 = NEW.contract_sha256
          AND a.role = 'prove' AND a.finding = 'exact_proof'
          AND a.artifact_uri = NEW.payload #>> '{candidate,uri}'
          AND a.artifact_sha256 = NEW.payload #>> '{candidate,sha256}'
    ) THEN
        RAISE EXCEPTION '% job requires the exact candidate attempt and artifact', NEW.kind USING ERRCODE = '23514';
    END IF;
    IF NEW.kind = 'verify' AND NOT EXISTS (
        SELECT 1 FROM claims c WHERE c.id = NEW.claim_id AND c.sensitivity = 'public'
          AND c.domain = 'fixed-directed-max-flow' AND NEW.payload ->> 'checker_name' = 'exact-flow-cut'
          AND NEW.payload ->> 'checker_version' = 'v0a-2'
          AND NEW.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$'
    ) THEN
        RAISE EXCEPTION 'verify job requires the supported domain checker and its revision' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_candidate_job BEFORE INSERT ON rm.jobs
    FOR EACH ROW WHEN (NEW.kind IN ('audit', 'verify')) EXECUTE FUNCTION rm.guard_candidate_job();

CREATE FUNCTION rm.enqueue_job_internal(subject_id uuid, subject_hash text, job_type rm.job_kind,
                                         body jsonb, job_budget jsonb, job_priority integer, stage_key text)
RETURNS rm.jobs LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE queued jobs;
DECLARE subject_status claim_status;
BEGIN
    IF jsonb_typeof(job_budget -> 'max_attempts') IS DISTINCT FROM 'number'
        OR jsonb_typeof(job_budget -> 'wall_seconds') IS DISTINCT FROM 'number'
        OR (job_budget ->> 'max_attempts')::integer < 1
        OR (job_budget ->> 'wall_seconds')::integer < 1 THEN
        RAISE EXCEPTION 'job budget needs positive max_attempts and wall_seconds' USING ERRCODE = '22023';
    END IF;

    SELECT c.status INTO subject_status FROM claims c WHERE c.id = subject_id
        AND c.contract_sha256 = subject_hash FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'job requires the current frozen claim' USING ERRCODE = '23514';
    END IF;
    IF stage_key IS NOT NULL THEN
        SELECT * INTO queued FROM jobs j WHERE j.claim_id = subject_id
          AND j.kind = job_type AND j.operation_key = stage_key;
    END IF;
    IF queued.id IS NULL THEN
        IF subject_status <> 'open' THEN
            RAISE EXCEPTION 'new job requires an open claim' USING ERRCODE = '23514';
        END IF;
        INSERT INTO jobs (claim_id, contract_sha256, kind, payload, budget, priority, max_attempts, operation_key)
        VALUES (subject_id, subject_hash, job_type, body, job_budget, job_priority,
                (job_budget ->> 'max_attempts')::integer, stage_key)
        ON CONFLICT (claim_id, kind, operation_key) DO NOTHING RETURNING * INTO queued;
        IF NOT FOUND THEN
            SELECT * INTO STRICT queued FROM jobs j WHERE j.claim_id = subject_id
              AND j.kind = job_type AND j.operation_key = stage_key;
        END IF;
    END IF;
    IF queued.payload IS DISTINCT FROM body OR queued.budget IS DISTINCT FROM job_budget
        OR queued.priority IS DISTINCT FROM job_priority THEN
        RAISE EXCEPTION 'stage key is already bound to different inputs' USING ERRCODE = '23514';
    END IF;
    RETURN queued;
END $$;

CREATE OR REPLACE FUNCTION rm.enqueue_job(subject_id uuid, subject_hash text, job_type text,
                                          body jsonb, job_budget jsonb, job_priority integer DEFAULT 0)
RETURNS rm.jobs LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT enqueue_job_internal(subject_id, subject_hash, job_type::job_kind, body, job_budget, job_priority, NULL)
$$;
CREATE FUNCTION rm.enqueue_stage(subject_id uuid, subject_hash text, job_type text, body jsonb,
                                 job_budget jsonb, operation_key text)
RETURNS rm.jobs LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF nullif(btrim(operation_key), '') IS NULL OR length(operation_key) > 256 THEN
        RAISE EXCEPTION 'stage requires a nonempty operation key of at most 256 characters' USING ERRCODE = '22023';
    END IF;
    RETURN enqueue_job_internal(subject_id, subject_hash, job_type::job_kind, body, job_budget, 0, operation_key);
END $$;

CREATE FUNCTION rm.guard_verification_attempt() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.role = 'verify' THEN
        IF NOT pg_has_role(NEW.submitted_by, 'rm_verifier', 'USAGE') THEN
            RAISE EXCEPTION 'verification attempts require the verifier role' USING ERRCODE = '42501';
        END IF;
        IF NEW.finding NOT IN ('exact_proof', 'no_result') OR NEW.model_provider IS NOT NULL THEN
            RAISE EXCEPTION 'verification attempt needs a program result' USING ERRCODE = '23514';
        END IF;
    ELSIF pg_has_role(NEW.submitted_by, 'rm_verifier', 'USAGE') THEN
        RAISE EXCEPTION 'ordinary attempts require the agent role' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_verification_attempt BEFORE INSERT ON rm.attempts
    FOR EACH ROW EXECUTE FUNCTION rm.guard_verification_attempt();

-- The declared verification record must be a successful program attempt on the
-- same proof artifact. The pre-0002 flow runner omitted the candidate ID from
-- verify jobs, so legacy review additionally checks the exact historical path.
CREATE FUNCTION rm.has_bound_verification(receipt rm.evidence, legacy boolean)
RETURNS boolean LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM attempts proof
        JOIN attempts verified ON verified.claim_id = proof.claim_id
          AND verified.contract_sha256 = proof.contract_sha256
        JOIN jobs j ON j.id = verified.job_id
        WHERE proof.id = receipt.attempt_id AND proof.claim_id = receipt.claim_id
          AND proof.contract_sha256 = receipt.contract_sha256
          AND proof.role = 'prove' AND proof.finding = 'exact_proof'
          AND proof.artifact_uri = receipt.artifact_uri AND proof.artifact_sha256 = receipt.artifact_sha256
          AND verified.id = CASE
              WHEN NOT legacy THEN receipt.verification_attempt_id
              WHEN receipt.result ->> 'verification_attempt_id'
                  ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
              THEN (receipt.result ->> 'verification_attempt_id')::uuid
          END
          AND verified.role = 'verify' AND verified.finding = 'exact_proof' AND verified.model_provider IS NULL
          AND verified.artifact_uri = receipt.result #>> '{verification_artifact,uri}'
          AND verified.artifact_sha256 = receipt.result #>> '{verification_artifact,sha256}'
          AND j.payload #>> '{candidate,uri}' = proof.artifact_uri
          AND j.payload #>> '{candidate,sha256}' = proof.artifact_sha256
          AND CASE WHEN legacy THEN
              receipt.verifier_name = 'exact-flow-cut' AND receipt.verifier_version = 'v0a-1'
              AND proof.harness_version = 'v0a-1' AND verified.harness_version = 'v0a-1'
              AND EXISTS (SELECT 1 FROM claims c WHERE c.id = receipt.claim_id
                          AND c.domain = 'fixed-directed-max-flow' AND c.sensitivity = 'public')
              AND (j.payload ->> 'candidate_attempt_id' IS NULL OR j.payload ->> 'candidate_attempt_id' = proof.id::text)
          ELSE j.payload ->> 'candidate_attempt_id' = proof.id::text
              AND j.payload ->> 'checker_name' = receipt.verifier_name
              AND j.payload ->> 'checker_version' = receipt.verifier_version
              AND j.payload ->> 'checker_revision' = receipt.code_revision END
    )
$$;

CREATE FUNCTION rm.guard_evidence_grade() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.kind = 'lean' OR (NEW.verdict = 'verified' AND NEW.kind IN ('audited_proof', 'counterexample')) THEN
        RAISE EXCEPTION '% verification is not enabled', NEW.kind USING ERRCODE = '23514';
    END IF;
    IF NEW.verdict = 'verified' AND NEW.kind = 'exact_certificate' THEN
        IF NOT EXISTS (SELECT 1 FROM attempts a WHERE a.id = NEW.attempt_id
                       AND a.role = 'prove' AND a.finding = 'exact_proof') THEN
            RAISE EXCEPTION 'proof-grade evidence requires an exact proof candidate' USING ERRCODE = '23514';
        END IF;
        IF NEW.audit_mode <> 'cross_family' OR NOT has_bound_verification(NEW, false) THEN
            RAISE EXCEPTION 'exact evidence requires cross-family audit and bound verification' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM attempts auditor JOIN jobs j ON j.id = auditor.job_id
            JOIN attempts proof ON proof.id = NEW.attempt_id
            WHERE auditor.id = NEW.audit_attempt_id
              AND j.payload #>> '{candidate,uri}' = NEW.artifact_uri
              AND j.payload #>> '{candidate,sha256}' = NEW.artifact_sha256
              AND nullif(btrim(proof.model_family), '') IS NOT NULL
              AND nullif(btrim(auditor.model_family), '') IS NOT NULL
              AND (NEW.audit_mode <> 'cross_family'
                   OR lower(btrim(proof.model_family)) <> lower(btrim(auditor.model_family)))
        ) THEN
            RAISE EXCEPTION 'evidence audit must identify the submitted proof artifact' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_evidence_grade BEFORE INSERT ON rm.evidence
    FOR EACH ROW EXECUTE FUNCTION rm.guard_evidence_grade();

CREATE OR REPLACE FUNCTION rm.issue_evidence(body jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt_id uuid;
DECLARE issuance_hash text := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');
BEGIN
    INSERT INTO evidence (claim_id, contract_sha256, attempt_id, audit_attempt_id, kind, verdict, diff_status,
        audit_mode, artifact_uri, artifact_sha256, verifier_name, verifier_version, code_revision,
        environment_sha256, result, verification_attempt_id, issuance_sha256)
    VALUES ((body #>> '{subject,claim_id}')::uuid, body #>> '{subject,contract_sha256}',
        (body ->> 'attempt_id')::uuid, (body ->> 'audit_attempt_id')::uuid, (body ->> 'kind')::evidence_kind,
        (body ->> 'verdict')::verdict, (body ->> 'diff_status')::scope_status, body ->> 'audit_mode',
        body #>> '{artifact,uri}', body #>> '{artifact,sha256}', body #>> '{verifier,name}',
        body #>> '{verifier,version}', body #>> '{provenance,code_revision}',
        body #>> '{provenance,environment_sha256}', body -> 'result',
        (body #>> '{result,verification_attempt_id}')::uuid, issuance_hash)
    ON CONFLICT (issuance_sha256) DO NOTHING RETURNING id INTO receipt_id;
    IF NOT FOUND THEN
        SELECT id INTO STRICT receipt_id FROM evidence WHERE issuance_sha256 = issuance_hash;
    END IF;
    RETURN receipt_id;
END $$;

CREATE FUNCTION rm.can_promote_receipt(receipt rm.evidence) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT receipt.issuance_sha256 IS NOT NULL OR (
        receipt.kind = 'exact_certificate' AND receipt.audit_mode = 'cross_family'
        AND has_bound_verification(receipt, true)
        AND EXISTS (
            SELECT 1 FROM attempts a JOIN jobs j ON j.id = a.job_id
            WHERE a.id = receipt.audit_attempt_id AND a.harness_version = 'v0a-1'
              AND j.payload #>> '{candidate,uri}' = receipt.artifact_uri
              AND j.payload #>> '{candidate,sha256}' = receipt.artifact_sha256
        ))
$$;

CREATE OR REPLACE FUNCTION rm.review_evidence(evidence_id uuid, action text, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt evidence;
DECLARE decision_id uuid;
BEGIN
    IF action IS NULL OR action NOT IN ('approve', 'return') THEN
        RAISE EXCEPTION 'review requires approve or return' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT receipt FROM evidence WHERE id = review_evidence.evidence_id;
    -- Write a new MVCC row version even though status is unchanged. A verifier
    -- on an older snapshot cannot then promote on a superseded human decision.
    UPDATE claims SET status = status WHERE id = receipt.claim_id AND status = 'open';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'review requires an open claim' USING ERRCODE = '23514';
    END IF;
    IF action = 'approve' AND (receipt.verdict <> 'verified' OR receipt.diff_status <> 'exact'
        OR NOT can_promote_receipt(receipt)) THEN
        RAISE EXCEPTION 'approval requires a verified receipt with a supported evidence path' USING ERRCODE = '23514';
    END IF;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (receipt.claim_id, receipt.contract_sha256, receipt.id, action, 'human', actor_ref, reason)
    RETURNING id INTO decision_id;
    RETURN decision_id;
END $$;

CREATE OR REPLACE FUNCTION rm.promote_claim(evidence_id uuid, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt evidence;
BEGIN
    SELECT * INTO STRICT receipt FROM evidence WHERE id = promote_claim.evidence_id;
    IF NOT can_promote_receipt(receipt) THEN
        RAISE EXCEPTION 'promotion requires a supported evidence path' USING ERRCODE = '23514';
    END IF;
    UPDATE claims SET status = CASE WHEN receipt.kind = 'counterexample' THEN 'refuted'::claim_status
                                    ELSE 'proved'::claim_status END,
        decisive_evidence_id = receipt.id WHERE id = receipt.claim_id AND status = 'open';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promotion requires an open claim' USING ERRCODE = '23514';
    END IF;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (receipt.claim_id, receipt.contract_sha256, receipt.id, 'promote', 'program', session_user, reason);
    RETURN receipt.claim_id;
END $$;

CREATE OR REPLACE VIEW rm.shared_evidence WITH (security_barrier = true) AS
    SELECT e.* FROM rm.evidence e JOIN rm.claims c ON c.id = e.claim_id
    WHERE e.verdict = 'verified' AND e.diff_status = 'exact' AND c.sensitivity = 'public'
      AND e.issuance_sha256 IS NOT NULL
      AND (SELECT d.action FROM rm.decisions d WHERE d.evidence_id = e.id
           AND d.action IN ('approve', 'return') ORDER BY d.sequence_no DESC LIMIT 1) IS DISTINCT FROM 'return';

REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA rm FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.submit_attempt(uuid, text, integer, jsonb) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.enqueue_stage(uuid, text, text, jsonb, jsonb, text) TO rm_scheduler;
