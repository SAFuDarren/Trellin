ALTER TABLE rm.lean_progress_worker_claims
    ADD COLUMN inquiry_generation rm.sha256_hex,
    ADD COLUMN continuation jsonb;
CREATE INDEX lean_progress_inquiry_history
    ON rm.lean_progress_runs(policy_id, (result #>> '{inquiry,generation}'), recorded_at DESC, inputs_sha256);
ALTER FUNCTION rm.lean_progress_inputs(uuid) RENAME TO lean_progress_inputs_before_inquiry;

-- Discovered local facts can wake the worker. They do not refill the allowance
-- for an inquiry under the same confirmed basis and observed Lean runtime.
CREATE FUNCTION rm.lean_inquiry_generation(policy_id uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT encode(sha256(convert_to(jsonb_build_array(policy.policy_sha256, basis.ids, signal.stamp)::text, 'UTF8')), 'hex')
    FROM research_progress_policies policy
    LEFT JOIN lean_progress_runtime_signals signal ON signal.policy_id = policy.id
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(b.item_version_id ORDER BY b.item_version_id), '[]'::jsonb) AS ids
        FROM get_packet_basis(policy.packet_id) b WHERE b.body ->> 'format' IS DISTINCT FROM 'lean-native-proof'
    ) basis
    WHERE policy.id = $1 AND (policy.policy #>> '{lean,model_rounds}')::integer > 0
$$;

CREATE FUNCTION rm.lean_inquiry_base(policy_id uuid)
RETURNS TABLE (statement_id uuid, model_rounds integer, base_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT p.statement_id, p.model_rounds, CASE WHEN p.model_rounds = 0 THEN p.inputs_sha256
        ELSE encode(sha256(convert_to(jsonb_build_array(p.inputs_sha256, coalesce(budget.call_limit, 8))::text,
                                        'UTF8')), 'hex') END
    FROM lean_progress_inputs_before_inquiry($1) p
    JOIN research_progress_policies policy ON policy.id = $1
    LEFT JOIN lean_search_budgets budget
        ON (budget.claim_id, budget.contract_sha256) = (policy.claim_id, policy.contract_sha256)
$$;

CREATE FUNCTION rm.lean_progress_ready(policy_id uuid)
RETURNS TABLE (statement_id uuid, model_rounds integer, inputs_sha256 text,
               inquiry_generation text, continuation jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT b.statement_id, b.model_rounds,
           CASE WHEN g.generation IS NULL THEN b.base_sha256
                ELSE encode(sha256(convert_to(jsonb_build_array('inquiry-v1', g.generation, b.base_sha256,
                                                               previous.inputs_sha256)::text, 'UTF8')), 'hex') END,
           g.generation, previous.result -> 'record'
    FROM lean_inquiry_base($1) b
    CROSS JOIN LATERAL (SELECT CASE WHEN b.model_rounds > 0 THEN lean_inquiry_generation($1) END AS generation) g
    LEFT JOIN LATERAL (
        SELECT result, inputs_sha256 FROM lean_progress_runs r
        WHERE r.policy_id = $1 AND r.result #>> '{inquiry,generation}' = g.generation
        ORDER BY r.recorded_at DESC, r.inputs_sha256 DESC LIMIT 1
    ) previous ON true
    WHERE previous.inputs_sha256 IS NULL OR previous.result ->> 'status' = 'search_continues'
          OR previous.result #>> '{inquiry,observed_input}' IS DISTINCT FROM b.base_sha256
$$;

CREATE FUNCTION rm.lean_progress_inputs(policy_id uuid)
RETURNS TABLE (statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT r.statement_id, r.model_rounds, r.inputs_sha256 FROM lean_progress_ready($1) r
$$;

CREATE OR REPLACE FUNCTION rm.lean_progress_queue(batch integer, policy_ids uuid[])
RETURNS TABLE (id uuid, statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT policy.id, inputs.* FROM research_progress_status policy
    CROSS JOIN LATERAL lean_progress_inputs(policy.id) inputs
    WHERE policy.policy ? 'lean' AND policy.state = 'awaiting_evidence'
      AND ($2 IS NULL OR policy.id = ANY($2))
      AND NOT EXISTS (SELECT FROM lean_progress_runs completed
                       WHERE completed.policy_id = policy.id AND completed.inputs_sha256 = inputs.inputs_sha256)
    ORDER BY (policy.policy ->> 'priority')::integer DESC, policy.approved_at, policy.id
    LIMIT greatest(0, least($1, 64))
$$;

CREATE OR REPLACE FUNCTION rm.lock_lean_progress(policy_id uuid, inputs_sha256 text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE lock_key bigint := hashtextextended($1::text, 4801);
DECLARE ready record;
BEGIN
    IF owns_lean_progress_lock($1) OR NOT pg_try_advisory_lock(lock_key) THEN
        RETURN false;
    END IF;
    SELECT * INTO ready FROM lean_progress_ready($1) r WHERE r.inputs_sha256 = $2;
    IF FOUND AND NOT EXISTS (SELECT FROM lean_progress_runs done WHERE done.policy_id = $1 AND done.inputs_sha256 = $2) THEN
        INSERT INTO lean_progress_worker_claims(policy_id, inputs_sha256, backend_pid, inquiry_generation, continuation)
        VALUES ($1, $2, pg_backend_pid(), ready.inquiry_generation, ready.continuation)
        ON CONFLICT ON CONSTRAINT lean_progress_worker_claims_pkey DO UPDATE SET
            inputs_sha256 = excluded.inputs_sha256, backend_pid = excluded.backend_pid,
            inquiry_generation = excluded.inquiry_generation, continuation = excluded.continuation;
        RETURN true;
    END IF;
    PERFORM pg_advisory_unlock(lock_key);
    RETURN false;
EXCEPTION WHEN OTHERS THEN
    PERFORM pg_advisory_unlock(lock_key);
    RAISE;
END $$;

CREATE FUNCTION rm.lean_progress_continuation(policy_id uuid, inputs_sha256 text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT c.continuation FROM lean_progress_worker_claims c
    WHERE c.policy_id = $1 AND c.inputs_sha256 = $2 AND c.backend_pid = pg_backend_pid()
       AND owns_lean_progress_lock($1)
$$;

CREATE OR REPLACE FUNCTION rm.finish_lean_progress(policy_id uuid, inputs_sha256 text, result jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE claimed lean_progress_worker_claims;
DECLARE observed text;
DECLARE saved jsonb := $3 - 'inquiry';
BEGIN
    SELECT * INTO claimed FROM lean_progress_worker_claims c
    WHERE c.policy_id = $1 AND c.inputs_sha256 = $2 AND c.backend_pid = pg_backend_pid();
    IF NOT FOUND OR NOT owns_lean_progress_lock($1) OR jsonb_typeof($3) IS DISTINCT FROM 'object'
        OR NOT $3 ? 'status' OR octet_length($3::text) > 64000
        OR ($3 ->> 'status' = 'search_continues' AND (claimed.inquiry_generation IS NULL
            OR NOT coalesce(lean_artifact_ref_valid($3 -> 'record'), false)
            OR $3 -> 'record' IS NOT DISTINCT FROM claimed.continuation)) THEN
        RAISE EXCEPTION 'a Lean advancement observation needs the exact claimed run and a bounded result'
            USING ERRCODE = '23514';
    END IF;
    IF claimed.inquiry_generation IS NOT NULL THEN
        SELECT b.base_sha256 INTO observed FROM lean_inquiry_base($1) b;
        saved := saved || jsonb_build_object('inquiry', jsonb_build_object('generation', claimed.inquiry_generation,
                                                                         'observed_input', observed));
    END IF;
    INSERT INTO lean_progress_runs (policy_id, inputs_sha256, result) VALUES ($1, $2, saved)
    ON CONFLICT ON CONSTRAINT lean_progress_runs_pkey DO NOTHING;
END $$;

CREATE FUNCTION rm.lean_progress_history(policy_id uuid, count_limit integer)
RETURNS TABLE (result jsonb, recorded_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT h.result, h.recorded_at FROM lean_progress_runs h WHERE h.policy_id = $1
    ORDER BY h.recorded_at DESC, h.inputs_sha256 DESC LIMIT greatest(0, least($2, 32))
$$;

REVOKE ALL ON FUNCTION rm.lean_inquiry_generation(uuid), rm.lean_inquiry_base(uuid), rm.lean_progress_ready(uuid),
    rm.lean_progress_inputs(uuid), rm.lean_progress_continuation(uuid,text), rm.lean_progress_history(uuid,integer)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_progress_continuation(uuid,text) TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.lean_progress_history(uuid,integer) TO rm_human;
