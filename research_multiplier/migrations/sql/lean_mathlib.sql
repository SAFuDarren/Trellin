-- Freeze the native Lean build and import order alongside the reviewed proposition.
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
CREATE OR REPLACE FUNCTION rm.require_reviewed_mathlib_type() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE approved jsonb;
BEGIN
    SELECT s.body INTO STRICT approved FROM lean_statements s WHERE s.id = NEW.statement_id;
    IF approved -> 'environment' ? 'external' AND (
        NOT coalesce(approved #>> '{environment,external,project_sha256}' ~ '^[0-9a-f]{64}$', false)
        OR NEW.body ->> 'expected_type' IS DISTINCT FROM approved ->> 'elaborated_theorem_type'
        OR jsonb_typeof(NEW.body #> '{dependencies,external_constants}') IS DISTINCT FROM 'array'
    ) THEN
        RAISE EXCEPTION 'Mathlib evidence must match the reviewed elaborated type and include actual dependencies'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION rm.require_reviewed_mathlib_type() FROM PUBLIC;
CREATE OR REPLACE TRIGGER require_reviewed_mathlib_type BEFORE INSERT ON rm.lean_checks
    FOR EACH ROW EXECUTE FUNCTION rm.require_reviewed_mathlib_type();

CREATE OR REPLACE FUNCTION rm.admit_lean_core_theorem(packet_id uuid, version_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved formal_item_versions;
DECLARE target record;
DECLARE runtime jsonb;
BEGIN
    SELECT f.claim_id, f.packet_sha256 INTO target FROM formalization_status f
    JOIN claims c ON c.id = f.claim_id
    WHERE f.id = admit_lean_core_theorem.packet_id AND f.proof_ready
        AND c.domain = 'lean-core' AND c.sensitivity = 'public';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'a promoted Lean Core theorem needs an approved public Lean Core destination'
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

