-- Derived statuses can change without an application write. Let the workbench
-- schedule those transitions from authoritative deadlines without polling its views.
CREATE INDEX workbench_job_lease_deadline ON rm.jobs (lease_until) WHERE status = 'running';
CREATE INDEX workbench_job_retry_deadline ON rm.jobs (ready_at) WHERE status = 'queued';

CREATE FUNCTION rm.workbench_runtime_clock(target_workspace uuid)
RETURNS TABLE (
    observed_at timestamptz, next_at timestamptz, month date,
    leased_jobs uuid[], waiting_jobs uuid[], lean_workers uuid[]
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_workspace_event_reader(target_workspace);
    RETURN QUERY
    WITH moment AS (
        SELECT statement_timestamp() AS observed,
               date_trunc('month', statement_timestamp() AT TIME ZONE 'UTC') AS period
    ), leased AS MATERIALIZED (
        SELECT id, lease_until FROM jobs
        WHERE status = 'running' AND lease_until > statement_timestamp()
    ), waiting AS MATERIALIZED (
        SELECT id, ready_at FROM jobs
        WHERE status = 'queued' AND ready_at > statement_timestamp()
    ), workers AS (
        SELECT claimed.policy_id
        FROM lean_progress_worker_claims claimed
        JOIN pg_locks active ON active.pid = claimed.backend_pid
          AND active.locktype = 'advisory' AND active.granted AND active.objsubid = 1
        CROSS JOIN LATERAL (SELECT hashtextextended(claimed.policy_id::text, 4801) AS value) AS key
        WHERE active.database = (SELECT oid FROM pg_database WHERE datname = current_database())
          AND active.classid::bigint = (key.value >> 32 & 4294967295::bigint)
          AND active.objid::bigint = (key.value & 4294967295::bigint)
    )
    SELECT observed, least(
               (period + interval '1 month') AT TIME ZONE 'UTC',
               (SELECT min(lease_until) FROM leased),
               (SELECT min(ready_at) FROM waiting)
           ), period::date,
           ARRAY(SELECT id FROM leased ORDER BY id),
           ARRAY(SELECT id FROM waiting ORDER BY id),
           ARRAY(SELECT policy_id FROM workers ORDER BY policy_id)
    FROM moment;
END $$;

REVOKE ALL ON FUNCTION rm.workbench_runtime_clock(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.workbench_runtime_clock(uuid) TO rm_human;
