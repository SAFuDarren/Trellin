-- Observations request a review. Only executable development cases can be claimed.
CREATE TABLE rm.lean_rechecks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL CHECK (kind IN ('development_replay', 'monthly_review', 'workflow_blocked',
                                      'budget_pressure', 'model_observed', 'implementation_changed', 'benchmark_plateau')),
    signal_key text NOT NULL UNIQUE,
    implementation rm.sha256_hex NOT NULL,
    origin_database uuid NOT NULL,
    origin_workspace uuid NOT NULL,
    regression_id uuid REFERENCES rm.incident_regressions(id),
    detail jsonb NOT NULL CHECK (jsonb_typeof(detail) = 'object'),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((kind = 'development_replay') = (regression_id IS NOT NULL))
);
CREATE INDEX lean_rechecks_workers ON rm.lean_rechecks(implementation, observed_at, id)
    WHERE kind = 'development_replay';
CREATE INDEX lean_recheck_recent_budget_exhaustion ON rm.lean_progress_runs(recorded_at, policy_id)
    WHERE result ->> 'status' = 'budget_exhausted';
CREATE INDEX lean_recheck_recent_model_attempts ON rm.attempts(submitted_at, job_id)
    WHERE role = 'prove' AND model_provider IS NOT NULL;
CREATE TRIGGER lean_rechecks_append_only BEFORE UPDATE OR DELETE ON rm.lean_rechecks
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE TABLE rm.lean_recheck_attempts (
    recheck_id uuid NOT NULL REFERENCES rm.lean_rechecks(id),
    generation integer NOT NULL CHECK (generation > 0),
    status text NOT NULL CHECK (status IN ('passed', 'failed', 'inconclusive')),
    report_sha256 rm.sha256_hex NOT NULL,
    verification_attempt_id uuid REFERENCES rm.attempts(id),
    finished_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (recheck_id, generation),
    CHECK (status = 'inconclusive' OR verification_attempt_id IS NOT NULL)
);
CREATE TRIGGER lean_recheck_attempts_append_only BEFORE UPDATE OR DELETE ON rm.lean_recheck_attempts
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE VIEW rm.lean_recheck_status WITH (security_barrier = true) AS
    SELECT q.*, latest.generation, latest.report_sha256, latest.verification_attempt_id, latest.finished_at,
           CASE WHEN q.regression_id IS NULL THEN 'review_signal' ELSE coalesce(latest.status, 'queued') END AS status
    FROM rm.lean_rechecks q
    LEFT JOIN LATERAL (
        SELECT a.* FROM rm.lean_recheck_attempts a WHERE a.recheck_id = q.id ORDER BY a.generation DESC LIMIT 1
    ) latest ON true;

CREATE FUNCTION rm.scan_lean_rechecks(implementation rm.sha256_hex, fixture_limit integer)
RETURNS SETOF rm.lean_rechecks
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE bound record;
DECLARE now_utc timestamptz;
DECLARE month_key text;
BEGIN
    IF $1 IS NULL OR NOT coalesce($2 BETWEEN 1 AND 128, false) THEN
        RAISE EXCEPTION 'a Lean evaluation scan needs an implementation and a bounded fixture batch' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO STRICT bound FROM current_workspace_binding();
    IF bound.workspace_id IS NULL THEN
        RAISE EXCEPTION 'bind the Lean evaluation workspace before scanning' USING ERRCODE = '23514';
    END IF;
    now_utc := date_trunc('month', statement_timestamp(), 'UTC');
    month_key := to_char(now_utc AT TIME ZONE 'UTC', 'YYYY-MM');

    RETURN QUERY
    WITH fixtures AS (
        SELECT r.id, r.artifact_sha256 FROM incident_regressions r
        WHERE r.test_case = 'lean-structured-verification'
          AND NOT EXISTS (SELECT FROM lean_rechecks q
              WHERE q.signal_key = 'fixture:' || $1 || ':' || r.id)
        ORDER BY r.recorded_at, r.id LIMIT $2
    ), models AS (
        SELECT DISTINCT jsonb_build_object('provider', a.model_provider, 'model_id', a.model_id,
                                            'revision', a.model_revision, 'family', a.model_family) AS identity
        FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.submitted_at >= now_utc AND a.role = 'prove' AND a.model_provider IS NOT NULL
          AND (j.payload -> 'lean_search' = 'true'::jsonb OR j.payload -> 'lean_author' = 'true'::jsonb)
    ), pressure AS (
        SELECT p.claim_id, count(*) AS passes FROM lean_progress_runs h
        JOIN research_progress_policies p ON p.id = h.policy_id
        WHERE h.recorded_at >= now_utc AND h.result ->> 'status' = 'budget_exhausted'
        GROUP BY p.claim_id HAVING count(*) >= 3
    ), observations(kind, signal_key, detail, regression_id) AS (
        SELECT 'implementation_changed', 'implementation:' || $1,
               jsonb_build_object('implementation', $1), NULL::uuid
        UNION ALL SELECT 'monthly_review', 'monthly:' || month_key,
                         jsonb_build_object('month_utc', month_key), NULL::uuid
        UNION ALL SELECT 'workflow_blocked', 'workflow:' || i.id,
                         jsonb_build_object('incident_id', i.id), NULL::uuid
            FROM verifier_incident_status i WHERE i.kind = 'workflow_blocked' AND i.status <> 'dismiss'
        UNION ALL SELECT 'model_observed', 'model:' || encode(sha256(convert_to(m.identity::text, 'UTF8')), 'hex'),
                         m.identity, NULL::uuid FROM models m
        UNION ALL SELECT 'budget_pressure', 'budget:' || month_key || ':' || p.claim_id,
                         jsonb_build_object('claim_id', p.claim_id, 'month_utc', month_key, 'budget_exhausted_passes', p.passes),
                         NULL::uuid FROM pressure p
        UNION ALL SELECT 'development_replay', 'fixture:' || $1 || ':' || f.id,
                         jsonb_build_object('fixture_sha256', f.artifact_sha256), f.id FROM fixtures f
    )
    INSERT INTO lean_rechecks(kind, signal_key, implementation, origin_database, origin_workspace, detail, regression_id)
        SELECT o.kind, o.signal_key, $1, bound.database_id, bound.workspace_id, o.detail, o.regression_id
        FROM observations o ORDER BY o.kind, o.signal_key
        ON CONFLICT (signal_key) DO NOTHING RETURNING *;
