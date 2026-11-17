CREATE FUNCTION rm.research_step_scope_ready(packet_id uuid, subject_id uuid, packet_sha text, contract_sha text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM formalization_status packet JOIN claims c ON c.id = packet.claim_id
        WHERE packet.id = $1 AND c.id = $2 AND packet.packet_sha256 = $3 AND c.contract_sha256 = $4
          AND packet.proof_ready
          AND NOT EXISTS (
              SELECT 1 FROM packet_basis_items basis JOIN foundation_stale stale
                  ON stale.kind = 'item' AND stale.id = basis.version_id
              WHERE basis.packet_id = packet.id
          )
    )
$$;

CREATE OR REPLACE FUNCTION rm.plan_step_satisfied(rm.research_plan_steps) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT ($1).status = 'succeeded'
      AND research_step_scope_ready(($1).packet_id, ($1).claim_id, ($1).packet_sha256, ($1).contract_sha256)
      AND (($1).action = 'snapshot' OR EXISTS (
          SELECT 1 FROM evidence_foundation_status e WHERE e.id = (($1).result ->> 'evidence_id')::uuid
            AND NOT e.needs_recheck AND (($1).action = 'prove' OR e.review_status = 'approved')
      ))
$$;

CREATE OR REPLACE VIEW rm.plan_work_status AS
SELECT p.*, CASE
    WHEN p.status = 'succeeded' THEN
        CASE WHEN rm.plan_step_satisfied(p) AND rm.plan_dependencies_ready(p)
             THEN 'succeeded' ELSE 'needs_recheck' END
    WHEN p.status = 'running' AND p.lease_until > clock_timestamp() THEN 'running'
    WHEN p.status NOT IN ('queued', 'running') THEN p.status
    WHEN p.generation >= p.max_attempts THEN 'exhausted'
    WHEN NOT rm.research_step_scope_ready(p.packet_id, p.claim_id, p.packet_sha256, p.contract_sha256)
        THEN 'scope_unavailable'
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

CREATE INDEX research_plan_steps_running_proofs ON rm.research_plan_steps (claim_id, lease_until)
    WHERE action = 'prove' AND status = 'running';

