CREATE TABLE rm.production_checkers (
    domain text NOT NULL CHECK (btrim(domain) <> ''),
    checker_name text NOT NULL CHECK (btrim(checker_name) <> ''),
    checker_version text NOT NULL CHECK (btrim(checker_version) <> ''),
    supports_refutation boolean NOT NULL,
    requires_formalization boolean NOT NULL,
    PRIMARY KEY (domain, checker_name, checker_version)
);

INSERT INTO rm.production_checkers
    (domain, checker_name, checker_version, supports_refutation, requires_formalization)
VALUES ('fixed-directed-max-flow', 'exact-flow-cut', 'v0a-2', true, false),
       ('graph-coloring', 'exact-graph-coloring', '1', true, true);

CREATE UNIQUE INDEX deterministic_refutation_once ON rm.evidence (verification_attempt_id)
    WHERE kind = 'counterexample' AND verdict = 'verified';

CREATE FUNCTION rm.registered_production_checker(subject_id uuid, checker_name text,
    checker_version text, result_finding rm.finding_type)
RETURNS boolean LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM claims c JOIN production_checkers allowed ON allowed.domain = c.domain
        WHERE c.id = $1 AND c.sensitivity = 'public'
          AND allowed.checker_name = $2 AND allowed.checker_version = $3
          AND ($4 = 'exact_proof' OR ($4 = 'counterexample' AND allowed.supports_refutation))
          AND (NOT allowed.requires_formalization AND $4 = 'exact_proof' OR EXISTS (
              SELECT 1 FROM formalization_status p WHERE p.claim_id = c.id AND p.proof_ready
          ))
    )
$$;
REVOKE EXECUTE ON FUNCTION rm.registered_production_checker(uuid, text, text, rm.finding_type) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.guard_candidate_job() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE author attempts;
BEGIN
    IF NEW.kind = 'verify' AND NEW.payload ? 'lean_statement_id' THEN
        PERFORM require_dynamic_lean_job(NEW);
        RETURN NEW;
    END IF;
    SELECT * INTO author FROM attempts a WHERE a.id = (NEW.payload ->> 'candidate_attempt_id')::uuid
      AND a.claim_id = NEW.claim_id AND a.contract_sha256 = NEW.contract_sha256 AND a.role = 'prove'
      AND a.artifact_uri = NEW.payload #>> '{candidate,uri}'
      AND a.artifact_sha256 = NEW.payload #>> '{candidate,sha256}';
    IF NOT FOUND OR author.finding NOT IN ('exact_proof', 'counterexample')
        OR (NEW.kind = 'audit' AND author.finding <> 'exact_proof') THEN
        RAISE EXCEPTION '% job requires a matching proof or refutation candidate', NEW.kind USING ERRCODE = '23514';
    END IF;
    IF NEW.kind = 'verify' AND (
        NOT registered_production_checker(NEW.claim_id, NEW.payload ->> 'checker_name',
            NEW.payload ->> 'checker_version', author.finding)
        OR NOT coalesce(NEW.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$', false)
    ) THEN
        RAISE EXCEPTION 'verify job requires the supported domain checker and its revision' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_verification_attempt() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.role = 'verify' THEN
        IF NOT pg_has_role(NEW.submitted_by, 'rm_verifier', 'USAGE') THEN
            RAISE EXCEPTION 'verification attempts require the verifier role' USING ERRCODE = '42501';
        END IF;
        IF NEW.finding NOT IN ('exact_proof', 'counterexample', 'no_result') OR NEW.model_provider IS NOT NULL THEN
            RAISE EXCEPTION 'verification attempt needs a program result' USING ERRCODE = '23514';
        END IF;
        IF NEW.finding = 'counterexample' AND NOT EXISTS (
            SELECT 1 FROM jobs j JOIN attempts a ON a.id::text = j.payload ->> 'candidate_attempt_id'
            WHERE j.id = NEW.job_id AND a.finding = 'counterexample' AND a.role = 'prove'
            AND registered_production_checker(j.claim_id, j.payload ->> 'checker_name',
                j.payload ->> 'checker_version', a.finding)
        ) THEN
            RAISE EXCEPTION 'refutation verification requires a registered refutation job' USING ERRCODE = '23514';
        END IF;
    ELSIF pg_has_role(NEW.submitted_by, 'rm_verifier', 'USAGE') THEN
        RAISE EXCEPTION 'ordinary attempts require the agent role' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION rm.has_bound_verification(receipt rm.evidence, legacy boolean)
RETURNS boolean LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM attempts proof
        JOIN attempts verified ON verified.claim_id = proof.claim_id
          AND verified.contract_sha256 = proof.contract_sha256
        JOIN jobs j ON j.id = verified.job_id
        WHERE proof.id = receipt.attempt_id AND proof.claim_id = receipt.claim_id
          AND proof.contract_sha256 = receipt.contract_sha256
          AND proof.role = 'prove' AND (
              (receipt.kind = 'exact_certificate' AND proof.finding = 'exact_proof')
              OR (NOT legacy AND receipt.kind = 'counterexample' AND proof.finding = 'counterexample')
          )
          AND proof.artifact_uri = receipt.artifact_uri AND proof.artifact_sha256 = receipt.artifact_sha256
          AND verified.id = CASE
              WHEN NOT legacy THEN receipt.verification_attempt_id
              WHEN receipt.result ->> 'verification_attempt_id'
                  ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
              THEN (receipt.result ->> 'verification_attempt_id')::uuid
          END
          AND verified.role = 'verify' AND verified.finding = proof.finding AND verified.model_provider IS NULL
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
              AND j.payload ->> 'checker_revision' = receipt.code_revision
              AND registered_production_checker(receipt.claim_id, receipt.verifier_name,
                  receipt.verifier_version, proof.finding) END
    )
