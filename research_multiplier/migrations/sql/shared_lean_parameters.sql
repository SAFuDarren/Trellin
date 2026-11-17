-- Match the trusted Lean source's explicit outer binders without accepting a raw header.
CREATE FUNCTION rm.lean_parameter_prefix(shared jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog AS $$
DECLARE binders text;
BEGIN
    IF shared IS NULL THEN RETURN ''; END IF;
    IF jsonb_typeof(shared) IS DISTINCT FROM 'array' OR jsonb_array_length(shared) NOT BETWEEN 1 AND 16 THEN
        RAISE EXCEPTION 'declare one to sixteen shared Lean parameters' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements(shared) p
        WHERE jsonb_typeof(p) IS DISTINCT FROM 'object'
           OR p - ARRAY['name','type'] <> '{}'::jsonb
           OR jsonb_typeof(p -> 'name') IS DISTINCT FROM 'string'
           OR NOT coalesce(p ->> 'name' ~ '^[A-Za-z_][A-Za-z_0-9'']{0,63}$', false)
           OR p ->> 'name' = '_'
           OR jsonb_typeof(p -> 'type') IS DISTINCT FROM 'string'
           OR NOT coalesce(length(p ->> 'type') BETWEEN 1 AND 2000, false)
           OR nullif(btrim(p ->> 'type'), '') IS NULL OR (p ->> 'type') ~ '[[:cntrl:]]'
    ) OR EXISTS (
        SELECT FROM jsonb_array_elements(shared) p GROUP BY p ->> 'name' HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'shared Lean parameters need distinct explicit names and nonempty single-line types'
            USING ERRCODE = '22023';
    END IF;
    SELECT string_agg('(' || (p ->> 'name') || ' : ' || (p ->> 'type') || ')', ' ' ORDER BY position)
        INTO binders FROM jsonb_array_elements(shared) WITH ORDINALITY ordered(p, position);
    RETURN '∀ ' || binders || ', ';
END $$;
REVOKE ALL ON FUNCTION rm.lean_parameter_prefix(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_parameter_prefix(jsonb) TO rm_human;

CREATE FUNCTION rm.lean_scoped_formula(parameters jsonb, hypotheses jsonb, consequent text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT lean_parameter_prefix(parameters) ||
        string_agg('(' || chr(10) || arg || chr(10) || ')', ' → ' ORDER BY position)
    FROM jsonb_array_elements_text(hypotheses || jsonb_build_array(consequent)) WITH ORDINALITY args(arg, position)
$$;
REVOKE ALL ON FUNCTION rm.lean_scoped_formula(jsonb,jsonb,text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.preview_research_target(body jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['title','domain','sensitivity','statement','formal',
                        'quantifiers','parameters','constant_dependencies'] <> '{}'::jsonb
        OR jsonb_typeof(body -> 'title') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'title'), '') IS NULL OR length(body ->> 'title') > 300
        OR jsonb_typeof(body -> 'domain') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'domain'), '') IS NULL OR length(body ->> 'domain') > 100
        OR coalesce(body ->> 'sensitivity', '') NOT IN ('public','restricted')
        OR jsonb_typeof(body -> 'statement') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'statement'), '') IS NULL OR length(body ->> 'statement') > 4000
        OR jsonb_typeof(body -> 'formal') IS DISTINCT FROM 'object' OR body -> 'formal' = '{}'::jsonb
        OR (body ->> 'domain' = 'lean-core' AND (
            jsonb_typeof(body #> '{formal,lean,proposition}') IS DISTINCT FROM 'string'
            OR nullif(btrim(body #>> '{formal,lean,proposition}'), '') IS NULL
            OR length(body #>> '{formal,lean,proposition}') > 16000
            OR (body -> 'formal') - 'lean' <> '{}'::jsonb
            OR (body #> '{formal,lean}') - ARRAY['proposition','parameters'] <> '{}'::jsonb))
        OR (body ? 'quantifiers' AND jsonb_typeof(body -> 'quantifiers') IS DISTINCT FROM 'array')
        OR (body ? 'parameters' AND jsonb_typeof(body -> 'parameters') IS DISTINCT FROM 'array')
        OR (body ? 'constant_dependencies' AND jsonb_typeof(body -> 'constant_dependencies') IS DISTINCT FROM 'object')
        OR octet_length(body::text) > 65000 THEN
        RAISE EXCEPTION 'preview the exact natural and formal target with its scope'
            USING ERRCODE = '22023';
    END IF;
    IF body ->> 'domain' = 'lean-core' THEN
        PERFORM lean_parameter_prefix(body #> '{formal,lean,parameters}');
    END IF;
    body := jsonb_build_object('quantifiers','[]'::jsonb,'parameters','[]'::jsonb,
                               'constant_dependencies','{}'::jsonb) || body;
    RETURN jsonb_build_object('body',body,'target_sha256',encode(sha256(convert_to(body::text, 'UTF8')), 'hex'));
END $$;

CREATE FUNCTION rm.guard_shared_lean_parameters() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE frame research_goals;
DECLARE parameters jsonb := NEW.body #> '{formal,lean,parameters}';
BEGIN
    SELECT * INTO STRICT frame FROM research_goals g WHERE g.id = NEW.goal_id;
    IF frame.body ->> 'domain' <> 'lean-core' THEN
        RETURN NEW;
    END IF;
    PERFORM lean_parameter_prefix(parameters);
    IF EXISTS (
        SELECT FROM jsonb_array_elements_text(frame.body -> 'basis_version_ids') AS refs(id)
        JOIN formal_item_versions v ON v.id = refs.id::uuid
        WHERE (v.body ->> 'kind' = 'hypothesis' AND
               v.body #> '{formal,lean,parameters}' IS DISTINCT FROM parameters)
           OR (v.body ->> 'kind' <> 'hypothesis' AND v.body #> '{formal,lean,parameters}' IS NOT NULL
               AND v.body ->> 'format' IS DISTINCT FROM 'lean-core-proof')
    ) THEN
        RAISE EXCEPTION 'each scoped hypothesis must declare the same ordered Lean parameters as the target'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION rm.guard_shared_lean_parameters() FROM PUBLIC;
CREATE TRIGGER shared_lean_parameters BEFORE INSERT ON rm.research_conjectures
    FOR EACH ROW EXECUTE FUNCTION rm.guard_shared_lean_parameters();

CREATE OR REPLACE FUNCTION rm.propose_lean_core_statement(packet_id uuid, body jsonb)
RETURNS rm.lean_statements LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE packet formalization_packets;
DECLARE subject claims;
DECLARE prior lean_statements;
DECLARE saved lean_statements;
DECLARE next_version integer;
DECLARE goal text;
DECLARE hypotheses jsonb;
DECLARE parameters jsonb;
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
    parameters := subject.contract_snapshot #> '{formal_skeleton,target,lean,parameters}';
    IF EXISTS (
        SELECT FROM jsonb_array_elements(subject.contract_snapshot -> 'hypotheses') h
        WHERE h #> '{formal,lean,parameters}' IS DISTINCT FROM parameters
    ) THEN
        RAISE EXCEPTION 'the frozen hypotheses must share the exact scope of the Lean target'
            USING ERRCODE = '23514';
    END IF;
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
        lean_scoped_formula(parameters, hypotheses, goal) || chr(10)
    INTO generated;
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR body ->> 'candidate_theorem' IS DISTINCT FROM 'RMTheory.Proof.checked'
        OR body -> 'checker' IS DISTINCT FROM '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
        OR body ->> 'theorem_type' IS DISTINCT FROM goal
        OR body #> '{scope,lean_hypotheses}' IS DISTINCT FROM hypotheses
        OR body #> '{scope,lean_parameters}' IS DISTINCT FROM parameters
        OR (parameters IS NOT NULL AND (
              NOT coalesce(body #>> '{environment,inputs,parameter_scope}' ~ '^[0-9a-f]{64}$', false)
              OR jsonb_typeof(body -> 'elaborated_theorem_type') IS DISTINCT FROM 'string'
              OR nullif(btrim(body ->> 'elaborated_theorem_type'), '') IS NULL))
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

CREATE OR REPLACE FUNCTION rm.local_lean_lemma_source_sha(approved jsonb, proposition text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT encode(sha256(convert_to(
        'import Lean' || chr(10) || coalesce((
            SELECT string_agg('import ' || m.value || chr(10), '' ORDER BY m.ordinal)
            FROM jsonb_array_elements_text(approved #> '{environment,external,modules}')
                 WITH ORDINALITY m(value, ordinal)
        ), '') || 'set_option autoImplicit false' || chr(10) || 'set_option maxHeartbeats 200000' || chr(10)
        || 'def RMTheory.expected : Prop := '
        || lean_scoped_formula(approved #> '{scope,lean_parameters}', approved #> '{scope,lean_hypotheses}',
                               proposition) || chr(10), 'UTF8')), 'hex');
$$;

CREATE OR REPLACE FUNCTION rm.lean_negation_sha(approved jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT encode(sha256(convert_to(
        'import Lean' || chr(10) || coalesce((
            SELECT string_agg('import ' || m.value || chr(10), '' ORDER BY m.ordinal)
            FROM jsonb_array_elements_text(approved #> '{environment,external,modules}')
                 WITH ORDINALITY m(value, ordinal)
        ), '') || 'set_option autoImplicit false' || chr(10) || 'set_option maxHeartbeats 200000' || chr(10)
        || 'def RMTheory.expected : Prop := (' || chr(10) || '_root_.Not (' || chr(10)
        || lean_scoped_formula(approved #> '{scope,lean_parameters}', approved #> '{scope,lean_hypotheses}',
                               approved ->> 'theorem_type')
        || chr(10) || ')' || chr(10) || ')' || chr(10), 'UTF8')), 'hex');
$$;

CREATE OR REPLACE FUNCTION rm.publish_prior_lean_core_theorem(evidence_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE source record;
DECLARE saved formal_item_versions;
BEGIN
    PERFORM lock_live_foundation();
    IF lean_receipt_has_local_inputs(evidence_id) THEN
        RAISE EXCEPTION 'a proof using local theorems needs a portable proof before cross-claim export'
            USING ERRCODE = '23514';
    END IF;
    SELECT e.id AS eid, e.claim_id, e.contract_sha256, e.code_revision, e.environment_sha256,
           statement.id AS sid, statement.statement_sha256, statement.packet_id, statement.body AS approved,
           f.packet_sha256, c.statement AS natural, checked.id AS check_id, checked.body AS report,
           proof.id AS attempt_id, j.payload AS task
    INTO source
    FROM evidence_foundation_status e
    JOIN claims c ON c.id = e.claim_id AND c.decisive_evidence_id = e.id AND c.status = 'proved'
    JOIN lean_checks checked ON checked.id = e.lean_check_id
    JOIN lean_statement_status statement ON statement.id = checked.statement_id
    JOIN formalization_status f ON f.id = statement.packet_id
    JOIN lean_execution_links execution ON execution.check_id = checked.id AND execution.target = 'goal'
         AND execution.verification_attempt_id = e.verification_attempt_id
    JOIN attempts proof ON proof.id = execution.verification_attempt_id AND proof.role = 'verify'
         AND proof.finding = 'exact_proof'
    JOIN jobs j ON j.id = proof.job_id
    WHERE e.id = publish_prior_lean_core_theorem.evidence_id
        AND c.domain = 'lean-core' AND c.sensitivity = 'public'
        AND e.kind = 'lean' AND e.verdict = 'verified' AND e.diff_status = 'exact'
        AND e.review_status = 'approved' AND NOT e.needs_recheck
        AND statement.check_ready
        AND statement.body ->> 'theorem' = 'RMTheory.expected'
        AND statement.body -> 'checker' = '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
        AND checked.body ->> 'checker_revision' = e.code_revision
        AND checked.body ->> 'verification_environment_sha256' = e.environment_sha256
        AND checked.body ->> 'trusted_source_sha256' = statement.body #>> '{trusted_source,sha256}'
        AND checked.body -> 'submission' = j.payload -> 'submission'
        AND checked.body -> 'candidate' = jsonb_build_object('uri', e.artifact_uri, 'sha256', e.artifact_sha256)
        AND statement.statement_sha256 = j.payload ->> 'statement_sha256';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'only the current promoted exact public Lean Core receipt can be exported'
            USING ERRCODE = '23514';
    END IF;

    saved := insert_formal_item('lean:export:' || source.eid, 'lean',
        jsonb_build_object('kind', 'theorem', 'format', 'lean-core-proof', 'natural', source.natural,
            'formal', jsonb_build_object('lean', jsonb_strip_nulls(jsonb_build_object(
                'proposition', source.approved -> 'theorem_type',
                'antecedents', source.approved #> '{scope,lean_hypotheses}',
                'parameters', source.approved #> '{scope,lean_parameters}'))),
            'source', jsonb_build_object(
                'kind', 'lean', 'evidence_id', source.eid, 'claim_id', source.claim_id,
                'contract_sha256', source.contract_sha256,
                'check_id', source.check_id, 'verification_attempt_id', source.attempt_id,
                'statement_id', source.sid, 'statement_sha256', source.statement_sha256,
                'packet_id', source.packet_id, 'packet_sha256', source.packet_sha256,
                'trusted_source', source.approved -> 'trusted_source',
                'checker', source.approved -> 'checker', 'checker_revision', source.code_revision,
                'environment', source.approved -> 'environment',
                'environment_sha256', source.environment_sha256,
                'candidate', source.report -> 'candidate', 'submission', source.report -> 'submission',
                'basis_version_ids', source.task -> 'basis_version_ids')));
    IF EXISTS (SELECT 1 FROM foundation_stale WHERE kind = 'item' AND id = saved.id) THEN
        RAISE EXCEPTION 'this Lean theorem export has been retired' USING ERRCODE = '23514';
    END IF;
    INSERT INTO formal_lean_derivations (version_id, packet_id, check_id)
        VALUES (saved.id, source.packet_id, source.check_id) ON CONFLICT (version_id) DO NOTHING;
    INSERT INTO lean_core_exports (version_id, evidence_id)
        VALUES (saved.id, source.eid) ON CONFLICT (version_id) DO NOTHING;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.publish_native_lean_theorem(evidence_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE original record;
DECLARE saved formal_item_versions;
BEGIN
    PERFORM lock_live_foundation();
    SELECT e.id, e.claim_id, e.contract_sha256, e.code_revision, e.environment_sha256,
           c.statement AS natural, checked.id AS check_id, checked.body AS audit,
           s.id AS statement_id, s.statement_sha256, s.packet_id, s.body AS approved,
           p.packet_sha256, proof.id AS attempt_id, archived.binding
    INTO original
    FROM evidence_foundation_status e
    JOIN claims c ON c.id = e.claim_id AND c.status = 'proved' AND c.decisive_evidence_id = e.id
    JOIN lean_checks checked ON checked.id = e.lean_check_id
    JOIN lean_statement_status s ON s.id = checked.statement_id AND s.check_ready
    JOIN formalization_status p ON p.id = s.packet_id
    JOIN lean_execution_links execution ON execution.check_id = checked.id AND execution.target = 'goal'
        AND execution.verification_attempt_id = e.verification_attempt_id
    JOIN attempts proof ON proof.id = execution.verification_attempt_id AND proof.role = 'verify'
        AND proof.finding = 'exact_proof'
    JOIN native_lean_replay_archives archived ON archived.evidence_id = e.id
    WHERE e.id = $1 AND c.domain = 'lean-core' AND e.kind = 'lean'
        AND e.review_status = 'approved' AND e.verdict = 'verified' AND e.diff_status = 'exact'
        AND NOT e.needs_recheck AND s.body ->> 'theorem' = 'RMTheory.expected'
        AND NOT EXISTS (SELECT FROM verification_input_retirements retired
            WHERE (retired.input_kind, retired.sha256::text) IN (
                ('source', archived.binding #>> '{artifact,sha256}'),
                ('verifier', archived.binding ->> 'verifier_revision')));
    IF NOT FOUND THEN
        RAISE EXCEPTION 'a native Lean receipt needs a current promoted proof and a portable structured proof or checked replay archive'
            USING ERRCODE = '23514';
    END IF;

    saved := insert_formal_item('lean:export:' || original.id, 'lean',
        jsonb_build_object('kind', 'theorem', 'format', 'lean-native-proof', 'natural', original.natural,
            'formal', jsonb_build_object('lean', jsonb_build_object(
                'proposition', CASE WHEN original.approved #> '{scope,lean_parameters}' IS NULL
                    THEN original.approved ->> 'theorem_type'
                    ELSE lean_scoped_formula(original.approved #> '{scope,lean_parameters}',
                        original.approved #> '{scope,lean_hypotheses}', original.approved ->> 'theorem_type') END,
                'antecedents', CASE WHEN original.approved #> '{scope,lean_parameters}' IS NULL
                    THEN original.approved #> '{scope,lean_hypotheses}' ELSE '[]'::jsonb END)),
            'source', jsonb_build_object(
                'kind', 'lean', 'evidence_id', original.id, 'claim_id', original.claim_id,
                'contract_sha256', original.contract_sha256, 'check_id', original.check_id,
                'verification_attempt_id', original.attempt_id, 'statement_id', original.statement_id,
                'statement_sha256', original.statement_sha256, 'packet_id', original.packet_id,
                'packet_sha256', original.packet_sha256, 'trusted_source', original.approved -> 'trusted_source',
                'checker', original.approved -> 'checker', 'checker_revision', original.code_revision,
                'environment', original.approved -> 'environment', 'environment_sha256', original.environment_sha256,
                'candidate', original.audit -> 'candidate', 'submission', original.audit -> 'submission',
                'basis_version_ids', original.binding -> 'basis_version_ids',
                'replay', original.binding -> 'artifact', 'sha256', original.binding #>> '{artifact,sha256}',
                'replay_builder_revision', original.binding -> 'verifier_revision')));
    IF EXISTS (SELECT FROM foundation_stale WHERE kind = 'item' AND id = saved.id) THEN
        RAISE EXCEPTION 'this Lean theorem export has been retired' USING ERRCODE = '23514';
    END IF;
    INSERT INTO formal_lean_derivations (version_id, packet_id, check_id)
        VALUES (saved.id, original.packet_id, original.check_id) ON CONFLICT (version_id) DO NOTHING;
    INSERT INTO lean_core_exports (version_id, evidence_id)
        VALUES (saved.id, original.id) ON CONFLICT (version_id) DO NOTHING;
    RETURN saved;
END $$;