END $$;

CREATE FUNCTION rm.claim_lean_recheck(implementation rm.sha256_hex, retry_before timestamptz)
RETURNS TABLE (id uuid, regression_id uuid, generation integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE claimed lean_rechecks;
DECLARE current_attempt lean_recheck_attempts;
BEGIN
    SELECT q.* INTO claimed
    FROM lean_rechecks q CROSS JOIN current_workspace_binding() bound
    LEFT JOIN LATERAL (
        SELECT a.status, a.finished_at FROM lean_recheck_attempts a
        WHERE a.recheck_id = q.id ORDER BY a.generation DESC LIMIT 1
    ) latest ON true
    WHERE q.kind = 'development_replay' AND q.implementation = $1
      AND bound.database_id <> q.origin_database AND bound.workspace_id <> q.origin_workspace
      AND (latest.status IS NULL OR (latest.status = 'inconclusive' AND latest.finished_at < $2))
    ORDER BY q.observed_at, q.id LIMIT 1 FOR UPDATE OF q SKIP LOCKED;
    IF NOT FOUND THEN RETURN; END IF;

    -- The first statement could race a commit just before acquiring the row
    -- lock. Read completion in a fresh snapshot before executing anything.
    SELECT * INTO current_attempt FROM lean_recheck_attempts a WHERE a.recheck_id = claimed.id
        ORDER BY a.generation DESC LIMIT 1;
    IF NOT FOUND OR (current_attempt.status = 'inconclusive' AND current_attempt.finished_at < $2) THEN
        RETURN QUERY SELECT claimed.id, claimed.regression_id, coalesce(current_attempt.generation, 0) + 1;
    END IF;
END $$;

CREATE FUNCTION rm.finish_lean_recheck(id uuid, generation integer, status text, report_sha256 rm.sha256_hex,
                                     verification_attempt_id uuid)
RETURNS SETOF rm.lean_recheck_attempts
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE request lean_rechecks;
DECLARE previous lean_recheck_attempts;
BEGIN
    SELECT q.* INTO request FROM lean_rechecks q CROSS JOIN current_workspace_binding() bound
        WHERE q.id = $1 AND q.kind = 'development_replay' AND bound.database_id <> q.origin_database
          AND bound.workspace_id <> q.origin_workspace FOR UPDATE OF q;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'run this Lean regression in a separate restored workspace' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO previous FROM lean_recheck_attempts a WHERE a.recheck_id = $1 ORDER BY a.generation DESC LIMIT 1;
    IF previous.status IN ('passed', 'failed') OR $2 IS DISTINCT FROM coalesce(previous.generation, 0) + 1 THEN
        RAISE EXCEPTION 'the selected Lean regression attempt changed' USING ERRCODE = '23514';
    END IF;
    IF $5 IS NOT NULL AND NOT EXISTS (
        SELECT FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = $5 AND a.role = 'verify' AND a.model_provider IS NULL
          AND j.budget -> 'max_model_calls' = '0'::jsonb
          AND starts_with(j.operation_key, 'v0a-2:lean-regression:' || $1 || ':' || $2 || ':')
    ) THEN
        RAISE EXCEPTION 'a Lean regression result needs its own zero-model verification attempt' USING ERRCODE = '23514';
    END IF;
    RETURN QUERY INSERT INTO lean_recheck_attempts(recheck_id, generation, status, report_sha256, verification_attempt_id)
        VALUES ($1, $2, $3, $4, $5) RETURNING *;
END $$;

REVOKE ALL ON rm.lean_rechecks, rm.lean_recheck_attempts, rm.lean_recheck_status FROM PUBLIC;
GRANT SELECT ON rm.lean_recheck_status TO rm_human;
REVOKE ALL ON FUNCTION rm.scan_lean_rechecks(rm.sha256_hex, integer), rm.claim_lean_recheck(rm.sha256_hex, timestamptz),
    rm.finish_lean_recheck(uuid, integer, text, rm.sha256_hex, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.scan_lean_rechecks(rm.sha256_hex, integer), rm.claim_lean_recheck(rm.sha256_hex, timestamptz),
    rm.finish_lean_recheck(uuid, integer, text, rm.sha256_hex, uuid) TO rm_verifier;

CREATE TABLE rm.lean_benchmark_observations (
    report_sha256 rm.sha256_hex PRIMARY KEY,
    corpus_sha256 rm.sha256_hex NOT NULL REFERENCES rm.lean_eval_corpora(sha256),
    series_sha256 rm.sha256_hex NOT NULL,
    candidate_sha256 rm.sha256_hex NOT NULL,
    exact integer NOT NULL CHECK (exact >= 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX lean_benchmark_observations_series ON rm.lean_benchmark_observations(series_sha256, recorded_at DESC);
CREATE TRIGGER lean_benchmark_observations_append_only BEFORE UPDATE OR DELETE ON rm.lean_benchmark_observations
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
REVOKE ALL ON rm.lean_benchmark_observations FROM PUBLIC;

CREATE FUNCTION rm.record_lean_benchmark(report_sha rm.sha256_hex, corpus_sha rm.sha256_hex, series rm.sha256_hex,
                                         candidate rm.sha256_hex, exact_count integer, implementation rm.sha256_hex)
RETURNS SETOF rm.lean_rechecks
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE prior lean_benchmark_observations;
DECLARE bound record;
DECLARE month_key text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended($3::text, 810324));
    IF NOT EXISTS (SELECT FROM lean_eval_corpora c WHERE c.sha256 = $2
                         AND $5 BETWEEN 0 AND jsonb_array_length(c.body -> 'development')) THEN
        RAISE EXCEPTION 'the benchmark needs a sealed development corpus and bounded outcome count' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO STRICT bound FROM current_workspace_binding();
    INSERT INTO lean_benchmark_observations(report_sha256, corpus_sha256, series_sha256, candidate_sha256, exact)
        VALUES ($1,$2,$3,$4,$5) ON CONFLICT (report_sha256) DO NOTHING;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT * INTO prior FROM lean_benchmark_observations o WHERE o.series_sha256 = $3 AND o.corpus_sha256 = $2
        AND o.candidate_sha256 <> $4 ORDER BY o.recorded_at DESC, o.report_sha256 LIMIT 1;
    IF NOT FOUND OR $5 > prior.exact THEN RETURN; END IF;
    month_key := to_char(statement_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM');
    RETURN QUERY INSERT INTO lean_rechecks(kind, signal_key, implementation, origin_database, origin_workspace, detail)
        VALUES ('benchmark_plateau', 'benchmark:' || month_key || ':' || $3, $6, bound.database_id, bound.workspace_id,
                jsonb_build_object('corpus_sha256', $2, 'previous_report_sha256', prior.report_sha256,
                                   'report_sha256', $1, 'previous_exact', prior.exact, 'exact', $5))
        ON CONFLICT (signal_key) DO NOTHING RETURNING *;
END $$;
REVOKE ALL ON FUNCTION rm.record_lean_benchmark(rm.sha256_hex, rm.sha256_hex, rm.sha256_hex,
    rm.sha256_hex, integer, rm.sha256_hex) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_lean_benchmark(rm.sha256_hex, rm.sha256_hex, rm.sha256_hex,
    rm.sha256_hex, integer, rm.sha256_hex) TO rm_verifier;
