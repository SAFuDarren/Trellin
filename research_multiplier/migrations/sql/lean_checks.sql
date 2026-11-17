CREATE TABLE rm.lean_statements (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    claim_id uuid NOT NULL,
    contract_sha256 rm.sha256_hex NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    statement_sha256 rm.sha256_hex NOT NULL,
    proposed_by name NOT NULL DEFAULT session_user,
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (packet_id, version),
    UNIQUE (id, statement_sha256),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES rm.claims(id, contract_sha256)
);

CREATE TABLE rm.lean_statement_approvals (
    statement_id uuid PRIMARY KEY REFERENCES rm.lean_statements(id),
    approved_by text NOT NULL CHECK (btrim(approved_by) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.lean_checks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id uuid NOT NULL,
    statement_sha256 rm.sha256_hex NOT NULL,
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    check_sha256 rm.sha256_hex NOT NULL,
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT lean_check_digest_unique UNIQUE (statement_id, check_sha256),
    FOREIGN KEY (statement_id, statement_sha256) REFERENCES rm.lean_statements(id, statement_sha256)
);

CREATE TABLE rm.lean_check_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    check_id uuid NOT NULL REFERENCES rm.lean_checks(id),
    action text NOT NULL CHECK (action IN ('approve', 'return')),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE
);
CREATE INDEX lean_check_recent_reviews ON rm.lean_check_reviews(check_id, sequence_no DESC);

CREATE TRIGGER lean_statements_append_only BEFORE UPDATE OR DELETE ON rm.lean_statements
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER lean_statement_approvals_append_only BEFORE UPDATE OR DELETE ON rm.lean_statement_approvals
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER lean_checks_append_only BEFORE UPDATE OR DELETE ON rm.lean_checks
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER lean_check_reviews_append_only BEFORE UPDATE OR DELETE ON rm.lean_check_reviews
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE VIEW rm.lean_statement_status WITH (security_barrier = true) AS
    SELECT s.*, a.statement_id IS NOT NULL AS approved, latest.is_current AS "current",
        a.statement_id IS NOT NULL AND latest.is_current AND p.proof_ready
            AND claim.status IN ('open', 'proved') AS check_ready,
        a.approved_by, a.approved_at, a.reason AS approval_reason
    FROM rm.lean_statements s
    JOIN rm.formalization_status p ON p.id = s.packet_id
    JOIN rm.claims claim ON claim.id = s.claim_id
    LEFT JOIN rm.lean_statement_approvals a ON a.statement_id = s.id
    CROSS JOIN LATERAL (
        SELECT NOT EXISTS (SELECT 1 FROM rm.lean_statements newer
                           WHERE newer.packet_id = s.packet_id AND newer.version > s.version) AS is_current
    ) latest;

CREATE VIEW rm.lean_check_status WITH (security_barrier = true) AS
    SELECT c.*, s.claim_id, s.packet_id, s.contract_sha256, s."current", s.check_ready,
        CASE latest.action WHEN 'approve' THEN 'approved' WHEN 'return' THEN 'returned' ELSE 'pending' END
            AS review_status,
        latest.actor_ref AS reviewed_by, latest.reviewed_at, latest.reason AS review_reason
    FROM rm.lean_checks c JOIN rm.lean_statement_status s ON s.id = c.statement_id
    LEFT JOIN LATERAL (
        SELECT r.action, r.actor_ref, r.reviewed_at, r.reason FROM rm.lean_check_reviews r
        WHERE r.check_id = c.id ORDER BY r.sequence_no DESC LIMIT 1
    ) latest ON true;