CREATE FUNCTION rm.lease_research_plan_group(plan_ids uuid[], owner uuid) RETURNS SETOF rm.research_plan_steps
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE selected_plan uuid;
DECLARE selected_step text;
DECLARE selected_claim uuid;
DECLARE action text;
BEGIN
    IF NOT coalesce(cardinality(plan_ids) BETWEEN 1 AND 64, false)
        OR array_position(plan_ids, NULL::uuid) IS NOT NULL OR owner IS NULL THEN
        RAISE EXCEPTION 'a worker needs a lease owner and an explicit scope of 1 to 64 plans' USING ERRCODE = '22023';
    END IF;
    SELECT plan.id, candidate.step_key, candidate.claim_id, candidate.action
    INTO selected_plan, selected_step, selected_claim, action
    FROM research_plans plan
    CROSS JOIN LATERAL (
        SELECT step.step_key, step.priority, step.claim_id, step.action
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
    RETURN QUERY UPDATE research_plan_steps step SET status = 'running', generation = generation + 1,
        lease_owner = owner, lease_until = clock_timestamp() + (step.timeout_seconds + 20) * interval '1 second'
    WHERE step.plan_id = selected_plan AND step.step_key = selected_step RETURNING step.*;
END $$;

CREATE OR REPLACE FUNCTION rm.lease_research_plan_step(plan_id uuid, owner uuid) RETURNS SETOF rm.research_plan_steps
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM 1 FROM research_plans p WHERE p.id = plan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no research plan matches this ID' USING ERRCODE = 'P0002';
    END IF;
    RETURN QUERY SELECT * FROM lease_research_plan_group(ARRAY[plan_id], owner);
END $$;

CREATE FUNCTION rm.plan_reusable_receipt(plan_id uuid, step_key text, owner uuid, generation integer)
RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT e.id FROM research_plan_steps step
    JOIN evidence_foundation_status e ON e.claim_id = step.claim_id AND e.contract_sha256 = step.contract_sha256
    JOIN attempts proof ON proof.id = e.attempt_id
    JOIN jobs job ON job.id = proof.job_id
    WHERE step.plan_id = $1 AND step.step_key = $2 AND step.action = 'prove' AND step.status = 'running'
      AND step.lease_owner = $3 AND step.generation = $4 AND step.lease_until > clock_timestamp()
      AND e.kind IN ('exact_certificate', 'counterexample') AND e.verdict = 'verified'
      AND e.diff_status = 'exact' AND e.issuance_sha256 IS NOT NULL AND NOT e.needs_recheck
      AND job.payload @> jsonb_build_object('formalization_id', step.packet_id, 'formalization_sha256', step.packet_sha256)
    ORDER BY CASE e.review_status WHEN 'approved' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, e.issued_at DESC, e.id
    LIMIT 1
$$;

CREATE FUNCTION rm.guard_plan_step_scope() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_formalization_ready(NEW.claim_id, jsonb_build_object(
        'formalization_id', NEW.packet_id, 'formalization_sha256', NEW.packet_sha256));
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(NEW.packet_id, '[]'::jsonb);
    IF NEW.action = 'prove' AND NOT EXISTS (
        SELECT 1 FROM evidence_foundation_status e JOIN attempts proof ON proof.id = e.attempt_id
        JOIN jobs job ON job.id = proof.job_id
        WHERE e.id = (NEW.result ->> 'evidence_id')::uuid AND e.claim_id = NEW.claim_id AND NOT e.needs_recheck
          AND job.payload @> jsonb_build_object('formalization_id', NEW.packet_id, 'formalization_sha256', NEW.packet_sha256)
    ) THEN
        RAISE EXCEPTION 'proof receipt must match the plan step and its current frozen formalization' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER fence_plan_finished_step BEFORE UPDATE OF status ON rm.research_plan_steps
    FOR EACH ROW WHEN (OLD.status = 'running' AND NEW.status IN ('succeeded', 'returned'))
    EXECUTE FUNCTION rm.guard_plan_step_scope();

CREATE FUNCTION rm.progress_verification_queue(batch integer, policy_ids uuid[]) RETURNS TABLE (id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT s.id FROM research_progress_status s WHERE s.state = 'waiting_verifier'
      AND ($2 IS NULL OR s.id = ANY($2))
    ORDER BY s.approved_at, s.id LIMIT greatest(1, least($1, 64))
$$;

CREATE FUNCTION rm.choose_research_route(target uuid)
RETURNS TABLE (domain text, route text, reason text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT state.domain,
        CASE WHEN NOT route.ready THEN 'scope_unavailable'
             WHEN state.status = 'needs_recheck' THEN 'needs_reopen'
             WHEN route.solver THEN 'deterministic_proof'
             ELSE 'observe_verified_evidence' END,
        CASE WHEN NOT route.ready THEN 'The frozen packet or its exact premise basis requires review.'
             WHEN state.status = 'needs_recheck' THEN 'The researcher must reopen the stale decisive result.'
             WHEN route.solver THEN 'A registered program can produce a certificate in this domain.'
             WHEN EXISTS (SELECT 1 FROM production_checkers c WHERE c.domain = state.domain)
                 THEN 'Observe independently checked receipts. Supply a witness to the registered checker when needed.'
             ELSE 'Observe independently checked receipts when available for this scope.' END
    FROM formalization_status packet
    JOIN claim_foundation_status state ON state.id = packet.claim_id
    JOIN claims claim ON claim.id = state.id
    CROSS JOIN LATERAL (
        SELECT research_step_scope_ready(packet.id, state.id, packet.packet_sha256, state.contract_sha256) AS ready,
            claim.sensitivity = 'public' AND EXISTS (
                SELECT 1 FROM production_checkers c WHERE c.domain = state.domain AND c.supports_worker_proof
            ) AS solver
    ) route
    WHERE packet.id = $1
$$;

REVOKE ALL ON FUNCTION rm.research_step_scope_ready(uuid, uuid, text, text),
    rm.lease_research_plan_group(uuid[], uuid), rm.plan_reusable_receipt(uuid, text, uuid, integer),
    rm.guard_plan_step_scope(), rm.progress_verification_queue(integer, uuid[]),
    rm.choose_research_route(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.research_step_scope_ready(uuid, uuid, text, text) TO rm_human, rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.lease_research_plan_group(uuid[], uuid),
    rm.plan_reusable_receipt(uuid, text, uuid, integer) TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.progress_verification_queue(integer, uuid[]) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.choose_research_route(uuid) TO rm_human;
