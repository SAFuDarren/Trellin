CREATE FUNCTION rm.propose_lean_core_statement(packet_id uuid, body jsonb)
RETURNS rm.lean_statements LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE packet formalization_packets;
DECLARE subject claims;
DECLARE prior lean_statements;
DECLARE saved lean_statements;
DECLARE next_version integer;
DECLARE goal text;
DECLARE hypotheses jsonb;
DECLARE generated text;
BEGIN
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = propose_lean_core_statement.packet_id;
    SELECT * INTO STRICT subject FROM claims c WHERE c.id = packet.claim_id;
    IF subject.domain <> 'lean-core' OR subject.sensitivity <> 'public'
        OR subject.contract_snapshot #>> '{formal_skeleton,format}' IS DISTINCT FROM 'rm-goal-v1'
        OR subject.contract_sha256 IS NULL OR subject.status NOT IN ('open', 'proved') THEN
        RAISE EXCEPTION 'Lean Core requires a public frozen research goal' USING ERRCODE = '23514';
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
    SELECT 'import Lean' || chr(10) || 'set_option autoImplicit false' || chr(10) ||
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
REVOKE ALL ON FUNCTION rm.propose_lean_core_statement(uuid, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_lean_core_statement(uuid, jsonb) TO rm_human;

CREATE OR REPLACE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
BEGIN
    IF job.kind <> 'verify'
        OR job.payload ->> 'lean_statement_id' IS NULL
        OR coalesce(job.payload ->> 'target', '') NOT IN ('goal', 'upper_bound', 'attainment')
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
            job.payload ->> 'target' <> 'goal'
            OR job.payload ->> 'checker_revision' IS DISTINCT FROM statement.body #>> '{environment,inputs,driver}'
            OR job.payload ->> 'verification_environment_sha256' IS DISTINCT FROM statement.body #>> '{environment,sha256}'
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
        OR (expected_target = 'goal' AND body ->> 'statement_type' IS DISTINCT FROM statement.body ->> 'theorem_type')
        OR body ->> 'candidate_theorem' IS DISTINCT FROM (CASE statement.body ->> 'theorem'
            WHEN 'RMTheory.expected' THEN 'RMTheory.Proof.checked'
            ELSE 'RMFlow.Submission.proof' END)
        OR body ->> 'trusted_source_sha256' IS DISTINCT FROM statement.body #>> '{trusted_source,sha256}'
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
                body #>> '{candidate,sha256}', body #>> '{submission,sha256}'))) THEN
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

-- Only the pinned pilot project may use the legacy, non-queued Lean receipt path.
CREATE OR REPLACE FUNCTION rm.record_lean_check(statement_id uuid, expected_sha256 text, body jsonb)
RETURNS rm.lean_checks LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE saved lean_checks;
DECLARE digest text;
BEGIN
    statement := require_lean_review(statement_id, true);
    IF statement.body ->> 'theorem' IS DISTINCT FROM 'RMFlow.Expected.realGoal'
        OR statement.statement_sha256 IS DISTINCT FROM expected_sha256
        OR body ->> 'theorem' IS DISTINCT FROM statement.body ->> 'theorem'
        OR body ->> 'candidate_theorem' IS DISTINCT FROM 'RMFlow.RealProof.proof'
        OR body ->> 'trusted_source_sha256' IS DISTINCT FROM statement.body #>> '{trusted_source,sha256}'
        OR body ->> 'environment_sha256' IS DISTINCT FROM statement.body #>> '{environment,sha256}' THEN
        RAISE EXCEPTION 'Lean check does not match the approved source, theorem, or pinned environment'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body -> 'compiled' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'kernel_rechecked' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'scope_matches' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'accepted' IS DISTINCT FROM 'true'::jsonb
        OR NOT lean_artifact_ref_valid(body -> 'candidate')
        OR NOT lean_artifact_ref_valid(body -> 'report')
        OR jsonb_typeof(body -> 'checker') IS DISTINCT FROM 'object'
        OR jsonb_typeof(body #> '{checker,name}') IS DISTINCT FROM 'string'
        OR jsonb_typeof(body #> '{checker,version}') IS DISTINCT FROM 'string'
        OR jsonb_typeof(body -> 'dependencies') IS DISTINCT FROM 'object'
        OR body #>> '{checker,name}' IS DISTINCT FROM 'rm-lean-flow-audit'
        OR body #>> '{checker,version}' IS DISTINCT FROM '1'
        OR jsonb_typeof(body #> '{dependencies,axioms}') IS DISTINCT FROM 'array'
        OR body #> '{dependencies,unauthorized_axioms}' IS DISTINCT FROM '[]'::jsonb
        OR body #> '{dependencies,unapproved}' IS DISTINCT FROM '[]'::jsonb
        OR body #> '{dependencies,unsafe}' IS DISTINCT FROM '[]'::jsonb THEN
        RAISE EXCEPTION 'Lean check requires accepted scope and dependency verification with replay artifacts'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(body #> '{dependencies,axioms}') a
               WHERE a NOT IN ('"propext"'::jsonb, '"Classical.choice"'::jsonb, '"Quot.sound"'::jsonb)) THEN
        RAISE EXCEPTION 'Lean check uses an unapproved axiom' USING ERRCODE = '23514';
    END IF;
    digest := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');
    INSERT INTO lean_checks (statement_id, statement_sha256, body, check_sha256)
    VALUES (statement.id, statement.statement_sha256, body, digest)
    ON CONFLICT ON CONSTRAINT lean_check_digest_unique DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM lean_checks c WHERE c.statement_id = statement.id AND c.check_sha256 = digest;
    END IF;
    RETURN saved;
END $$;

