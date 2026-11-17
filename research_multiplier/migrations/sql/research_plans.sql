CREATE TABLE rm.research_plans (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 160),
    body_sha256 rm.sha256_hex UNIQUE NOT NULL,
    max_parallel integer NOT NULL CHECK (max_parallel BETWEEN 1 AND 8),
    budget_seconds integer NOT NULL CHECK (budget_seconds BETWEEN 1 AND 36000),
    created_by name NOT NULL DEFAULT session_user,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    cancelled_at timestamptz,
    cancelled_by name,
    cancellation_reason text
);

CREATE TABLE rm.research_plan_steps (
    plan_id uuid NOT NULL REFERENCES rm.research_plans(id),
    step_key text NOT NULL CHECK (step_key ~ '^[a-z][a-z0-9_-]{0,47}$'),
    action text NOT NULL CHECK (action IN ('prove', 'await-review', 'snapshot')),
    claim_id uuid NOT NULL,
    contract_sha256 rm.sha256_hex NOT NULL,
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    packet_sha256 rm.sha256_hex NOT NULL,
    priority integer NOT NULL CHECK (priority BETWEEN -100 AND 100),
    timeout_seconds integer NOT NULL CHECK (timeout_seconds BETWEEN 1 AND 240),
    max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 4),
    generation integer NOT NULL DEFAULT 0,
    ready_after timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_owner uuid,
    lease_until timestamptz,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'returned', 'cancelled')),
    result jsonb,
    PRIMARY KEY (plan_id, step_key),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES rm.claims(id, contract_sha256)
);

CREATE TABLE rm.research_plan_dependencies (
    plan_id uuid NOT NULL,
    step_key text NOT NULL,
    depends_on text NOT NULL,
    PRIMARY KEY (plan_id, step_key, depends_on),
    FOREIGN KEY (plan_id, step_key) REFERENCES rm.research_plan_steps(plan_id, step_key),
    FOREIGN KEY (plan_id, depends_on) REFERENCES rm.research_plan_steps(plan_id, step_key),
    CHECK (step_key <> depends_on)
);

