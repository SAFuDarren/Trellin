CREATE TABLE rm.lean_progress_worker_claims (
    policy_id uuid PRIMARY KEY REFERENCES rm.research_progress_policies(id),
    inputs_sha256 rm.sha256_hex NOT NULL,
    backend_pid integer NOT NULL
);
CREATE TABLE rm.lean_progress_runtime_signals (
    policy_id uuid PRIMARY KEY REFERENCES rm.research_progress_policies(id),
    stamp rm.sha256_hex NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON rm.lean_progress_worker_claims, rm.lean_progress_runtime_signals FROM PUBLIC;

CREATE FUNCTION rm.lean_progress_watch_targets(policy_ids uuid[])
RETURNS TABLE (id uuid, statement_id uuid, modules jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT policy.id, anchor.id, anchor.body #> '{environment,external,modules}'
    FROM research_progress_status policy
    JOIN lean_statement_status anchor ON anchor.id::text = policy.policy #>> '{lean,statement_id}'
        AND anchor.statement_sha256 = policy.policy #>> '{lean,statement_sha256}' AND anchor.check_ready
    WHERE policy.state = 'awaiting_evidence' AND policy.policy ? 'lean'
      AND anchor.body #> '{environment,external,modules}' IS NOT NULL
      AND ($1 IS NULL OR policy.id = ANY($1))
$$;

CREATE FUNCTION rm.record_lean_progress_runtime_signal(policy_id uuid, stamp text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM lean_progress_watch_targets(ARRAY[$1]))
       OR NOT coalesce($2 ~ '^[a-f0-9]{64}$', false) THEN
        RETURN false;
    END IF;
    INSERT INTO lean_progress_runtime_signals AS signal(policy_id, stamp)
        VALUES ($1, $2) ON CONFLICT ON CONSTRAINT lean_progress_runtime_signals_pkey
        DO UPDATE SET stamp = excluded.stamp, observed_at = clock_timestamp()
        WHERE signal.stamp <> excluded.stamp;
    RETURN FOUND;
END $$;

ALTER FUNCTION rm.lean_progress_inputs(uuid) RENAME TO lean_progress_inputs_before_runtime_signal;
-- Recreate the caller when its name also appeared in a previously cached SQL plan.
CREATE FUNCTION rm.lean_progress_inputs(policy_id uuid)
RETURNS TABLE (statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT p.statement_id, p.model_rounds,
           CASE WHEN signal.stamp IS NULL AND budget.epoch IS NULL THEN p.inputs_sha256
                ELSE encode(sha256(convert_to(jsonb_build_array(p.inputs_sha256, signal.stamp, budget.epoch)::text,
                                               'UTF8')), 'hex') END
    FROM lean_progress_inputs_before_runtime_signal($1) p
    LEFT JOIN lean_progress_runtime_signals signal ON signal.policy_id = $1
    CROSS JOIN LATERAL (SELECT CASE WHEN p.model_rounds > 0 THEN project_model_epoch() END AS epoch) budget
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

CREATE FUNCTION rm.owns_lean_progress_lock(policy_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM pg_locks l, hashtextextended($1::text, 4801) AS key(value)
        WHERE l.locktype = 'advisory' AND l.granted AND l.pid = pg_backend_pid() AND l.objsubid = 1
          AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND l.classid::bigint = (key.value >> 32 & 4294967295::bigint)
          AND l.objid::bigint = (key.value & 4294967295::bigint)
    )
$$;

CREATE OR REPLACE FUNCTION rm.lock_lean_progress(policy_id uuid, inputs_sha256 text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE lock_key bigint := hashtextextended($1::text, 4801);
BEGIN
    IF owns_lean_progress_lock($1) OR NOT pg_try_advisory_lock(lock_key) THEN
        RETURN false;
    END IF;
    IF EXISTS (SELECT FROM lean_progress_inputs($1) i WHERE i.inputs_sha256 = $2)
        AND NOT EXISTS (SELECT FROM lean_progress_runs done WHERE done.policy_id = $1 AND done.inputs_sha256 = $2)
    THEN
        INSERT INTO lean_progress_worker_claims(policy_id, inputs_sha256, backend_pid)
        VALUES ($1, $2, pg_backend_pid()) ON CONFLICT ON CONSTRAINT lean_progress_worker_claims_pkey
        DO UPDATE SET inputs_sha256 = excluded.inputs_sha256, backend_pid = excluded.backend_pid;
        RETURN true;
    END IF;
    PERFORM pg_advisory_unlock(lock_key);
    RETURN false;
EXCEPTION WHEN OTHERS THEN
    PERFORM pg_advisory_unlock(lock_key);
    RAISE;
END $$;

CREATE OR REPLACE FUNCTION rm.unlock_lean_progress(policy_id uuid) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    DELETE FROM lean_progress_worker_claims claimed
    WHERE claimed.policy_id = $1 AND claimed.backend_pid = pg_backend_pid();
    RETURN pg_advisory_unlock(hashtextextended($1::text, 4801));
END $$;

CREATE OR REPLACE FUNCTION rm.finish_lean_progress(policy_id uuid, inputs_sha256 text, result jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT owns_lean_progress_lock($1) OR NOT EXISTS (
        SELECT FROM lean_progress_worker_claims claimed
        WHERE claimed.policy_id = $1 AND claimed.inputs_sha256 = $2 AND claimed.backend_pid = pg_backend_pid()
    ) OR jsonb_typeof($3) IS DISTINCT FROM 'object'
        OR NOT $3 ? 'status' OR octet_length($3::text) > 64000 THEN
        RAISE EXCEPTION 'a Lean advancement observation needs the exact claimed run and a bounded result'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_progress_runs (policy_id, inputs_sha256, result) VALUES ($1, $2, $3)
    ON CONFLICT ON CONSTRAINT lean_progress_runs_pkey DO NOTHING;
END $$;

REVOKE ALL ON FUNCTION rm.lean_progress_inputs(uuid), rm.owns_lean_progress_lock(uuid),
    rm.lean_progress_watch_targets(uuid[]), rm.record_lean_progress_runtime_signal(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_progress_watch_targets(uuid[]), rm.record_lean_progress_runtime_signal(uuid,text)
    TO rm_scheduler;
