-- A verifier may check supplied certificates without having an automatic solver.
ALTER TABLE rm.production_checkers ADD COLUMN supports_worker_proof boolean NOT NULL DEFAULT false;
UPDATE rm.production_checkers SET supports_worker_proof = true
WHERE (domain, checker_name) IN (
    ('fixed-directed-max-flow', 'exact-flow-cut'), ('graph-coloring', 'exact-graph-coloring')
);

CREATE FUNCTION rm.guard_plan_worker_proof() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM claims c JOIN production_checkers checker ON checker.domain = c.domain
        WHERE c.id = NEW.claim_id AND checker.supports_worker_proof
    ) THEN
        RAISE EXCEPTION 'the plan proof needs a registered deterministic solver' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
REVOKE EXECUTE ON FUNCTION rm.guard_plan_worker_proof() FROM PUBLIC;
CREATE TRIGGER require_plan_worker_proof BEFORE INSERT ON rm.research_plan_steps
FOR EACH ROW WHEN (NEW.action = 'prove') EXECUTE FUNCTION rm.guard_plan_worker_proof();

CREATE TABLE rm.research_progress_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    packet_sha256 rm.sha256_hex NOT NULL,
    claim_id uuid NOT NULL,
    contract_sha256 rm.sha256_hex NOT NULL,
    policy jsonb NOT NULL,
    initial_interval_upper numeric,
    policy_sha256 rm.sha256_hex UNIQUE NOT NULL,
    proposed_by name NOT NULL DEFAULT session_user,
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES rm.claims(id, contract_sha256)
);

