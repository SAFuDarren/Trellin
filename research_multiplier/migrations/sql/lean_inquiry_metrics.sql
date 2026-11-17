CREATE FUNCTION rm.lean_inquiry_pass_metrics(policy_id uuid)
RETURNS TABLE (completed_passes bigint, timed_passes bigint, worker_elapsed_ms numeric,
               first_recorded_at timestamptz, last_recorded_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT count(*), count(t.elapsed), sum(t.elapsed), min(h.recorded_at), max(h.recorded_at)
    FROM lean_progress_runs h
    CROSS JOIN LATERAL (SELECT CASE WHEN jsonb_typeof(h.result -> 'worker_elapsed_ms') = 'number'
                 THEN (h.result ->> 'worker_elapsed_ms')::numeric END AS elapsed) t
    WHERE h.policy_id = $1
$$;
REVOKE ALL ON FUNCTION rm.lean_inquiry_pass_metrics(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_inquiry_pass_metrics(uuid) TO rm_human;