CREATE TABLE rm.research_plan_returns (
    plan_id uuid NOT NULL,
    step_key text NOT NULL,
    generation integer NOT NULL,
    outcome text NOT NULL,
    result jsonb NOT NULL,
    returned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (plan_id, step_key, generation),
    FOREIGN KEY (plan_id, step_key) REFERENCES rm.research_plan_steps(plan_id, step_key)
);
CREATE TRIGGER plan_returns_append_only BEFORE UPDATE OR DELETE ON rm.research_plan_returns
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.create_research_plan(payload jsonb) RETURNS rm.research_plans
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved research_plans;
DECLARE step jsonb;
DECLARE digest text := encode(sha256(convert_to(payload::text, 'UTF8')), 'hex');
BEGIN
    IF jsonb_typeof(payload) IS DISTINCT FROM 'object'
        OR jsonb_typeof(payload -> 'steps') IS DISTINCT FROM 'array'
        OR jsonb_array_length(payload -> 'steps') NOT BETWEEN 1 AND 64
        OR jsonb_typeof(payload -> 'title') IS DISTINCT FROM 'string'
        OR jsonb_typeof(payload -> 'budget_seconds') IS DISTINCT FROM 'number'
        OR jsonb_typeof(payload -> 'max_parallel') IS DISTINCT FROM 'number' THEN
        RAISE EXCEPTION 'plan needs a title, concurrency, budget, and 1 to 64 steps' USING ERRCODE = '22023';
    END IF;
    INSERT INTO research_plans (title, body_sha256, max_parallel, budget_seconds)
    VALUES (payload ->> 'title', digest, (payload ->> 'max_parallel')::integer, (payload ->> 'budget_seconds')::integer)
    ON CONFLICT (body_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_plans p WHERE p.body_sha256 = digest;
        RETURN saved;
    END IF;

    FOR step IN SELECT value FROM jsonb_array_elements(payload -> 'steps')
    LOOP
        IF jsonb_typeof(step) IS DISTINCT FROM 'object'
            OR jsonb_typeof(step -> 'key') IS DISTINCT FROM 'string'
            OR jsonb_typeof(step -> 'action') IS DISTINCT FROM 'string'
            OR jsonb_typeof(step -> 'packet_id') IS DISTINCT FROM 'string'
            OR jsonb_typeof(step -> 'depends_on') IS DISTINCT FROM 'array'
            OR jsonb_typeof(step -> 'priority') IS DISTINCT FROM 'number'
            OR jsonb_typeof(step -> 'timeout_seconds') IS DISTINCT FROM 'number'
            OR jsonb_typeof(step -> 'max_attempts') IS DISTINCT FROM 'number' THEN
            RAISE EXCEPTION 'plan steps need a key, action, packet, dependencies, priority, and budgets'
                USING ERRCODE = '22023';
        END IF;
        INSERT INTO research_plan_steps (plan_id, step_key, action, claim_id, contract_sha256,
            packet_id, packet_sha256, priority, timeout_seconds, max_attempts)
        SELECT saved.id, step ->> 'key', step ->> 'action', c.id, c.contract_sha256,
            p.id, p.packet_sha256, (step ->> 'priority')::integer,
            (step ->> 'timeout_seconds')::integer, (step ->> 'max_attempts')::integer
        FROM formalization_status p JOIN claims c ON c.id = p.claim_id
        WHERE p.id = (step ->> 'packet_id')::uuid AND p.proof_ready AND c.contract_sha256 IS NOT NULL
          AND (step ->> 'action' <> 'prove' OR (c.sensitivity = 'public' AND EXISTS (
                SELECT 1 FROM production_checkers checker WHERE checker.domain = c.domain
          )));
        IF NOT FOUND THEN
            RAISE EXCEPTION 'plan step % needs an approved frozen packet supported by its action', step ->> 'key'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    INSERT INTO research_plan_dependencies (plan_id, step_key, depends_on)
    SELECT saved.id, item ->> 'key', dep
    FROM jsonb_array_elements(payload -> 'steps') AS nodes(item)
    CROSS JOIN LATERAL jsonb_array_elements_text(item -> 'depends_on') dep;

    IF EXISTS (
        WITH RECURSIVE reachable(original, visited) AS (
            SELECT step_key, depends_on FROM research_plan_dependencies WHERE plan_id = saved.id
            UNION
            SELECT r.original, e.depends_on FROM reachable r JOIN research_plan_dependencies e
            ON e.plan_id = saved.id AND e.step_key = r.visited
        )
        SELECT 1 FROM reachable WHERE original = visited
    ) THEN
        RAISE EXCEPTION 'plan dependencies must be acyclic' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM research_plan_steps s WHERE s.plan_id = saved.id AND s.action = 'await-review'
        AND (
            (SELECT count(*) FROM research_plan_dependencies e
             WHERE e.plan_id = s.plan_id AND e.step_key = s.step_key) <> 1
            OR NOT EXISTS (
                SELECT 1 FROM research_plan_dependencies e JOIN research_plan_steps proof
                ON proof.plan_id = e.plan_id AND proof.step_key = e.depends_on
                WHERE e.plan_id = s.plan_id AND e.step_key = s.step_key
                  AND proof.action = 'prove' AND proof.packet_id = s.packet_id
            )
        )
    ) THEN
        RAISE EXCEPTION 'await-review needs exactly one proof step for the same packet' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM research_plan_steps s WHERE s.plan_id = saved.id AND s.action = 'prove'
        GROUP BY s.claim_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'a plan can schedule at most one deterministic proof for the same claim'
            USING ERRCODE = '23514';
    END IF;
    IF (SELECT sum(s.timeout_seconds * s.max_attempts) FROM research_plan_steps s
        WHERE s.plan_id = saved.id) > saved.budget_seconds THEN
        RAISE EXCEPTION 'maximum step execution time exceeds the plan budget' USING ERRCODE = '23514';
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.plan_reviewed_dependency(rm.research_plan_steps) RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT (p.result ->> 'evidence_id')::uuid
    FROM research_plan_dependencies d JOIN research_plan_steps p
      ON p.plan_id = d.plan_id AND p.step_key = d.depends_on
    WHERE d.plan_id = ($1).plan_id AND d.step_key = ($1).step_key
      AND p.action = 'prove' AND p.status = 'succeeded'
$$;

CREATE FUNCTION rm.plan_step_satisfied(rm.research_plan_steps) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT ($1).status = 'succeeded' AND (
        ($1).action = 'snapshot' OR EXISTS (
            SELECT 1 FROM evidence_foundation_status e WHERE e.id = (($1).result ->> 'evidence_id')::uuid
              AND NOT e.needs_recheck AND (($1).action = 'prove' OR e.review_status = 'approved')
        )
    )
$$;

CREATE FUNCTION rm.plan_dependencies_ready(rm.research_plan_steps) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE prerequisites(step_key) AS (
        SELECT d.depends_on FROM research_plan_dependencies d
        WHERE d.plan_id = ($1).plan_id AND d.step_key = ($1).step_key
        UNION
        SELECT d.depends_on FROM prerequisites ancestor JOIN research_plan_dependencies d
        ON d.plan_id = ($1).plan_id AND d.step_key = ancestor.step_key
    )
    SELECT NOT EXISTS (
        SELECT 1 FROM prerequisites ancestor JOIN research_plan_steps dependency
          ON dependency.plan_id = ($1).plan_id AND dependency.step_key = ancestor.step_key
        WHERE NOT plan_step_satisfied(dependency)
    )