$$;

CREATE OR REPLACE FUNCTION rm.guard_evidence_grade() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_formalization_ready(NEW.claim_id);
    PERFORM lock_live_foundation();
    IF EXISTS (SELECT 1 FROM verification_input_retirements r
        WHERE (r.input_kind = 'verifier' AND r.sha256 = NEW.code_revision)
           OR (r.input_kind = 'environment' AND r.sha256 = NEW.environment_sha256)
           OR (r.input_kind = 'source' AND r.sha256 = NEW.artifact_sha256)) THEN
        RAISE EXCEPTION 'verification input requires recheck' USING ERRCODE = '23514';
    END IF;
    IF NEW.kind = 'lean' THEN
        IF NEW.verdict <> 'verified' OR NEW.diff_status <> 'exact' OR NEW.audit_mode <> 'none' OR NOT EXISTS (
            SELECT 1 FROM lean_checks c JOIN lean_statement_status s ON s.id = c.statement_id
            LEFT JOIN lean_execution_links execution ON execution.check_id = c.id
            WHERE c.id = NEW.lean_check_id AND s.claim_id = NEW.claim_id
              AND s.contract_sha256 = NEW.contract_sha256 AND s.check_ready
              AND c.body -> 'accepted' = 'true'::jsonb AND c.body -> 'scope_matches' = 'true'::jsonb
              AND c.body #>> '{candidate,uri}' = NEW.artifact_uri AND c.body #>> '{candidate,sha256}' = NEW.artifact_sha256
              AND (execution.target = 'goal' OR execution.check_id IS NULL)
              AND execution.verification_attempt_id IS NOT DISTINCT FROM NEW.verification_attempt_id
              AND NOT EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = c.id)
        ) THEN
            RAISE EXCEPTION 'Lean evidence must refer to an accepted full-goal check'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.verdict = 'verified' AND NEW.kind = 'audited_proof' THEN
        RAISE EXCEPTION '% verification is not enabled', NEW.kind USING ERRCODE = '23514';
    END IF;
    IF NEW.verdict = 'verified' AND NEW.kind IN ('exact_certificate', 'counterexample') THEN
        IF NEW.audit_mode NOT IN ('none', 'cross_family')
            OR (NEW.kind = 'counterexample' AND NEW.audit_mode <> 'none') OR NOT has_bound_verification(NEW, false) THEN
            RAISE EXCEPTION 'exact evidence requires a successful deterministic check bound to its candidate'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.diff_status <> 'exact' THEN
            RAISE EXCEPTION 'exact evidence must match the frozen statement' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM formalization_packets p JOIN packet_basis_items b ON b.packet_id = p.id
            JOIN foundation_stale stale ON stale.kind = 'item' AND stale.id = b.version_id
            WHERE p.claim_id = NEW.claim_id
        ) THEN
            RAISE EXCEPTION 'the frozen premise basis requires recheck' USING ERRCODE = '23514';
        END IF;
        IF NEW.audit_mode = 'cross_family' AND NOT EXISTS (
            SELECT 1 FROM attempts auditor JOIN jobs j ON j.id = auditor.job_id
            JOIN attempts proof ON proof.id = NEW.attempt_id
            WHERE auditor.id = NEW.audit_attempt_id
              AND j.payload #>> '{candidate,uri}' = NEW.artifact_uri
              AND j.payload #>> '{candidate,sha256}' = NEW.artifact_sha256
              AND nullif(btrim(proof.model_family), '') IS NOT NULL
              AND nullif(btrim(auditor.model_family), '') IS NOT NULL
              AND lower(btrim(proof.model_family)) <> lower(btrim(auditor.model_family))
        ) THEN
            RAISE EXCEPTION 'evidence audit must identify the submitted proof artifact' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION rm.capture_evidence_basis() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.kind IN ('exact_certificate', 'counterexample') AND NEW.verdict = 'verified' THEN
        INSERT INTO evidence_basis_use (evidence_id, version_id)
            SELECT NEW.id, b.version_id FROM formalization_status p JOIN packet_basis_items b ON b.packet_id = p.id
            WHERE p.claim_id = NEW.claim_id AND p.proof_ready ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE VIEW rm.verified_derivations WITH (security_barrier = true) AS
    SELECT d.* FROM rm.flow_derivation_records d
    JOIN rm.formalization_status p ON p.id = d.packet_id
    JOIN rm.attempts a ON a.id = d.verification_attempt_id JOIN rm.jobs j ON j.id = a.job_id
    WHERE p.proof_ready AND NOT EXISTS (
        SELECT 1 FROM rm.formal_certificate_derivations bridge JOIN rm.foundation_stale stale
          ON stale.kind = 'item' AND stale.id = bridge.version_id WHERE bridge.derivation_id = d.id
    ) AND NOT EXISTS (
        SELECT 1 FROM rm.packet_basis_items b JOIN rm.foundation_stale stale ON stale.kind = 'item'
            AND stale.id = b.version_id WHERE b.packet_id = p.id
    ) AND NOT EXISTS (
        SELECT 1 FROM rm.verification_input_retirements r
        WHERE r.input_kind = 'verifier' AND r.sha256 = j.payload ->> 'checker_revision'
    ) AND NOT EXISTS (
        SELECT 1 FROM rm.evidence e JOIN rm.foundation_stale stale ON stale.kind = 'evidence' AND stale.id = e.id
        WHERE e.verification_attempt_id = d.verification_attempt_id
    );
