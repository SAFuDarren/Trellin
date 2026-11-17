CREATE FUNCTION rm.lean_runtime_current(body jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT CASE
        WHEN body ->> 'theorem' = 'RMTheory.expected'
            AND coalesce(jsonb_typeof(body #> '{environment,external}'), 'null') = 'null'
        THEN coalesce(body #>> '{environment,inputs,native_runtime_policy}' = 'native-lean-v1'
            AND body #>> '{environment,inputs,native_runtime_sha256}' ~ '^[0-9a-f]{64}$'
            AND body #>> '{environment,inputs,compiler}' ~ '^[0-9a-f]{64}$', false)
        ELSE true
    END
$$;

-- The previous standalone environment did not identify the compiled imports
-- or native libraries. Its receipts and descendants use ordinary retirement.
SELECT rm.advance_foundation();
INSERT INTO rm.verification_input_retirements (input_kind, sha256, reason)
    SELECT DISTINCT 'environment', s.body #>> '{environment,sha256}',
           'Standalone Lean Core runtime dependencies require a newly measured check.'
    FROM rm.lean_statements s WHERE NOT rm.lean_runtime_current(s.body)
    ON CONFLICT DO NOTHING;

CREATE OR REPLACE VIEW rm.lean_statement_status WITH (security_barrier = true) AS
    SELECT s.*, a.statement_id IS NOT NULL AS approved, latest.is_current AS "current",
        a.statement_id IS NOT NULL AND latest.is_current AND p.proof_ready
            AND claim.status IN ('open', 'proved') AND rm.lean_runtime_current(s.body) AS check_ready,
        a.approved_by, a.approved_at, a.reason AS approval_reason
    FROM rm.lean_statements s
    JOIN rm.formalization_status p ON p.id = s.packet_id
    JOIN rm.claims claim ON claim.id = s.claim_id
    LEFT JOIN rm.lean_statement_approvals a ON a.statement_id = s.id
    CROSS JOIN LATERAL (
        SELECT NOT EXISTS (SELECT 1 FROM rm.lean_statements newer
                           WHERE newer.packet_id = s.packet_id AND newer.version > s.version) AS is_current
    ) latest;

CREATE FUNCTION rm.guard_lean_runtime_proposal() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT lean_runtime_current(NEW.body) THEN
        RAISE EXCEPTION 'Draft this Lean Core statement with a measured native runtime.' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER require_measured_lean_runtime BEFORE INSERT ON rm.lean_statements
    FOR EACH ROW EXECUTE FUNCTION rm.guard_lean_runtime_proposal();

CREATE OR REPLACE FUNCTION rm.require_lean_review(statement_id uuid, for_check boolean) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE packet formalization_packets;
BEGIN
    SELECT * INTO STRICT statement FROM lean_statements s WHERE s.id = require_lean_review.statement_id;
    IF NOT lean_runtime_current(statement.body) THEN
        RAISE EXCEPTION 'Draft and confirm a new Lean Core statement with a measured native runtime.'
            USING ERRCODE = '23514';
    END IF;
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

DO $$
DECLARE obsolete record;
BEGIN
    FOR obsolete IN
        SELECT job.id FROM rm.jobs job
        JOIN rm.lean_statements statement
          ON statement.id::text = coalesce(job.payload ->> 'lean_statement_id', job.payload ->> 'author_statement_id')
        WHERE job.status IN ('queued', 'running', 'failed') AND job.kind IN ('verify','prove')
          AND NOT rm.lean_runtime_current(statement.body)
        FOR UPDATE OF job
    LOOP
        PERFORM rm.cancel_research_job(obsolete.id, 'schema migration 0040',
            'Recheck under a newly measured standalone Lean Core runtime.');
    END LOOP;
END $$;

REVOKE ALL ON FUNCTION rm.guard_lean_runtime_proposal(), rm.lean_runtime_current(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_runtime_current(jsonb) TO rm_human, rm_scheduler, rm_verifier, rm_agent;
