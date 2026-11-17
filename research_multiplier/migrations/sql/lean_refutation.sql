-- Use the reviewed theorem's exact logical negation as an independent Lean evidence path.
ALTER TABLE rm.lean_execution_links DROP CONSTRAINT lean_execution_links_target_check;
ALTER TABLE rm.lean_execution_links ADD CONSTRAINT lean_execution_links_target_check
    CHECK (target IN ('goal', 'upper_bound', 'attainment', 'refutation'));
ALTER TABLE rm.evidence DROP CONSTRAINT evidence_submission_reference;
ALTER TABLE rm.evidence ADD CONSTRAINT evidence_submission_reference CHECK (
    (lean_check_id IS NULL AND attempt_id IS NOT NULL)
    OR (lean_check_id IS NOT NULL AND kind IN ('lean', 'counterexample') AND attempt_id IS NULL)
);

CREATE FUNCTION rm.lean_negation_sha(approved jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT encode(sha256(convert_to(
        'import Lean' || chr(10) || coalesce((
            SELECT string_agg('import ' || m.value || chr(10), '' ORDER BY m.ordinal)
            FROM jsonb_array_elements_text(approved #> '{environment,external,modules}')
                 WITH ORDINALITY m(value, ordinal)
        ), '') || 'set_option autoImplicit false' || chr(10) || 'set_option maxHeartbeats 200000' || chr(10)
        || 'def RMTheory.expected : Prop := '
        || (SELECT string_agg('(' || chr(10) || a.value || chr(10) || ')', ' → ' ORDER BY a.ordinal)
            FROM jsonb_array_elements_text(approved #> '{scope,lean_hypotheses}' || jsonb_build_array(
                '_root_.Not (' || chr(10) || (approved ->> 'theorem_type') || chr(10) || ')'
            )) WITH ORDINALITY a(value, ordinal)) || chr(10), 'UTF8')), 'hex');
$$;
REVOKE ALL ON FUNCTION rm.lean_negation_sha(jsonb) FROM PUBLIC;


CREATE OR REPLACE FUNCTION rm.propose_lean_core_statement(packet_id uuid, body jsonb)
RETURNS rm.lean_statements LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE packet formalization_packets;
DECLARE subject claims;
DECLARE prior lean_statements;
DECLARE saved lean_statements;
DECLARE next_version integer;
DECLARE goal text;
DECLARE hypotheses jsonb;
DECLARE generated text;
DECLARE runtime jsonb;
DECLARE imports text := '';
BEGIN
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = propose_lean_core_statement.packet_id;
    SELECT * INTO STRICT subject FROM claims c WHERE c.id = packet.claim_id;
    IF subject.domain <> 'lean-core'
        OR subject.contract_snapshot #>> '{formal_skeleton,format}' IS DISTINCT FROM 'rm-goal-v1'
        OR subject.contract_sha256 IS NULL OR subject.status NOT IN ('open', 'proved') THEN
        RAISE EXCEPTION 'Lean Core requires a frozen research goal' USING ERRCODE = '23514';
    END IF;
    goal := subject.contract_snapshot #>> '{formal_skeleton,target,lean,proposition}';
    SELECT coalesce(jsonb_agg(h.value #> '{formal,lean,proposition}' ORDER BY h.ordinal), '[]'::jsonb) INTO hypotheses
        FROM jsonb_array_elements(subject.contract_snapshot -> 'hypotheses') WITH ORDINALITY h(value, ordinal);
    IF nullif(btrim(goal), '') IS NULL OR EXISTS (
        SELECT 1 FROM jsonb_array_elements(hypotheses) h WHERE jsonb_typeof(h) <> 'string'
            OR nullif(btrim(h #>> '{}'), '') IS NULL
    ) THEN
        RAISE EXCEPTION 'The frozen goal and every hypothesis require a formal Lean proposition'
            USING ERRCODE = '23514';
    END IF;
    IF body -> 'environment' ? 'external' THEN
        runtime := body #> '{environment,external}';
        IF jsonb_typeof(runtime) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'a frozen Mathlib environment needs its exact manifest' USING ERRCODE = '23514';
        END IF;
        IF (runtime - ARRAY['format_version', 'project_sha256', 'modules']) <> '{}'::jsonb
            OR runtime -> 'format_version' IS DISTINCT FROM '1'::jsonb
            OR NOT coalesce(runtime ->> 'project_sha256' ~ '^[0-9a-f]{64}$', false)
            OR jsonb_typeof(runtime -> 'modules') IS DISTINCT FROM 'array'
            OR jsonb_typeof(body -> 'elaborated_theorem_type') IS DISTINCT FROM 'string'
            OR nullif(btrim(body ->> 'elaborated_theorem_type'), '') IS NULL
            OR body #>> '{environment,inputs,mathlib_policy}' IS DISTINCT FROM 'native-mathlib-v1' THEN
            RAISE EXCEPTION 'the approved Mathlib environment must pin the compiled project and its module list' USING ERRCODE = '23514';
        END IF;
        IF jsonb_array_length(runtime -> 'modules') NOT BETWEEN 1 AND 12 OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(runtime -> 'modules') m
            WHERE jsonb_typeof(m) <> 'string' OR char_length(m #>> '{}') > 160
                OR m #>> '{}' !~ '^[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)*$'
                OR split_part(m #>> '{}', '.', 1) IN ('Lean', 'Init', 'Std', 'RMExpected', 'RMCandidate', 'Audit', 'RMTheory')
        ) OR jsonb_array_length(runtime -> 'modules') <> (
            SELECT count(DISTINCT m) FROM jsonb_array_elements_text(runtime -> 'modules') m
        ) THEN
            RAISE EXCEPTION 'list distinct reviewed Lean modules' USING ERRCODE = '23514';
        END IF;
        SELECT string_agg('import ' || m.value || chr(10), '' ORDER BY m.ordinal) INTO STRICT imports
        FROM jsonb_array_elements_text(runtime -> 'modules') WITH ORDINALITY m(value, ordinal);
    END IF;
    SELECT 'import Lean' || chr(10) || imports || 'set_option autoImplicit false' || chr(10) ||
        'set_option maxHeartbeats 200000' || chr(10) || 'def RMTheory.expected : Prop := ' ||
        string_agg('(' || chr(10) || t.value || chr(10) || ')', ' → ' ORDER BY t.ordinal) || chr(10)
    INTO generated FROM jsonb_array_elements_text(hypotheses || jsonb_build_array(goal)) WITH ORDINALITY t(value, ordinal);
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR body ->> 'candidate_theorem' IS DISTINCT FROM 'RMTheory.Proof.checked'
        OR body -> 'checker' IS DISTINCT FROM '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
        OR body ->> 'theorem_type' IS DISTINCT FROM goal
        OR body #> '{scope,lean_hypotheses}' IS DISTINCT FROM hypotheses
        OR body #>> '{scope,principal_packet_sha256}' IS DISTINCT FROM packet.packet_sha256
        OR body #>> '{scope,claim_contract_sha256}' IS DISTINCT FROM subject.contract_sha256
        OR NOT lean_artifact_ref_valid(body -> 'trusted_source')
        OR body #>> '{trusted_source,sha256}' IS DISTINCT FROM encode(sha256(convert_to(generated, 'UTF8')), 'hex')
        OR body #>> '{environment,toolchain}' IS DISTINCT FROM 'leanprover/lean4:v4.19.0'
        OR NOT coalesce(body #>> '{environment,sha256}' ~ '^[0-9a-f]{64}$', false)
        OR NOT coalesce(body #>> '{environment,inputs,driver}' ~ '^[0-9a-f]{64}$', false) THEN
        RAISE EXCEPTION 'The Lean review must match the frozen goal, hypotheses and generated source'
            USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM formalization_reviews r WHERE r.id = packet.review_id AND r.active_version = packet.version FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Lean review requires the current approved packet' USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(subject.id, jsonb_build_object(
        'formalization_id', packet.id, 'formalization_sha256', packet.packet_sha256));
    SELECT * INTO prior FROM lean_statements s WHERE s.packet_id = packet.id ORDER BY s.version DESC LIMIT 1;
    IF prior.id IS NOT NULL AND prior.body = body THEN
        RETURN prior;
    END IF;
    next_version := coalesce(prior.version, 0) + 1;
    UPDATE formalization_reviews r SET gate_revision = r.gate_revision + 1 WHERE r.id = packet.review_id;
    INSERT INTO lean_statements (packet_id, claim_id, contract_sha256, version, body, statement_sha256)
    VALUES (packet.id, subject.id, subject.contract_sha256, next_version, body,
        encode(sha256(convert_to(jsonb_build_object('packet_id', packet.id, 'packet_sha256', packet.packet_sha256,
            'contract_sha256', subject.contract_sha256, 'version', next_version, 'body', body)::text, 'UTF8')), 'hex'))
    RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.admit_lean_core_theorem(packet_id uuid, version_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved formal_item_versions;
DECLARE target record;
DECLARE runtime jsonb;
BEGIN
    SELECT f.claim_id, f.packet_sha256 INTO target FROM formalization_status f
    JOIN claims c ON c.id = f.claim_id
    WHERE f.id = admit_lean_core_theorem.packet_id AND f.proof_ready
        AND c.domain = 'lean-core';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'a promoted Lean Core theorem needs an approved Lean Core destination'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(target.claim_id, jsonb_build_object(
        'formalization_id', packet_id, 'formalization_sha256', target.packet_sha256));
    PERFORM lock_live_foundation();
    SELECT s.body #> '{environment,external}' INTO runtime FROM lean_statement_status s
    WHERE s.packet_id = admit_lean_core_theorem.packet_id AND s.check_ready;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'the destination needs a current approved Lean statement' USING ERRCODE = '23514';
    END IF;
    SELECT v.* INTO saved FROM formal_item_versions v
    JOIN current_lean_core_theorems e ON e.id = v.id
    WHERE v.id = admit_lean_core_theorem.version_id
        AND e.body #>> '{source,claim_id}' <> target.claim_id::text
        AND (e.body #> '{source,environment,external}') IS NOT DISTINCT FROM runtime;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'the cited theorem needs a current promoted proof from another claim with the same reviewed Lean imports'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_core_imports (packet_id, version_id)
        VALUES (admit_lean_core_theorem.packet_id, saved.id) ON CONFLICT ON CONSTRAINT lean_core_imports_pkey DO NOTHING;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.require_reviewed_mathlib_type() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE approved jsonb;
BEGIN
    SELECT s.body INTO STRICT approved FROM lean_statements s WHERE s.id = NEW.statement_id;
    IF approved -> 'environment' ? 'external' AND (
        NOT coalesce(approved #>> '{environment,external,project_sha256}' ~ '^[0-9a-f]{64}$', false)
        OR (NEW.body ->> 'target' IS DISTINCT FROM 'refutation'
            AND NEW.body ->> 'expected_type' IS DISTINCT FROM approved ->> 'elaborated_theorem_type')
        OR jsonb_typeof(NEW.body #> '{dependencies,external_constants}') IS DISTINCT FROM 'array'
    ) THEN
        RAISE EXCEPTION 'Mathlib evidence must match the reviewed elaborated type and include actual dependencies'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
BEGIN
    IF job.kind <> 'verify'
        OR job.payload ->> 'lean_statement_id' IS NULL
        OR coalesce(job.payload ->> 'target', '') NOT IN ('goal', 'upper_bound', 'attainment', 'refutation')
        OR NOT lean_artifact_ref_valid(job.payload -> 'submission')
        OR jsonb_typeof(coalesce(job.payload -> 'basis_version_ids', '[]'::jsonb)) IS DISTINCT FROM 'array'
        OR NOT coalesce(job.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$', false)
        OR NOT coalesce(job.payload ->> 'verification_environment_sha256' ~ '^[0-9a-f]{64}$', false) THEN
        RAISE EXCEPTION 'Lean verification needs an approved statement, target, and submission'
            USING ERRCODE = '23514';
    END IF;
    statement := require_lean_review((job.payload ->> 'lean_statement_id')::uuid, true);
    IF statement.claim_id IS DISTINCT FROM job.claim_id
        OR statement.contract_sha256 IS DISTINCT FROM job.contract_sha256
        OR statement.statement_sha256 IS DISTINCT FROM job.payload ->> 'statement_sha256'
        OR job.payload -> 'checker' IS DISTINCT FROM (CASE statement.body ->> 'theorem'
            WHEN 'RMTheory.expected' THEN '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
            ELSE '{"name":"rm-lean-candidate-audit","version":"1"}'::jsonb END)
        OR (statement.body ->> 'theorem' = 'RMTheory.expected' AND (
            job.payload ->> 'target' NOT IN ('goal', 'refutation')
            OR job.payload ->> 'checker_revision' IS DISTINCT FROM statement.body #>> '{environment,inputs,driver}'
            OR job.payload ->> 'verification_environment_sha256' IS DISTINCT FROM statement.body #>> '{environment,sha256}'
        ))
        OR (job.payload ->> 'target' = 'refutation' AND (
            statement.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
            OR job.payload ->> 'refutation_source_sha256' IS DISTINCT FROM lean_negation_sha(statement.body)
        )) THEN
        RAISE EXCEPTION 'Lean job does not match its frozen claim and reviewed statement'
            USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(statement.packet_id, coalesce(job.payload -> 'basis_version_ids', '[]'::jsonb));
    RETURN statement;
END $$;

CREATE OR REPLACE FUNCTION rm.store_lean_execution(statement_id uuid, expected_sha text, body jsonb, expected_target text)
RETURNS rm.lean_checks
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE attempt attempts;
DECLARE job jobs;
DECLARE prior lean_checks;
DECLARE saved lean_checks;
DECLARE digest text;
BEGIN
    SELECT * INTO attempt FROM attempts a WHERE a.id = (body ->> 'verification_attempt_id')::uuid;
    IF NOT FOUND OR attempt.role <> 'verify' OR attempt.finding <> 'exact_proof' THEN
        RAISE EXCEPTION 'a successful verifier attempt must precede a Lean result' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO STRICT job FROM jobs j WHERE j.id = attempt.job_id;
    statement := require_dynamic_lean_job(job);
    PERFORM lock_live_foundation();
    IF job.status <> 'succeeded'
        OR statement.id IS DISTINCT FROM statement_id
        OR statement.statement_sha256 IS DISTINCT FROM expected_sha
        OR job.payload ->> 'target' IS DISTINCT FROM expected_target
        OR body ->> 'target' IS DISTINCT FROM expected_target
        OR body ->> 'theorem' IS DISTINCT FROM statement.body ->> 'theorem'
        OR (expected_target IN ('goal', 'refutation') AND body ->> 'statement_type' IS DISTINCT FROM statement.body ->> 'theorem_type')
        OR body ->> 'candidate_theorem' IS DISTINCT FROM (CASE statement.body ->> 'theorem'
            WHEN 'RMTheory.expected' THEN 'RMTheory.Proof.checked'
            ELSE 'RMFlow.Submission.proof' END)
        OR body ->> 'trusted_source_sha256' IS DISTINCT FROM statement.body #>> '{trusted_source,sha256}'
        OR (expected_target = 'refutation' AND (
            body ->> 'refutation_source_sha256' IS DISTINCT FROM job.payload ->> 'refutation_source_sha256'
            OR body ->> 'refutation_mode' IS DISTINCT FROM 'logical_negation'
            OR body -> 'witness' IS DISTINCT FROM 'null'::jsonb
        ))
        OR body ->> 'environment_sha256' IS DISTINCT FROM statement.body #>> '{environment,sha256}'
        OR body -> 'checker' IS DISTINCT FROM job.payload -> 'checker'
        OR body -> 'submission' IS DISTINCT FROM job.payload -> 'submission'
        OR body ->> 'checker_revision' IS DISTINCT FROM job.payload ->> 'checker_revision'
        OR body ->> 'verification_environment_sha256' IS DISTINCT FROM job.payload ->> 'verification_environment_sha256'
        OR NOT coalesce(coalesce(job.payload -> 'basis_version_ids', '[]'::jsonb) @> (body -> 'basis_version_ids'), false)
        OR nullif(btrim(body ->> 'statement_type'), '') IS NULL
        OR body -> 'compiled' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'kernel_rechecked' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'scope_matches' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'accepted' IS DISTINCT FROM 'true'::jsonb
        OR NOT lean_artifact_ref_valid(body -> 'candidate')
        OR NOT lean_artifact_ref_valid(body -> 'report')
        OR body #>> '{report,uri}' IS DISTINCT FROM attempt.artifact_uri
        OR body #>> '{report,sha256}' IS DISTINCT FROM attempt.artifact_sha256
        OR NOT lean_analysis_valid(body) THEN
        RAISE EXCEPTION 'Lean result does not match the approved statement, submitted candidate and successful check'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_current_basis(statement.packet_id, body -> 'basis_version_ids');
    IF EXISTS (SELECT 1 FROM verification_input_retirements r
        WHERE (r.input_kind = 'verifier' AND r.sha256 = body ->> 'checker_revision')
          OR (r.input_kind = 'environment' AND r.sha256 IN (body ->> 'environment_sha256',
                                                            body ->> 'verification_environment_sha256'))
          OR (r.input_kind = 'source' AND r.sha256 IN (body ->> 'trusted_source_sha256',
                body #>> '{candidate,sha256}', body #>> '{submission,sha256}', body ->> 'refutation_source_sha256'))) THEN
        RAISE EXCEPTION 'Lean verification input requires recheck' USING ERRCODE = '23514';
    END IF;
    SELECT c.* INTO prior FROM lean_checks c JOIN lean_execution_links execution ON execution.check_id = c.id
        WHERE execution.verification_attempt_id = attempt.id;
    IF FOUND THEN
        IF prior.body IS DISTINCT FROM body THEN
            RAISE EXCEPTION 'verification attempt already has a different Lean result' USING ERRCODE = '23514';
        END IF;
        RETURN prior;
    END IF;

    digest := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');
    INSERT INTO lean_checks (statement_id, statement_sha256, body, check_sha256)
        VALUES (statement.id, statement.statement_sha256, body, digest)
        ON CONFLICT ON CONSTRAINT lean_check_digest_unique DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT c.* INTO STRICT saved FROM lean_checks c
            WHERE c.statement_id = statement.id AND c.check_sha256 = digest;
    END IF;
    INSERT INTO lean_execution_links (check_id, verification_attempt_id, target)
        VALUES (saved.id, attempt.id, expected_target) ON CONFLICT (check_id) DO NOTHING;
    INSERT INTO verification_basis_use (check_id, version_id, dependency_kind)
        SELECT saved.id, b.version_id, 'scope' FROM packet_basis_items b WHERE b.packet_id = statement.packet_id
        UNION SELECT saved.id, version_id::uuid, 'actual'
              FROM jsonb_array_elements_text(body -> 'basis_version_ids') ids(version_id)
        ON CONFLICT DO NOTHING;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.issue_lean_evidence(check_id uuid) RETURNS rm.evidence
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
DECLARE execution lean_execution_links;
DECLARE statement lean_statements;
DECLARE saved evidence;
DECLARE digest text;
BEGIN
    SELECT * INTO STRICT checked FROM lean_checks c WHERE c.id = issue_lean_evidence.check_id;
    SELECT * INTO execution FROM lean_execution_links e WHERE e.check_id = checked.id;
    IF NOT FOUND THEN
        PERFORM fence_legacy_lean_review(checked.id);
    ELSIF execution.target NOT IN ('goal', 'refutation') THEN
        RAISE EXCEPTION 'a derived result cannot be promoted as the complete claim' USING ERRCODE = '23514';
    END IF;
    statement := require_lean_review(checked.statement_id, true);
    PERFORM lock_live_foundation();
    IF EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = checked.id) THEN
        RAISE EXCEPTION 'Lean verification requires recheck' USING ERRCODE = '23514';
    END IF;
    digest := encode(sha256(convert_to('lean:' || checked.id::text || ':' || checked.check_sha256, 'UTF8')), 'hex');
    INSERT INTO evidence (claim_id, contract_sha256, attempt_id, kind, verdict, diff_status, audit_mode,
        artifact_uri, artifact_sha256, verifier_name, verifier_version, code_revision, environment_sha256,
        result, verification_attempt_id, issuance_sha256, lean_check_id)
    VALUES (statement.claim_id, statement.contract_sha256, NULL,
        CASE WHEN execution.target = 'refutation' THEN 'counterexample'::evidence_kind ELSE 'lean'::evidence_kind END,
        'verified', 'exact', 'none',
        checked.body #>> '{candidate,uri}', checked.body #>> '{candidate,sha256}',
        checked.body #>> '{checker,name}', checked.body #>> '{checker,version}',
        coalesce(checked.body ->> 'checker_revision', statement.body #>> '{environment,inputs,RMFlow/Audit.lean}',
                 statement.body #>> '{trusted_source,sha256}'),
        coalesce(checked.body ->> 'verification_environment_sha256', checked.body ->> 'environment_sha256'),
        jsonb_build_object('lean_check_id', checked.id, 'report', checked.body -> 'report')
        || CASE WHEN execution.target = 'refutation' THEN jsonb_build_object(
            'refutation_mode', 'logical_negation', 'witness', NULL, 'negated_type', checked.body -> 'expected_type')
            ELSE '{}'::jsonb END,
        execution.verification_attempt_id, digest, checked.id)
    ON CONFLICT (lean_check_id) DO NOTHING RETURNING * INTO saved;
    IF FOUND THEN
        -- A previously authenticated review of this exact legacy check can be
        -- reused when it becomes a canonical receipt.
        INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action,
                               decided_by, actor_ref, authenticated_role, reason, decided_at)
            SELECT statement.claim_id, statement.contract_sha256, saved.id, review.action,
                'human', review.actor_ref, review.authenticated_role, review.reason, review.reviewed_at
            FROM lean_check_reviews review WHERE review.check_id = checked.id ORDER BY review.sequence_no;
    ELSE
        SELECT * INTO STRICT saved FROM evidence e WHERE e.lean_check_id = checked.id;
    END IF;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_audit() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE author attempts%ROWTYPE;
DECLARE auditor attempts%ROWTYPE;
BEGIN
    IF NEW.kind IN ('lean', 'counterexample') AND NEW.lean_check_id IS NOT NULL THEN RETURN NEW; END IF;
    SELECT * INTO author FROM attempts WHERE id = NEW.attempt_id;
    IF ROW(author.artifact_uri, author.artifact_sha256)
        IS DISTINCT FROM ROW(NEW.artifact_uri, NEW.artifact_sha256) THEN
        RAISE EXCEPTION 'evidence artifact must match the submitted attempt' USING ERRCODE = '23514';
    END IF;
    IF NEW.verdict = 'verified' AND NEW.kind = 'counterexample'
        AND author.finding <> 'counterexample' THEN
        RAISE EXCEPTION 'a verified counterexample needs a counterexample attempt' USING ERRCODE = '23514';
    END IF;
    IF NEW.audit_mode = 'none' THEN RETURN NEW; END IF;
    SELECT * INTO auditor FROM attempts WHERE id = NEW.audit_attempt_id;
    IF author.role IS DISTINCT FROM 'prove' OR auditor.role IS DISTINCT FROM 'audit'
        OR author.model_family IS NULL OR auditor.model_family IS NULL OR auditor.finding IS DISTINCT FROM 'audit_report'
        OR (SELECT payload ->> 'candidate_attempt_id' FROM jobs WHERE id = auditor.job_id)
            IS DISTINCT FROM author.id::text
        OR (NEW.audit_mode = 'cross_family' AND author.model_family = auditor.model_family)
        OR (NEW.audit_mode = 'restricted_same_family' AND (
            author.model_family <> auditor.model_family OR author.model_id = auditor.model_id
            OR (SELECT sensitivity FROM claims WHERE id = NEW.claim_id) IS DISTINCT FROM 'restricted'
        )) THEN
        RAISE EXCEPTION 'audit attempt does not satisfy model-family policy' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
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
    IF NEW.kind = 'lean' OR (NEW.kind = 'counterexample' AND NEW.lean_check_id IS NOT NULL) THEN
        IF NEW.verdict <> 'verified' OR NEW.diff_status <> 'exact' OR NEW.audit_mode <> 'none' OR NOT EXISTS (
            SELECT 1 FROM lean_checks c JOIN lean_statement_status s ON s.id = c.statement_id
            LEFT JOIN lean_execution_links execution ON execution.check_id = c.id
            WHERE c.id = NEW.lean_check_id AND s.claim_id = NEW.claim_id
              AND s.contract_sha256 = NEW.contract_sha256 AND s.check_ready
              AND c.body -> 'accepted' = 'true'::jsonb AND c.body -> 'scope_matches' = 'true'::jsonb
              AND c.body #>> '{candidate,uri}' = NEW.artifact_uri AND c.body #>> '{candidate,sha256}' = NEW.artifact_sha256
              AND ((NEW.kind = 'lean' AND (execution.target = 'goal' OR execution.check_id IS NULL))
                   OR (NEW.kind = 'counterexample' AND execution.target = 'refutation'
                       AND c.body ->> 'refutation_mode' = 'logical_negation'
                       AND NEW.result ->> 'refutation_mode' = 'logical_negation'
                       AND NEW.result -> 'witness' = 'null'::jsonb))
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

CREATE OR REPLACE FUNCTION rm.review_lean_check(check_id uuid, action text, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
DECLARE recent lean_check_reviews;
DECLARE review_id uuid;
DECLARE evidence_id uuid;
DECLARE execution lean_execution_links;
BEGIN
    IF action IS NULL OR action NOT IN ('approve', 'return')
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'Lean review requires approval or return, a researcher name, and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT checked FROM lean_checks c WHERE c.id = review_lean_check.check_id;
    SELECT * INTO execution FROM lean_execution_links e WHERE e.check_id = checked.id;
    IF NOT FOUND THEN
        PERFORM fence_legacy_lean_review(checked.id);
    ELSIF execution.target NOT IN ('goal', 'refutation') THEN
        RAISE EXCEPTION 'review of a complete claim requires the full-goal verification' USING ERRCODE = '23514';
    END IF;
    SELECT e.id INTO evidence_id FROM evidence e WHERE e.lean_check_id = checked.id;
    IF FOUND THEN
        RETURN review_evidence(evidence_id, action, actor_ref, reason);
    END IF;
    IF action = 'approve' THEN
        PERFORM require_lean_review(checked.statement_id, true);
        PERFORM lock_live_foundation();
        IF EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = checked.id) THEN
            RAISE EXCEPTION 'approval requires a current Lean verification' USING ERRCODE = '23514';
        END IF;
    END IF;
    SELECT * INTO recent FROM lean_check_reviews d WHERE d.check_id = checked.id ORDER BY d.sequence_no DESC LIMIT 1;
    IF FOUND AND recent.action = action AND recent.actor_ref = actor_ref
        AND recent.authenticated_role = session_user AND recent.reason = reason THEN
        RETURN recent.id;
    END IF;
    INSERT INTO lean_check_reviews (check_id, action, actor_ref, reason)
    VALUES (checked.id, action, actor_ref, reason) RETURNING id INTO review_id;
    RETURN review_id;
END $$;


CREATE FUNCTION rm.record_verified_lean_refutation(statement_id uuid, expected_sha text, body jsonb) RETURNS rm.evidence
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
BEGIN
    checked := store_lean_execution(statement_id, expected_sha, body, 'refutation');
    RETURN issue_lean_evidence(checked.id);
END $$;
REVOKE ALL ON FUNCTION rm.record_verified_lean_refutation(uuid, text, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_verified_lean_refutation(uuid, text, jsonb) TO rm_verifier;

CREATE FUNCTION rm.guard_restricted_lean_model() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM claims c WHERE c.id = NEW.claim_id
               AND c.domain = 'lean-core' AND c.sensitivity = 'restricted') THEN
        RAISE EXCEPTION 'restricted Lean work cannot dispatch a model' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION rm.guard_restricted_lean_model() FROM PUBLIC;
CREATE TRIGGER check_restricted_lean_model_job BEFORE INSERT ON rm.jobs
    FOR EACH ROW WHEN (NEW.kind = 'prove' AND NEW.payload -> 'lean_author' = 'true'::jsonb)
    EXECUTE FUNCTION rm.guard_restricted_lean_model();
CREATE TRIGGER check_restricted_lean_model_attempt BEFORE INSERT ON rm.attempts
    FOR EACH ROW WHEN (NEW.model_provider IS NOT NULL) EXECUTE FUNCTION rm.guard_restricted_lean_model();


-- Invalidate both the original approved source and the exact generated negation.
CREATE OR REPLACE VIEW rm.foundation_stale WITH (security_barrier = true) AS
    WITH RECURSIVE edges(parent_kind, parent_id, child_kind, child_id) AS (
        SELECT 'item', version_id, 'lean', check_id FROM rm.verification_basis_use
        UNION ALL SELECT 'item', basis.version_id, 'lean', legacy.id FROM rm.lean_checks legacy
            JOIN rm.lean_statements statement ON statement.id = legacy.statement_id
            JOIN rm.packet_basis_items basis ON basis.packet_id = statement.packet_id
            WHERE NOT EXISTS (SELECT 1 FROM rm.lean_execution_links execution WHERE execution.check_id = legacy.id)
        UNION ALL SELECT 'item', version_id, 'evidence', evidence_id FROM rm.evidence_basis_use
        UNION ALL SELECT 'lean', check_id, 'item', version_id FROM rm.formal_lean_derivations
        UNION ALL SELECT 'evidence', evidence_id, 'item', version_id FROM rm.lean_core_exports
        UNION ALL SELECT 'lean', lean_check_id, 'evidence', id FROM rm.evidence WHERE lean_check_id IS NOT NULL
        UNION ALL SELECT 'item', basis.version_id, 'item', bridge.version_id
            FROM rm.formal_certificate_derivations bridge JOIN rm.flow_derivation_records fact
                ON fact.id = bridge.derivation_id
            JOIN rm.packet_basis_items basis ON basis.packet_id = fact.packet_id
                AND basis.item_label = ANY(fact.premise_item_ids)
        UNION ALL SELECT 'evidence', e.id, 'item', bridge.version_id
            FROM rm.formal_certificate_derivations bridge JOIN rm.flow_derivation_records fact
                ON fact.id = bridge.derivation_id
            JOIN rm.evidence e ON e.verification_attempt_id = fact.verification_attempt_id
                AND e.kind = 'exact_certificate' AND e.verdict = 'verified'
    ), roots(kind, id) AS (
        SELECT 'item', version_id FROM rm.formal_item_retirements
        UNION SELECT 'item', publication.version_id FROM rm.lean_core_exports publication
            JOIN rm.evidence receipt ON receipt.id = publication.evidence_id
            JOIN rm.claims c ON c.id = receipt.claim_id
            JOIN rm.lean_checks checked ON checked.id = receipt.lean_check_id
            JOIN rm.lean_statement_status statement ON statement.id = checked.statement_id
            WHERE c.status <> 'proved' OR c.decisive_evidence_id IS DISTINCT FROM receipt.id
               OR NOT statement.check_ready
        UNION SELECT 'item', v.id FROM rm.formal_item_versions v JOIN rm.verification_input_retirements r
            ON r.input_kind = 'source' AND r.sha256 = v.body #>> '{source,sha256}'
        UNION SELECT 'lean', c.id FROM rm.lean_checks c
            JOIN rm.lean_statements s ON s.id = c.statement_id
            JOIN rm.verification_input_retirements r ON
                (r.input_kind = 'verifier' AND r.sha256 = coalesce(c.body ->> 'checker_revision',
                    s.body #>> '{environment,inputs,RMFlow/Audit.lean}')) OR
                (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                              c.body ->> 'verification_environment_sha256')) OR
                (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
                    c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
                    c.body ->> 'refutation_source_sha256'))
        UNION SELECT 'lean', c.id FROM rm.lean_checks c JOIN rm.lean_statements s ON s.id = c.statement_id
            WHERE EXISTS (SELECT 1 FROM rm.lean_statements newer WHERE newer.packet_id = s.packet_id
                          AND newer.version > s.version)
        UNION SELECT 'item', bridge.version_id FROM rm.formal_certificate_derivations bridge
            JOIN rm.flow_derivation_records fact ON fact.id = bridge.derivation_id
            JOIN rm.attempts attempt ON attempt.id = fact.verification_attempt_id
            JOIN rm.jobs job ON job.id = attempt.job_id
            JOIN rm.verification_input_retirements r ON r.input_kind = 'verifier'
                AND r.sha256 = job.payload ->> 'checker_revision'
        UNION SELECT 'evidence', e.id FROM rm.evidence e JOIN rm.verification_input_retirements r
            ON (r.input_kind = 'verifier' AND r.sha256 = e.code_revision)
            OR (r.input_kind = 'environment' AND r.sha256 = e.environment_sha256)
            OR (r.input_kind = 'source' AND r.sha256 = e.artifact_sha256)
    ), obsolete(kind, id) AS (
        SELECT kind, id FROM roots
        UNION
        SELECT e.child_kind, e.child_id FROM obsolete o JOIN edges e
            ON e.parent_kind = o.kind AND e.parent_id = o.id
    )
    SELECT kind, id FROM obsolete;