CREATE FUNCTION rm.lean_artifact_ref_valid(ref jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT coalesce(jsonb_typeof(ref) = 'object'
        AND jsonb_typeof(ref -> 'sha256') = 'string' AND jsonb_typeof(ref -> 'uri') = 'string'
        AND (ref ->> 'sha256') ~ '^[0-9a-f]{64}$'
        AND ref ->> 'uri' = 'urn:sha256:' || (ref ->> 'sha256'), false)
$$;

CREATE FUNCTION rm.propose_lean_statement(packet_id uuid, body jsonb)
RETURNS rm.lean_statements LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE packet formalization_packets;
DECLARE subject claims;
DECLARE prior lean_statements;
DECLARE saved lean_statements;
DECLARE next_version integer;
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body ->> 'theorem' IS DISTINCT FROM 'RMFlow.Expected.realGoal'
        OR jsonb_typeof(body -> 'theorem_type') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'theorem_type'), '') IS NULL
        OR jsonb_typeof(body -> 'scope') IS DISTINCT FROM 'object'
        OR body -> 'scope' = '{}'::jsonb
        OR NOT lean_artifact_ref_valid(body -> 'trusted_source')
        OR jsonb_typeof(body -> 'environment') IS DISTINCT FROM 'object'
        OR jsonb_typeof(body #> '{environment,toolchain}') IS DISTINCT FROM 'string'
        OR body #>> '{environment,toolchain}' IS DISTINCT FROM 'leanprover/lean4:v4.19.0'
        OR jsonb_typeof(body #> '{environment,sha256}') IS DISTINCT FROM 'string'
        OR NOT coalesce(body #>> '{environment,sha256}' ~ '^[0-9a-f]{64}$', false) THEN
        RAISE EXCEPTION 'Lean review needs the expected theorem, exact statement, scope, source and pinned environment'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = propose_lean_statement.packet_id;
    SELECT * INTO subject FROM claims c WHERE c.id = packet.claim_id;
    IF NOT FOUND OR subject.domain <> 'fixed-directed-max-flow' OR subject.sensitivity <> 'public'
        OR subject.contract_sha256 IS NULL OR subject.status NOT IN ('open', 'proved') THEN
        RAISE EXCEPTION 'Lean pilot requires a frozen public fixed-network claim' USING ERRCODE = '23514';
    END IF;
    IF body #>> '{scope,flow_type}' IS DISTINCT FROM 'real'
        OR body #>> '{scope,principal_packet_sha256}' IS DISTINCT FROM packet.packet_sha256
        OR body #>> '{scope,claim_contract_sha256}' IS DISTINCT FROM subject.contract_sha256
        OR body #> '{scope,network}' IS DISTINCT FROM subject.contract_snapshot -> 'small_case_spec' THEN
        RAISE EXCEPTION 'Lean statement scope differs from the frozen research packet' USING ERRCODE = '23514';
    END IF;
    -- Serialize review versions and checks against the existing clarification gate.
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

CREATE FUNCTION rm.require_lean_review(statement_id uuid, for_check boolean) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE packet formalization_packets;
BEGIN
    SELECT * INTO STRICT statement FROM lean_statements s WHERE s.id = require_lean_review.statement_id;
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = statement.packet_id;
    PERFORM require_formalization_ready(statement.claim_id, jsonb_build_object(
        'formalization_id', packet.id, 'formalization_sha256', packet.packet_sha256));
    IF NOT EXISTS (SELECT 1 FROM claims c WHERE c.id = statement.claim_id
                   AND c.contract_sha256 = statement.contract_sha256 AND c.status IN ('open', 'proved')) THEN
        RAISE EXCEPTION 'Lean review requires an active frozen claim' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM lean_statements newer WHERE newer.packet_id = packet.id
                AND newer.version > statement.version) THEN
        RAISE EXCEPTION 'only the current Lean statement version can be used' USING ERRCODE = '23514';
    END IF;
    IF for_check AND NOT EXISTS (SELECT 1 FROM lean_statement_approvals a WHERE a.statement_id = statement.id) THEN
        RAISE EXCEPTION 'the Lean statement still needs researcher approval' USING ERRCODE = '23514';
    END IF;
    RETURN statement;
END $$;

CREATE FUNCTION rm.confirm_lean_statement(statement_id uuid, expected_version integer, expected_sha256 text,
                                          actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE prior lean_statement_approvals;
BEGIN
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'Lean confirmation requires a researcher name and reason' USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM formalization_reviews r JOIN formalization_packets p ON p.review_id = r.id
    JOIN lean_statements s ON s.packet_id = p.id WHERE s.id = confirm_lean_statement.statement_id FOR UPDATE OF r;
    statement := require_lean_review(statement_id, false);
    IF statement.version IS DISTINCT FROM expected_version OR statement.statement_sha256 IS DISTINCT FROM expected_sha256 THEN
        RAISE EXCEPTION 'Lean approval must match the statement version and hash' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO prior FROM lean_statement_approvals a WHERE a.statement_id = statement.id;
    IF FOUND THEN
        IF prior.approved_by <> actor_ref OR prior.authenticated_role <> session_user OR prior.reason <> reason THEN
            RAISE EXCEPTION 'Lean statement already has a different confirmation' USING ERRCODE = '23514';
        END IF;
    ELSE
        INSERT INTO lean_statement_approvals (statement_id, approved_by, reason) VALUES (statement.id, actor_ref, reason);
    END IF;
    RETURN statement.id;
END $$;

CREATE FUNCTION rm.record_lean_check(statement_id uuid, expected_sha256 text, body jsonb)
RETURNS rm.lean_checks LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE saved lean_checks;
DECLARE digest text;
BEGIN
    statement := require_lean_review(statement_id, true);
    IF statement.statement_sha256 IS DISTINCT FROM expected_sha256
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

CREATE FUNCTION rm.review_lean_check(check_id uuid, action text, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
DECLARE recent lean_check_reviews;
DECLARE review_id uuid;
BEGIN
    IF action IS NULL OR action NOT IN ('approve', 'return')
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'Lean review requires approval or return, a researcher name, and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT checked FROM lean_checks c WHERE c.id = review_lean_check.check_id;
    PERFORM 1 FROM formalization_reviews r JOIN formalization_packets p ON p.review_id = r.id
    JOIN lean_statements s ON s.packet_id = p.id WHERE s.id = checked.statement_id FOR UPDATE OF r;
    IF action = 'approve' THEN
        PERFORM require_lean_review(checked.statement_id, true);
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

REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA rm FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_lean_statement(uuid, jsonb),
    rm.confirm_lean_statement(uuid, integer, text, text, text), rm.review_lean_check(uuid, text, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.record_lean_check(uuid, text, jsonb) TO rm_verifier;
GRANT SELECT ON rm.lean_statement_status, rm.lean_check_status TO rm_human, rm_scheduler, rm_verifier;
