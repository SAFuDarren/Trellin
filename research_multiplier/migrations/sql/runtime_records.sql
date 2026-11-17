ALTER TABLE rm.jobs
    ADD COLUMN ready_at timestamptz,
    ADD COLUMN leased_at timestamptz,
    ADD COLUMN result_reuses bigint NOT NULL DEFAULT 0 CHECK (result_reuses >= 0);
UPDATE rm.jobs SET ready_at = enqueued_at;
ALTER TABLE rm.jobs ALTER COLUMN ready_at SET NOT NULL,
    ALTER COLUMN ready_at SET DEFAULT clock_timestamp();

CREATE FUNCTION rm.stamp_research_job() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.lease_generation > OLD.lease_generation THEN
        NEW.leased_at := clock_timestamp();
        IF OLD.status = 'running' THEN
            NEW.ready_at := OLD.lease_until;
        END IF;
    ELSIF OLD.status = 'failed' AND NEW.status = 'queued' THEN
        NEW.ready_at := clock_timestamp();
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER stamp_research_job BEFORE UPDATE ON rm.jobs
    FOR EACH ROW EXECUTE FUNCTION rm.stamp_research_job();

CREATE TABLE rm.attempt_measurements (
    attempt_id uuid PRIMARY KEY REFERENCES rm.attempts(id),
    execution_ms bigint NOT NULL CHECK (execution_ms >= 0),
    queue_ms bigint CHECK (queue_ms >= 0),
    failure_code text,
    retryable boolean,
    runtime_version text NOT NULL DEFAULT 'research-foundation-1',
    submitted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE rm.job_cancellations (
    job_id uuid PRIMARY KEY REFERENCES rm.jobs(id),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    cancelled_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER measure_append_only BEFORE UPDATE OR DELETE ON rm.attempt_measurements
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER cancel_append_only BEFORE UPDATE OR DELETE ON rm.job_cancellations
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.submit_measured_attempt(job_id uuid, worker text, generation int, body jsonb, metrics jsonb)
RETURNS rm.attempts LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target jobs;
DECLARE saved attempts;
BEGIN
    SELECT * INTO STRICT target FROM jobs j WHERE j.id = submit_measured_attempt.job_id FOR UPDATE;
    IF (target.kind = 'verify' AND NOT pg_has_role(session_user, 'rm_verifier', 'USAGE'))
        OR (target.kind <> 'verify' AND NOT pg_has_role(session_user, 'rm_agent', 'USAGE')) THEN
        RAISE EXCEPTION 'submission requires the designated execution role' USING ERRCODE = '42501';
    END IF;
    saved := submit_attempt(job_id, worker, generation, body);
    INSERT INTO attempt_measurements(attempt_id, execution_ms, queue_ms, failure_code, retryable)
    VALUES (saved.id, (metrics ->> 'execution_ms')::bigint,
        CASE WHEN target.leased_at IS NOT NULL THEN
            greatest(0, floor(extract(epoch FROM target.leased_at - target.ready_at) * 1000)::bigint)
        END,
        metrics ->> 'failure_code', (metrics ->> 'retryable')::boolean)
    ON CONFLICT (attempt_id) DO NOTHING;
    RETURN saved;
END
$$;

CREATE FUNCTION rm.cancel_research_job(job_id uuid, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target jobs;
BEGIN
    SELECT * INTO STRICT target FROM jobs j WHERE j.id = cancel_research_job.job_id FOR UPDATE;
    IF target.status = 'cancelled' AND EXISTS (SELECT 1 FROM job_cancellations c WHERE c.job_id = target.id) THEN
        RETURN target.id;
    END IF;
    IF target.status NOT IN ('queued', 'running', 'failed') THEN
        RAISE EXCEPTION 'only unfinished tasks can be cancelled' USING ERRCODE = '23514';
    END IF;
    INSERT INTO job_cancellations(job_id, actor_ref, reason)
    VALUES (target.id, cancel_research_job.actor_ref, cancel_research_job.reason);
    UPDATE jobs SET status = 'cancelled', lease_owner = NULL, lease_until = NULL WHERE id = target.id;
    RETURN target.id;
END
$$;

CREATE FUNCTION rm.record_job_reuse(job_id uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    UPDATE jobs SET result_reuses = result_reuses + 1
    WHERE id = job_id AND status = 'succeeded'
$$;

CREATE TABLE rm.human_time_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL REFERENCES rm.claims(id),
    activity text NOT NULL CHECK (activity IN ('clarification', 'review')),
    minutes numeric(10,2) NOT NULL CHECK (minutes > 0),
    logged_by name NOT NULL DEFAULT session_user,
    logged_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER human_time_append_only BEFORE UPDATE OR DELETE ON rm.human_time_entries
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.log_human_time(claim_id uuid, activity text, minutes numeric) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    INSERT INTO human_time_entries(claim_id, activity, minutes) VALUES ($1, $2, $3) RETURNING id
$$;

CREATE VIEW rm.research_task_status WITH (security_barrier = true) AS
SELECT j.id, j.claim_id, j.kind, j.operation_key, j.status, j.enqueued_at, j.lease_generation,
       a.id AS attempt_id, a.finding, a.input_tokens, a.output_tokens, a.cost_usd,
       m.execution_ms, m.queue_ms, m.failure_code, m.retryable, m.runtime_version,
       c.cancelled_at, c.reason AS cancellation_reason, j.result_reuses
FROM rm.jobs j
LEFT JOIN rm.attempts a ON a.job_id = j.id AND a.lease_generation = j.lease_generation
LEFT JOIN rm.attempt_measurements m ON m.attempt_id = a.id
LEFT JOIN rm.job_cancellations c ON c.job_id = j.id;

REVOKE EXECUTE ON FUNCTION rm.stamp_research_job(), rm.cancel_research_job(uuid, text, text),
    rm.submit_measured_attempt(uuid, text, integer, jsonb, jsonb),
    rm.record_job_reuse(uuid), rm.log_human_time(uuid, text, numeric) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.cancel_research_job(uuid, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.submit_measured_attempt(uuid, text, integer, jsonb, jsonb) TO rm_agent, rm_verifier;
GRANT SELECT ON rm.research_task_status TO rm_human, rm_scheduler, rm_verifier;
GRANT EXECUTE ON FUNCTION rm.record_job_reuse(uuid) TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.log_human_time(uuid, text, numeric) TO rm_human;
GRANT SELECT ON rm.human_time_entries TO rm_human;

INSERT INTO rm.harness_components(name, version, layer, purpose, metric, enabled)
VALUES
  ('research-basis','1','trust','Track approved versions and transitive evidence validity',
   '{"measure":"eligible_basis_versions"}',true),
  ('lean-candidate-audit','1','trust','Check goal type, axioms and actual proof references',
   '{"measure":"verification_results_by_checker"}',true),
  ('durable-research-runtime','1','trust','Fence task leases, cancellation and repeated results',
   '{"measure":"result_reuses"}',true)
ON CONFLICT DO NOTHING;
GRANT SELECT ON rm.harness_components TO rm_human;