$$;

CREATE VIEW rm.plan_work_status AS
SELECT p.*, CASE
    WHEN p.status = 'succeeded' THEN
        CASE WHEN rm.plan_step_satisfied(p) AND rm.plan_dependencies_ready(p) THEN 'succeeded' ELSE 'needs_recheck' END
    WHEN p.status = 'running' AND p.lease_until > clock_timestamp() THEN 'running'
    WHEN p.status NOT IN ('queued', 'running') THEN p.status
    WHEN p.generation >= p.max_attempts THEN 'exhausted'
    WHEN NOT rm.plan_dependencies_ready(p) THEN 'waiting_dependency'
    WHEN p.ready_after > clock_timestamp() THEN 'waiting_retry'
    WHEN p.action = 'await-review' AND NOT EXISTS (
        SELECT 1 FROM rm.evidence_foundation_status e WHERE e.id = rm.plan_reviewed_dependency(p)
          AND NOT e.needs_recheck AND e.review_status IN ('approved', 'returned')
    ) THEN 'waiting_review'
    ELSE 'ready' END AS effective_status,
    ARRAY(SELECT d.depends_on FROM rm.research_plan_dependencies d WHERE d.plan_id = p.plan_id
        AND d.step_key = p.step_key ORDER BY d.depends_on) AS depends_on
FROM rm.research_plan_steps p;

CREATE FUNCTION rm.lease_research_plan_step(plan_id uuid, owner uuid) RETURNS SETOF rm.research_plan_steps
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE plan research_plans;
DECLARE step text;
BEGIN
    SELECT * INTO STRICT plan FROM research_plans p WHERE p.id = lease_research_plan_step.plan_id FOR UPDATE;
    IF plan.cancelled_at IS NOT NULL OR (
        SELECT count(*) FROM research_plan_steps s WHERE s.plan_id = plan.id
        AND s.status = 'running' AND s.lease_until > clock_timestamp()
    ) >= plan.max_parallel THEN
        RETURN;
    END IF;
    SELECT s.step_key INTO step FROM plan_work_status s WHERE s.plan_id = plan.id AND s.effective_status = 'ready'
    ORDER BY s.priority DESC, s.step_key LIMIT 1;
    IF NOT FOUND THEN RETURN; END IF;
    RETURN QUERY UPDATE research_plan_steps s SET status = 'running', generation = generation + 1,
        lease_owner = owner, lease_until = clock_timestamp() + (s.timeout_seconds + 20) * interval '1 second'
    WHERE s.plan_id = plan.id AND s.step_key = step RETURNING s.*;
END $$;