CREATE TABLE rm.research_progress_approvals (
    policy_id uuid PRIMARY KEY REFERENCES rm.research_progress_policies(id),
    actor_ref text NOT NULL CHECK (length(btrim(actor_ref)) BETWEEN 1 AND 160),
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 2000),
    authenticated_role name NOT NULL DEFAULT session_user,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.research_progress_stops (
    policy_id uuid PRIMARY KEY REFERENCES rm.research_progress_policies(id),
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 2000),
    stopped_by name NOT NULL DEFAULT session_user,
    stopped_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.research_progress_plans (
    policy_id uuid NOT NULL REFERENCES rm.research_progress_policies(id),
    round integer NOT NULL CHECK (round BETWEEN 1 AND 4),
    plan_id uuid UNIQUE NOT NULL REFERENCES rm.research_plans(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (policy_id, round)
);

CREATE TABLE rm.research_progress_observations (
    policy_id uuid NOT NULL REFERENCES rm.research_progress_policies(id),
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    disposition text NOT NULL CHECK (disposition IN ('proved', 'refuted')),
    measurement jsonb NOT NULL,
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (policy_id, evidence_id)
);

DO $$
DECLARE relation text;
BEGIN
    FOREACH relation IN ARRAY ARRAY['research_progress_policies', 'research_progress_approvals',
            'research_progress_stops', 'research_progress_plans', 'research_progress_observations'] LOOP
        EXECUTE format('CREATE TRIGGER research_progress_append_only BEFORE UPDATE OR DELETE ON rm.%I '
            'FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation()', relation);
    END LOOP;
END $$;

CREATE FUNCTION rm.propose_research_progress(input jsonb) RETURNS rm.research_progress_policies
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE packet formalization_status;
DECLARE subject claims;
DECLARE approved_policy jsonb;
DECLARE digest text;
DECLARE rounds integer;
DECLARE attempts integer;
DECLARE duration integer;
DECLARE budget integer;
DECLARE priority integer;
DECLARE cost_per_round integer;
DECLARE baseline numeric;
DECLARE saved research_progress_policies;
BEGIN
    IF jsonb_typeof(input) IS DISTINCT FROM 'object'
        OR input - ARRAY['packet_id','metric','actions','max_rounds','proof_timeout_seconds',
            'proof_max_attempts','budget_seconds','priority'] <> '{}'::jsonb
        OR jsonb_typeof(input -> 'packet_id') IS DISTINCT FROM 'string'
        OR coalesce(input ->> 'metric', '') NOT IN ('exact-closure', 'flow-interval-width')
        OR input -> 'actions' IS NULL
        OR input -> 'actions' NOT IN ('[]'::jsonb, '["prove","await-review","snapshot"]'::jsonb)
        OR NOT coalesce(input ->> 'max_rounds' ~ '^[0-4]$', false)
        OR NOT coalesce(input ->> 'proof_timeout_seconds' ~ '^[0-9]{1,3}$', false)
        OR NOT coalesce(input ->> 'proof_max_attempts' ~ '^[0-4]$', false)
        OR NOT coalesce(input ->> 'budget_seconds' ~ '^[0-9]{1,5}$', false)
        OR NOT coalesce(input ->> 'priority' ~ '^-?[0-9]{1,3}$', false) THEN
        RAISE EXCEPTION 'expected a packet, supported metric, exact actions and bounded budgets'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT packet FROM formalization_status p WHERE p.id = (input ->> 'packet_id')::uuid;
    SELECT * INTO STRICT subject FROM claims c WHERE c.id = packet.claim_id;
    IF NOT packet.proof_ready OR subject.contract_sha256 IS NULL THEN
        RAISE EXCEPTION 'progress requires the current, approved and frozen formalization packet'
            USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(packet.id, '[]'::jsonb);
    rounds := (input ->> 'max_rounds')::integer;
    attempts := (input ->> 'proof_max_attempts')::integer;
    duration := (input ->> 'proof_timeout_seconds')::integer;
    budget := (input ->> 'budget_seconds')::integer;
    priority := (input ->> 'priority')::integer;
    cost_per_round := duration * attempts + 2;
    IF priority NOT BETWEEN -100 AND 100 OR budget > 36000 OR (
        input -> 'actions' = '[]'::jsonb AND (rounds <> 0 OR budget <> 0 OR attempts <> 0 OR duration <> 0)
    ) OR (
        input -> 'actions' <> '[]'::jsonb AND (
            rounds = 0 OR attempts = 0 OR duration NOT BETWEEN 1 AND 240
            OR budget < rounds * cost_per_round OR subject.sensitivity <> 'public'
            OR NOT EXISTS (SELECT 1 FROM production_checkers c WHERE c.domain = subject.domain AND c.supports_worker_proof)
        )
    ) THEN
        RAISE EXCEPTION 'approved actions must fit the budget and have a registered public deterministic solver'
            USING ERRCODE = '23514';
    END IF;
    IF input ->> 'metric' = 'flow-interval-width' THEN
        IF subject.domain <> 'fixed-directed-max-flow' THEN
            RAISE EXCEPTION 'flow interval progress requires a frozen maximum-flow instance' USING ERRCODE = '23514';
        END IF;
        -- A zero flow is feasible. The capacity leaving the source universally bounds net flow.
        SELECT coalesce(sum((edge ->> 2)::numeric), 0) INTO baseline
        FROM jsonb_array_elements(subject.contract_snapshot #> '{small_case_spec,edges}') edge
        WHERE edge ->> 0 = subject.contract_snapshot #>> '{small_case_spec,source}';
    END IF;
    approved_policy := jsonb_build_object(
        'metric', input -> 'metric', 'actions', input -> 'actions', 'max_rounds', rounds,
        'proof_timeout_seconds', duration, 'proof_max_attempts', attempts, 'budget_seconds', budget,
        'priority', priority
    );
    digest := encode(sha256(convert_to(jsonb_build_object('version', 1, 'packet_id', packet.id,
        'claim_id', subject.id, 'packet_sha256', packet.packet_sha256, 'contract_sha256', subject.contract_sha256,
        'policy', approved_policy, 'initial_interval_upper', baseline)::text, 'UTF8')), 'hex');
    INSERT INTO research_progress_policies (packet_id, packet_sha256, claim_id, contract_sha256,
        policy, initial_interval_upper, policy_sha256)
    VALUES (packet.id, packet.packet_sha256, subject.id, subject.contract_sha256, approved_policy, baseline, digest)
    ON CONFLICT (policy_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_progress_policies p WHERE p.policy_sha256 = digest;
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.approve_research_progress(policy_id uuid, expected_sha text, actor text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_progress_policies;
BEGIN
    SELECT * INTO STRICT target FROM research_progress_policies p
    WHERE p.id = approve_research_progress.policy_id FOR UPDATE;
    IF target.policy_sha256 IS DISTINCT FROM expected_sha THEN
        RAISE EXCEPTION 'review the exact progress policy hash before approval' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM research_progress_stops s WHERE s.policy_id = target.id) THEN
        RAISE EXCEPTION 'a stopped policy cannot be approved' USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(target.claim_id, jsonb_build_object(
        'formalization_id', target.packet_id, 'formalization_sha256', target.packet_sha256));
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(target.packet_id, '[]'::jsonb);
    INSERT INTO research_progress_approvals (policy_id, actor_ref, reason) VALUES (target.id, actor, reason)
    ON CONFLICT ON CONSTRAINT research_progress_approvals_pkey DO NOTHING;
    RETURN target.id;
END $$;

-- Select only verifier-issued, exact receipts matching the frozen signed scope.
CREATE VIEW rm.progress_eligible_receipts WITH (security_barrier = true) AS
SELECT p.id AS policy_id, e.id AS evidence_id, e.kind, e.review_status, e.issued_at,
    CASE WHEN p.policy ->> 'metric' = 'flow-interval-width'
        THEN (e.result ->> 'flow_value')::numeric END AS exact_value
FROM rm.research_progress_policies p
JOIN rm.evidence_foundation_status e ON e.claim_id = p.claim_id AND e.contract_sha256 = p.contract_sha256
WHERE e.kind IN ('exact_certificate', 'counterexample', 'lean') AND NOT e.needs_recheck
    AND e.verdict = 'verified' AND e.diff_status = 'exact' AND e.issuance_sha256 IS NOT NULL
    AND (CASE WHEN e.kind = 'lean' THEN EXISTS (
        SELECT 1 FROM rm.lean_checks checked JOIN rm.lean_statements statement ON statement.id = checked.statement_id
        WHERE checked.id = e.lean_check_id AND statement.packet_id = p.packet_id
    ) ELSE EXISTS (
        SELECT 1 FROM rm.attempts attempt JOIN rm.jobs job ON job.id = attempt.job_id
        WHERE attempt.id = e.attempt_id AND job.payload @> jsonb_build_object(
            'formalization_id', p.packet_id, 'formalization_sha256', p.packet_sha256)
    ) END)
    AND (p.policy ->> 'metric' = 'exact-closure' OR (
        e.verifier_name = 'exact-flow-cut'
        AND jsonb_typeof(e.result -> 'flow_value') = 'number'
        AND e.result -> 'flow_value' = e.result -> 'cut_capacity'
        AND e.result -> 'flow_value' = e.result -> 'value'
    ));

CREATE VIEW rm.research_progress_status WITH (security_barrier = true) AS
SELECT p.*, approval.actor_ref AS approved_by, approval.approved_at, stop.stopped_at,
    stop.reason AS stop_reason, recent.round AS latest_round, recent.plan_id, recent.step_states,
    receipt.evidence_id AS current_evidence_id,
    CASE WHEN observed.evidence_id IS NOT NULL AND scope.ready AND NOT conflict.found
        THEN observed.evidence_id END AS best_evidence_id,
    CASE WHEN observed.evidence_id IS NOT NULL AND scope.ready AND NOT conflict.found THEN observed.disposition END
        AS disposition,
    CASE WHEN observed.evidence_id IS NOT NULL AND scope.ready AND NOT conflict.found THEN observed.measurement
        WHEN p.policy ->> 'metric' = 'flow-interval-width' THEN jsonb_build_object(
            'lower', 0, 'upper', p.initial_interval_upper, 'width', p.initial_interval_upper)
        ELSE jsonb_build_object('resolved', false) END AS progress,
    CASE WHEN stop.stopped_at IS NOT NULL THEN 'stopped'
        WHEN approval.approved_at IS NULL THEN 'awaiting_approval'
        WHEN NOT scope.ready THEN 'scope_unavailable'
        WHEN conflict.found THEN 'conflicting_receipts'
        WHEN state.status = 'needs_recheck' THEN 'needs_reopen'
        WHEN observed.evidence_id IS NOT NULL AND NOT coalesce(recent.ready, false) THEN 'resolved'
        WHEN receipt.review_status = 'returned' AND NOT coalesce(recent.ready, false) THEN 'review_returned'
        WHEN coalesce(recent.ready, false) THEN 'ready'
        WHEN receipt.review_status = 'pending' THEN 'waiting_review'
        WHEN receipt.review_status = 'approved' THEN 'waiting_verifier'
        WHEN coalesce(recent.running, false) THEN 'running'
        WHEN coalesce(recent.waiting_retry, false) THEN 'waiting_retry'
        WHEN coalesce(recent.failed, false) THEN 'route_stopped'
        WHEN p.policy -> 'actions' = '[]'::jsonb THEN 'awaiting_evidence'
        WHEN NOT EXISTS (SELECT 1 FROM rm.production_checkers c
                         WHERE c.domain = state.domain AND c.supports_worker_proof) THEN 'checker_unavailable'
        WHEN coalesce(recent.round, 0) >= (p.policy ->> 'max_rounds')::integer THEN 'budget_exhausted'
        WHEN recent.round IS NULL OR coalesce(recent.stale_proof, false) THEN 'needs_plan'
        ELSE 'waiting_dependency' END AS state
FROM rm.research_progress_policies p
JOIN rm.formalization_status packet ON packet.id = p.packet_id
JOIN rm.claim_foundation_status state ON state.id = p.claim_id
CROSS JOIN LATERAL (
    SELECT packet.proof_ready AND NOT EXISTS (
        SELECT 1 FROM rm.packet_basis_items item JOIN rm.foundation_stale stale
            ON stale.kind = 'item' AND stale.id = item.version_id WHERE item.packet_id = p.packet_id
    ) AS ready
) scope
LEFT JOIN rm.research_progress_approvals approval ON approval.policy_id = p.id
LEFT JOIN rm.research_progress_stops stop ON stop.policy_id = p.id
LEFT JOIN LATERAL (
    SELECT count(DISTINCT (r.kind = 'counterexample')) > 1 OR count(DISTINCT r.exact_value) > 1 AS found
    FROM rm.progress_eligible_receipts r
    WHERE r.policy_id = p.id AND r.review_status <> 'returned'
) conflict ON true
LEFT JOIN LATERAL (
    SELECT r.* FROM rm.progress_eligible_receipts r WHERE r.policy_id = p.id
    ORDER BY CASE r.review_status WHEN 'approved' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
        r.issued_at DESC, r.evidence_id LIMIT 1
) receipt ON true
LEFT JOIN LATERAL (
    SELECT observation.* FROM rm.research_progress_observations observation
    JOIN rm.progress_eligible_receipts current ON current.evidence_id = observation.evidence_id
        AND current.policy_id = observation.policy_id AND current.review_status = 'approved'
    WHERE observation.policy_id = p.id
    ORDER BY observation.recorded_at DESC, observation.evidence_id LIMIT 1
) observed ON true
LEFT JOIN LATERAL (
    SELECT link.round, link.plan_id,
        bool_or(step.effective_status = 'ready') AS ready,
        bool_or(step.effective_status = 'running') AS running,
        bool_or(step.effective_status = 'waiting_retry') AS waiting_retry,
        bool_or(step.effective_status IN ('failed', 'exhausted', 'returned', 'cancelled')) AS failed,
        bool_or(step.action = 'prove' AND step.effective_status = 'needs_recheck') AS stale_proof,
        jsonb_object_agg(step.step_key, jsonb_build_object('state', step.effective_status,
            'attempts', step.generation, 'result', step.result)) AS step_states
    FROM rm.research_progress_plans link JOIN rm.plan_work_status step ON step.plan_id = link.plan_id
    WHERE link.policy_id = p.id
    GROUP BY link.round, link.plan_id ORDER BY link.round DESC LIMIT 1
) recent ON true;

CREATE FUNCTION rm.record_research_progress(policy_id uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_progress_policies;
DECLARE recorded integer;
BEGIN
    SELECT * INTO STRICT target FROM research_progress_policies p
    WHERE p.id = record_research_progress.policy_id FOR SHARE;
    IF NOT EXISTS (
        SELECT 1 FROM research_progress_approvals a WHERE a.policy_id = target.id
    ) OR EXISTS (
        SELECT 1 FROM research_progress_stops s WHERE s.policy_id = target.id
    ) THEN
        RETURN 0;
    END IF;
    PERFORM require_formalization_ready(target.claim_id, jsonb_build_object(
        'formalization_id', target.packet_id, 'formalization_sha256', target.packet_sha256));
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(target.packet_id, '[]'::jsonb);
    INSERT INTO research_progress_observations (policy_id, evidence_id, disposition, measurement)
    SELECT target.id, r.evidence_id, CASE WHEN r.kind = 'counterexample' THEN 'refuted' ELSE 'proved' END,
        CASE WHEN target.policy ->> 'metric' = 'flow-interval-width' THEN jsonb_build_object(
            'lower', r.exact_value, 'upper', r.exact_value, 'width', 0, 'initial_width', target.initial_interval_upper)
        ELSE jsonb_build_object('resolved', true) END
    FROM progress_eligible_receipts r WHERE r.policy_id = target.id AND r.review_status = 'approved'
      AND (SELECT count(DISTINCT (other.kind = 'counterexample')) = 1
                AND count(DISTINCT other.exact_value) <= 1
           FROM progress_eligible_receipts other
           WHERE other.policy_id = target.id AND other.review_status <> 'returned')
    ON CONFLICT ON CONSTRAINT research_progress_observations_pkey DO NOTHING;
    GET DIAGNOSTICS recorded = ROW_COUNT;
    RETURN recorded;
END $$;

CREATE FUNCTION rm.progress_verification_queue(batch integer) RETURNS TABLE (id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT s.id FROM research_progress_status s WHERE s.state = 'waiting_verifier'
    ORDER BY s.approved_at, s.id LIMIT greatest(1, least(batch, 64))
$$;

CREATE FUNCTION rm.prepare_research_progress_plan(policy_id uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_progress_policies;
DECLARE status research_progress_status;
DECLARE next_round integer;
DECLARE attempts integer;
DECLARE duration integer;
DECLARE priority integer;
DECLARE plan research_plans;
DECLARE steps jsonb;
BEGIN
    SELECT * INTO STRICT target FROM research_progress_policies p
    WHERE p.id = prepare_research_progress_plan.policy_id FOR UPDATE;
    SELECT * INTO STRICT status FROM research_progress_status s WHERE s.id = target.id;
    IF status.state = 'ready' THEN
        RETURN status.plan_id;
    ELSIF status.state <> 'needs_plan' THEN
        RETURN NULL;
    END IF;
    PERFORM require_formalization_ready(target.claim_id, jsonb_build_object(
        'formalization_id', target.packet_id, 'formalization_sha256', target.packet_sha256));
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(target.packet_id, '[]'::jsonb);
    next_round := coalesce(status.latest_round, 0) + 1;
    attempts := (target.policy ->> 'proof_max_attempts')::integer;
    duration := (target.policy ->> 'proof_timeout_seconds')::integer;
    priority := (target.policy ->> 'priority')::integer;
    steps := jsonb_build_array(
        jsonb_build_object('key', 'prove', 'action', 'prove', 'packet_id', target.packet_id,
            'depends_on', '[]'::jsonb, 'priority', priority, 'timeout_seconds', duration, 'max_attempts', attempts),
        jsonb_build_object('key', 'review', 'action', 'await-review', 'packet_id', target.packet_id,
            'depends_on', '["prove"]'::jsonb, 'priority', priority, 'timeout_seconds', 1, 'max_attempts', 1),
        jsonb_build_object('key', 'snapshot', 'action', 'snapshot', 'packet_id', target.packet_id,
            'depends_on', '["review"]'::jsonb, 'priority', priority, 'timeout_seconds', 1, 'max_attempts', 1)
    );
    plan := create_research_plan(jsonb_build_object('title', format('Progress %s round %s', target.id, next_round),
        'max_parallel', 1, 'budget_seconds', duration * attempts + 2, 'steps', steps));
    INSERT INTO research_progress_plans (policy_id, round, plan_id)
    VALUES (target.id, next_round, plan.id);
    RETURN plan.id;
END $$;

CREATE FUNCTION rm.stop_research_progress(policy_id uuid, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_progress_policies;
BEGIN
    SELECT * INTO STRICT target FROM research_progress_policies p
    WHERE p.id = stop_research_progress.policy_id FOR UPDATE;
    INSERT INTO research_progress_stops (policy_id, reason) VALUES (target.id, reason)
    ON CONFLICT ON CONSTRAINT research_progress_stops_pkey DO NOTHING;
    PERFORM cancel_research_plan(link.plan_id, reason)
    FROM research_progress_plans link WHERE link.policy_id = target.id;
    RETURN target.id;
END $$;

REVOKE EXECUTE ON FUNCTION rm.propose_research_progress(jsonb),
    rm.approve_research_progress(uuid, text, text, text), rm.stop_research_progress(uuid, text),
    rm.prepare_research_progress_plan(uuid), rm.record_research_progress(uuid),
    rm.progress_verification_queue(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_research_progress(jsonb),
    rm.approve_research_progress(uuid, text, text, text), rm.stop_research_progress(uuid, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.prepare_research_progress_plan(uuid) TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.record_research_progress(uuid), rm.progress_verification_queue(integer) TO rm_verifier;
GRANT SELECT ON rm.research_progress_status, rm.research_progress_observations, rm.research_progress_plans
    TO rm_human, rm_scheduler;
