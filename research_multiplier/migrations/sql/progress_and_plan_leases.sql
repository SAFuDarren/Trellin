CREATE OR REPLACE VIEW rm.progress_eligible_receipts WITH (security_barrier = true) AS
SELECT p.id AS policy_id, e.id AS evidence_id, e.kind, e.review_status, e.issued_at,
    CASE WHEN p.policy ->> 'metric' = 'flow-interval-width'
        THEN (e.result ->> 'flow_value')::numeric END AS exact_value
FROM rm.research_progress_policies p
JOIN rm.evidence_foundation_status e ON e.claim_id = p.claim_id AND e.contract_sha256 = p.contract_sha256
WHERE e.kind IN ('exact_certificate', 'counterexample', 'lean') AND NOT e.needs_recheck
    AND e.verdict = 'verified' AND e.diff_status = 'exact' AND e.issuance_sha256 IS NOT NULL
    AND (CASE WHEN e.lean_check_id IS NOT NULL THEN EXISTS (
        SELECT 1 FROM rm.lean_checks checked
        JOIN rm.lean_statements statement ON statement.id = checked.statement_id
        JOIN rm.formalization_packets packet ON packet.id = statement.packet_id
        LEFT JOIN rm.lean_execution_links execution ON execution.check_id = checked.id
        WHERE checked.id = e.lean_check_id AND statement.packet_id = p.packet_id
          AND packet.packet_sha256 = p.packet_sha256
          AND (
              (e.kind = 'lean' AND execution.check_id IS NULL)
              OR (execution.verification_attempt_id = e.verification_attempt_id AND (
                  (e.kind = 'lean' AND execution.target = 'goal') OR
                  (e.kind = 'counterexample' AND execution.target = 'refutation')
              ))
          )
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

-- The plan row is the capacity fence. Change its revision on each committed
-- lease so even a repeatable-read worker cannot proceed from an older plan row.
ALTER TABLE rm.research_plans ADD COLUMN lease_revision bigint NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION rm.lease_research_plan_group(plan_ids uuid[], owner uuid)
RETURNS SETOF rm.research_plan_steps
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE selected_plan uuid;
DECLARE selected_step text;
DECLARE selected_generation integer;
DECLARE selected_claim uuid;
DECLARE action text;
BEGIN
    IF NOT coalesce(cardinality(plan_ids) BETWEEN 1 AND 64, false)
        OR array_position(plan_ids, NULL::uuid) IS NOT NULL OR owner IS NULL THEN
        RAISE EXCEPTION 'a worker needs a lease owner and an explicit scope of 1 to 64 plans' USING ERRCODE = '22023';
    END IF;
    SELECT plan.id, candidate.step_key, candidate.generation, candidate.claim_id, candidate.action
    INTO selected_plan, selected_step, selected_generation, selected_claim, action
    FROM research_plans plan
    CROSS JOIN LATERAL (
        SELECT step.step_key, step.priority, step.generation, step.claim_id, step.action
        FROM plan_work_status step
        WHERE step.plan_id = plan.id AND step.effective_status = 'ready'
          AND (step.action <> 'prove' OR NOT EXISTS (
              SELECT 1 FROM research_plan_steps busy WHERE busy.claim_id = step.claim_id
                  AND busy.action = 'prove' AND busy.status = 'running' AND busy.lease_until > clock_timestamp()
          ))
        ORDER BY step.priority DESC, step.step_key LIMIT 1
    ) candidate
    WHERE plan.id = ANY(plan_ids) AND plan.cancelled_at IS NULL AND (
        SELECT count(*) FROM research_plan_steps active
        WHERE active.plan_id = plan.id AND active.status = 'running' AND active.lease_until > clock_timestamp()
    ) < plan.max_parallel
    ORDER BY candidate.priority DESC, plan.created_at, plan.id, candidate.step_key
    LIMIT 1 FOR UPDATE OF plan SKIP LOCKED;
    IF NOT FOUND THEN RETURN; END IF;
    IF action = 'prove' THEN
        -- Serialize claims briefly while assigning a lease across separate plans.
        PERFORM 1 FROM claims c WHERE c.id = selected_claim FOR UPDATE SKIP LOCKED;
        IF NOT FOUND OR EXISTS (
            SELECT 1 FROM research_plan_steps busy WHERE busy.claim_id = selected_claim
                AND busy.action = 'prove' AND busy.status = 'running' AND busy.lease_until > clock_timestamp()
        ) THEN RETURN; END IF;
    END IF;
    -- The ranking snapshot can predate another worker's commit. Re-read the
    -- candidate and active leases in a new statement while holding the plan lock.
    RETURN QUERY UPDATE research_plan_steps step SET status = 'running', generation = step.generation + 1,
        lease_owner = owner, lease_until = clock_timestamp() + (step.timeout_seconds + 20) * interval '1 second'
    FROM research_plans plan
    WHERE plan.id = selected_plan AND plan.cancelled_at IS NULL
        AND step.plan_id = plan.id AND step.step_key = selected_step AND step.generation = selected_generation
        AND EXISTS (
            SELECT FROM plan_work_status ready WHERE ready.plan_id = plan.id
                AND ready.step_key = selected_step AND ready.effective_status = 'ready'
        )
        AND (
            SELECT count(*) FROM research_plan_steps active
            WHERE active.plan_id = plan.id AND active.status = 'running' AND active.lease_until > clock_timestamp()
        ) < plan.max_parallel
    RETURNING step.*;
    IF FOUND THEN
        UPDATE research_plans p SET lease_revision = p.lease_revision + 1 WHERE p.id = selected_plan;
    END IF;
END $$;