CREATE FUNCTION rm.finish_research_plan_step(plan_id uuid, step_key text, owner uuid,
    generation integer, outcome text, payload jsonb) RETURNS rm.research_plan_steps
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE plan research_plans;
DECLARE step research_plan_steps;
DECLARE receipt evidence_foundation_status;
DECLARE next_status text;
DECLARE report jsonb;
DECLARE retry_after timestamptz;
BEGIN
    SELECT * INTO STRICT plan FROM research_plans p WHERE p.id = finish_research_plan_step.plan_id FOR UPDATE;
    SELECT * INTO STRICT step FROM research_plan_steps s
    WHERE s.plan_id = plan.id AND s.step_key = finish_research_plan_step.step_key FOR UPDATE;
    IF plan.cancelled_at IS NOT NULL OR step.status <> 'running' OR step.lease_owner IS DISTINCT FROM owner
        OR step.generation IS DISTINCT FROM finish_research_plan_step.generation
        OR step.lease_until <= clock_timestamp() THEN
        RAISE EXCEPTION 'plan step lease is no longer current' USING ERRCODE = '23514';
    END IF;
    IF outcome IS NULL OR outcome NOT IN ('completed', 'failed', 'retryable') THEN
        RAISE EXCEPTION 'invalid plan step completion' USING ERRCODE = '22023';
    END IF;
    IF outcome <> 'completed' THEN
        IF jsonb_typeof(payload -> 'reason') IS DISTINCT FROM 'string' THEN
            RAISE EXCEPTION 'an unsuccessful completion needs a reason' USING ERRCODE = '22023';
        END IF;
        report := jsonb_build_object('reason', left(payload ->> 'reason', 2000));
        next_status := CASE WHEN outcome = 'retryable' AND step.generation < step.max_attempts
            THEN 'queued' ELSE 'failed' END;
        IF next_status = 'queued' THEN
            -- After a proof process times out, its underlying production stage may
            -- still own a durable lease. Retry after that lease becomes recoverable.
            SELECT greatest(clock_timestamp() + interval '1 second', max(j.lease_until))
            INTO retry_after FROM jobs j WHERE j.claim_id = step.claim_id AND j.status = 'running'
                AND ((j.kind = 'prove' AND j.payload ->> 'strategy' = 'program')
                    OR (j.kind = 'verify' AND j.payload ? 'checker_name'));
        END IF;
    ELSE
        IF NOT plan_dependencies_ready(step) THEN
            RAISE EXCEPTION 'plan step prerequisites changed during execution' USING ERRCODE = '23514';
        END IF;
        IF step.action IN ('prove', 'await-review') THEN
            SELECT * INTO receipt FROM evidence_foundation_status e
            WHERE e.id = CASE WHEN step.action = 'prove' THEN (payload ->> 'evidence_id')::uuid
                              ELSE plan_reviewed_dependency(step) END
                AND e.claim_id = step.claim_id AND e.contract_sha256 = step.contract_sha256
                AND e.kind IN ('exact_certificate', 'counterexample') AND NOT e.needs_recheck
                AND e.verdict = 'verified' AND e.diff_status = 'exact' AND e.issuance_sha256 IS NOT NULL;
            IF NOT FOUND OR (step.action = 'await-review' AND receipt.review_status NOT IN ('approved', 'returned')) THEN
                RAISE EXCEPTION 'plan proof needs its current verified receipt and review decision'
                    USING ERRCODE = '23514';
            END IF;
            report := jsonb_build_object('evidence_id', receipt.id, 'kind', receipt.kind,
                                         'review_status', receipt.review_status);
            next_status := CASE WHEN step.action = 'await-review' AND receipt.review_status = 'returned'
                THEN 'returned' ELSE 'succeeded' END;
        ELSE
            SELECT jsonb_build_object('claim_id', s.id, 'contract_sha256', s.contract_sha256, 'status', s.status,
                'evidence', coalesce((
                    SELECT jsonb_agg(jsonb_build_object('id', e.id, 'kind', e.kind, 'review_status', e.review_status,
                                                       'needs_recheck', e.needs_recheck) ORDER BY e.issued_at, e.id)
                    FROM evidence_foundation_status e WHERE e.claim_id = s.id
                ), '[]'::jsonb))
            INTO STRICT report FROM claim_foundation_status s WHERE s.id = step.claim_id;
            next_status := 'succeeded';
        END IF;
    END IF;
    INSERT INTO research_plan_returns (plan_id, step_key, generation, outcome, result)
    VALUES (plan.id, step.step_key, step.generation, next_status, report);
    UPDATE research_plan_steps s SET status = next_status, result = report, lease_owner = NULL, lease_until = NULL,
        ready_after = coalesce(retry_after, s.ready_after)
    WHERE s.plan_id = plan.id AND s.step_key = step.step_key RETURNING * INTO step;
    RETURN step;
END $$;

CREATE FUNCTION rm.cancel_research_plan(plan_id uuid, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE plan research_plans;
BEGIN
    IF reason IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'cancellation requires a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT plan FROM research_plans p WHERE p.id = cancel_research_plan.plan_id FOR UPDATE;
    IF plan.cancelled_at IS NULL THEN
        UPDATE research_plans SET cancelled_at = clock_timestamp(), cancelled_by = session_user,
            cancellation_reason = left(reason, 2000) WHERE id = plan.id;
        UPDATE research_plan_steps s SET status = 'cancelled', lease_owner = NULL, lease_until = NULL
        WHERE s.plan_id = plan.id AND s.status IN ('queued', 'running');
    END IF;
    RETURN plan.id;
END $$;

REVOKE EXECUTE ON FUNCTION rm.create_research_plan(jsonb), rm.plan_reviewed_dependency(rm.research_plan_steps),
    rm.plan_step_satisfied(rm.research_plan_steps), rm.plan_dependencies_ready(rm.research_plan_steps),
    rm.lease_research_plan_step(uuid, uuid), rm.finish_research_plan_step(uuid, text, uuid, integer, text, jsonb),
    rm.cancel_research_plan(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.create_research_plan(jsonb), rm.cancel_research_plan(uuid, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.lease_research_plan_step(uuid, uuid),
    rm.finish_research_plan_step(uuid, text, uuid, integer, text, jsonb) TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.plan_reviewed_dependency(rm.research_plan_steps),
    rm.plan_step_satisfied(rm.research_plan_steps), rm.plan_dependencies_ready(rm.research_plan_steps)
    TO rm_human, rm_scheduler;
GRANT SELECT ON rm.research_plans, rm.research_plan_steps, rm.research_plan_dependencies, rm.research_plan_returns,
    rm.plan_work_status TO rm_human, rm_scheduler;
