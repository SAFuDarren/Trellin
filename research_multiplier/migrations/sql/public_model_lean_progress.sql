-- A connected model is bound to the exact workspace, frozen scope and call allowance.
CREATE FUNCTION rm.propose_public_model_lean_progress(statement_id uuid, model_rounds integer, priority integer,
    identity jsonb) RETURNS rm.research_progress_policies
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE packet formalization_packets;
DECLARE binding record;
DECLARE settings jsonb;
DECLARE digest text;
DECLARE saved research_progress_policies;
BEGIN
    PERFORM lock_live_foundation();
    anchor := require_lean_review($1, true);
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = anchor.packet_id;
    SELECT * INTO STRICT binding FROM current_workspace_binding();
    IF anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR binding.workspace_id IS NULL
        OR NOT coalesce($2 BETWEEN 1 AND 8, false)
        OR NOT coalesce($3 BETWEEN -100 AND 100, false)
        OR NOT EXISTS (SELECT FROM claims c WHERE c.id = anchor.claim_id AND c.sensitivity = 'public')
        OR NOT coalesce(
            jsonb_typeof($4) = 'object'
            AND $4 - ARRAY['id', 'provider', 'model', 'fingerprint'] = '{}'::jsonb
            AND jsonb_typeof($4 -> 'id') = 'string'
            AND $4 ->> 'id' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
            AND $4 ->> 'provider' IN ('codex-cli', 'claude-code', 'openai', 'anthropic')
            AND jsonb_typeof($4 -> 'model') = 'string'
            AND $4 ->> 'model' ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}$'
            AND jsonb_typeof($4 -> 'fingerprint') = 'string'
            AND $4 ->> 'fingerprint' ~ '^[a-f0-9]{64}$', false)
    THEN
        RAISE EXCEPTION 'a connected model needs a public frozen scope, workspace and exact signed identity'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_current_basis(packet.id, '[]'::jsonb);
    PERFORM require_public_model_packet(packet.id);
    settings := jsonb_build_object('metric', 'exact-closure', 'actions', '[]'::jsonb, 'max_rounds', 0,
        'proof_timeout_seconds', 0, 'proof_max_attempts', 0, 'budget_seconds', 0, 'priority', $3,
        'lean', jsonb_build_object('statement_id', anchor.id, 'statement_sha256', anchor.statement_sha256,
            'model_rounds', $2, 'public_model', $4,
            'public_workspace', jsonb_build_array(binding.workspace_id, binding.database_id)));
    digest := encode(sha256(convert_to(jsonb_build_object('version', 1, 'packet_id', packet.id,
        'claim_id', anchor.claim_id, 'packet_sha256', packet.packet_sha256, 'contract_sha256', anchor.contract_sha256,
        'policy', settings)::text, 'UTF8')), 'hex');
    INSERT INTO research_progress_policies (packet_id, packet_sha256, claim_id, contract_sha256, policy, policy_sha256)
    VALUES (packet.id, packet.packet_sha256, anchor.claim_id, anchor.contract_sha256, settings, digest)
    ON CONFLICT (policy_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_progress_policies p WHERE p.policy_sha256 = digest;
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.public_lean_policy_in_workspace(policy jsonb) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (SELECT FROM current_workspace_binding() binding WHERE binding.workspace_id IS NOT NULL
        AND $1 #> '{lean,public_workspace}' = jsonb_build_array(binding.workspace_id, binding.database_id))
$$;

CREATE FUNCTION rm.lean_progress_queue_for_model(batch integer, policy_ids uuid[], connected_model boolean)
RETURNS TABLE (id uuid, statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT policy.id, inputs.* FROM research_progress_status policy
    CROSS JOIN LATERAL lean_progress_inputs(policy.id) inputs
    WHERE policy.policy ? 'lean' AND policy.state = 'awaiting_evidence'
      AND (policy.policy #> '{lean,public_model}' IS NOT NULL) = $3
      AND (NOT $3 OR ($2 IS NOT NULL AND public_lean_policy_in_workspace(policy.policy)))
      AND ($2 IS NULL OR policy.id = ANY($2))
      AND NOT EXISTS (SELECT FROM lean_progress_runs completed
                       WHERE completed.policy_id = policy.id AND completed.inputs_sha256 = inputs.inputs_sha256)
    ORDER BY (policy.policy ->> 'priority')::integer DESC, policy.approved_at, policy.id
    LIMIT greatest(0, least($1, 64))
$$;

CREATE OR REPLACE FUNCTION rm.lean_progress_queue(batch integer, policy_ids uuid[])
RETURNS TABLE (id uuid, statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT * FROM lean_progress_queue_for_model($1, $2, false)
$$;

CREATE FUNCTION rm.lean_progress_public_model_queue(batch integer, policy_ids uuid[])
RETURNS TABLE (id uuid, statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT * FROM lean_progress_queue_for_model($1, $2, true)
$$;

-- Keep the existing pass lock implementation and narrow access before acquiring it.
ALTER FUNCTION rm.lock_lean_progress(uuid, text) RENAME TO claim_lean_progress_pass;

CREATE FUNCTION rm.lock_lean_progress(policy_id uuid, inputs_sha256 text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT CASE WHEN EXISTS (SELECT FROM research_progress_policies p
        WHERE p.id = $1 AND p.policy #> '{lean,public_model}' IS NULL)
        THEN claim_lean_progress_pass($1, $2) ELSE false END
$$;

CREATE FUNCTION rm.lock_public_model_lean_progress(policy_id uuid, inputs_sha256 text) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT CASE WHEN EXISTS (SELECT FROM research_progress_policies p
        WHERE p.id = $1 AND p.policy #> '{lean,public_model}' IS NOT NULL
        AND public_lean_policy_in_workspace(p.policy))
        THEN claim_lean_progress_pass($1, $2) ELSE false END
$$;

-- Check a model-bound proposal at insertion and again while claiming its call slot.
CREATE FUNCTION rm.require_public_lean_policy_job(job rm.jobs, signed rm.research_progress_policies)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF signed.id IS NULL
        OR signed.claim_id IS DISTINCT FROM job.claim_id
        OR signed.contract_sha256 IS DISTINCT FROM job.contract_sha256
        OR job.kind IS DISTINCT FROM 'prove'
        OR job.budget -> 'max_model_calls' IS DISTINCT FROM '1'::jsonb
        OR job.payload -> 'lean_search' IS DISTINCT FROM 'true'::jsonb
        OR job.payload -> 'lean_author' IS DISTINCT FROM 'true'::jsonb
        OR job.payload ? 'private_model' OR job.payload ? 'workbench_guidance_ids'
        OR signed.packet_id::text IS DISTINCT FROM job.payload ->> 'formalization_id'
        OR signed.packet_sha256 IS DISTINCT FROM job.payload ->> 'formalization_sha256'
        OR signed.policy #>> '{lean,statement_id}' IS DISTINCT FROM job.payload ->> 'author_statement_id'
        OR signed.policy #>> '{lean,statement_sha256}' IS DISTINCT FROM job.payload ->> 'author_statement_sha256'
        OR signed.policy #> '{lean,public_model}' IS NULL
        OR signed.policy #> '{lean,public_model}' IS DISTINCT FROM job.payload -> 'public_model'
        OR NOT public_lean_policy_in_workspace(signed.policy)
        OR NOT EXISTS (SELECT FROM research_progress_status p WHERE p.id = signed.id AND p.state = 'awaiting_evidence')
    THEN
        RAISE EXCEPTION 'a connected proof job needs its exact active signed model and scope' USING ERRCODE = '23514';
    END IF;
    PERFORM require_lean_review((job.payload ->> 'author_statement_id')::uuid, true);
END $$;

CREATE FUNCTION rm.guard_public_lean_policy_job() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE signed research_progress_policies;
BEGIN
    SELECT * INTO signed FROM research_progress_policies p
    WHERE p.id::text = NEW.payload ->> 'lean_progress_policy_id';
    PERFORM require_public_lean_policy_job(NEW, signed);
    RETURN NEW;
END $$;

CREATE TRIGGER check_public_lean_policy_job BEFORE INSERT ON rm.jobs
    FOR EACH ROW WHEN (NEW.payload ? 'public_model') EXECUTE FUNCTION rm.guard_public_lean_policy_job();

CREATE OR REPLACE FUNCTION rm.reserve_lean_route_policy_call() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE bound_policy_id uuid := (NEW.payload ->> 'lean_progress_policy_id')::uuid;
DECLARE signed research_progress_policies;
DECLARE allowance integer;
BEGIN
    -- Serialize reservations for parallel lanes or a selected connection, including replacement leases.
    SELECT p.* INTO signed FROM research_progress_policies p WHERE p.id = bound_policy_id FOR UPDATE;
    IF NEW.payload ? 'public_model' OR signed.policy #> '{lean,public_model}' IS NOT NULL THEN
        PERFORM require_public_lean_policy_job(NEW, signed);
    ELSIF NOT FOUND OR signed.claim_id <> NEW.claim_id OR signed.contract_sha256 <> NEW.contract_sha256
        OR signed.policy #>> '{lean,statement_id}' IS DISTINCT FROM NEW.payload ->> 'author_statement_id'
        OR signed.policy #>> '{lean,statement_sha256}' IS DISTINCT FROM NEW.payload ->> 'author_statement_sha256'
        OR NOT coalesce((signed.policy #>> '{lean,route_parallelism}')::integer BETWEEN 2 AND 3, false)
        OR NOT EXISTS (SELECT FROM research_progress_approvals a WHERE a.policy_id = signed.id)
        OR EXISTS (SELECT FROM research_progress_stops t WHERE t.policy_id = signed.id) THEN
        RAISE EXCEPTION 'a model proposal needs its active signed Lean route policy' USING ERRCODE = '23514';
    END IF;
    allowance := (signed.policy #>> '{lean,model_rounds}')::integer;
    IF (SELECT count(*) FROM lean_search_call_slots slot JOIN jobs job ON job.id = slot.job_id
        WHERE (slot.claim_id, slot.contract_sha256) = (NEW.claim_id, NEW.contract_sha256)
            AND job.payload ->> 'lean_progress_policy_id' = bound_policy_id::text) >= allowance THEN
        RAISE EXCEPTION 'Signed Lean policy call allowance reached.' USING ERRCODE = 'RMB01';
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION rm.guard_public_lean_model_receipt() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE identity jsonb;
BEGIN
    SELECT job.payload -> 'public_model' INTO identity FROM jobs job WHERE job.id = NEW.job_id;
    IF identity IS NOT NULL AND (
        NEW.finding NOT IN ('numeric_observation', 'no_result')
        OR (NEW.model_provider IS NULL AND NEW.finding <> 'no_result')
        OR (NEW.model_provider IS NOT NULL AND (
            NEW.transcript_sha256 IS NULL
            OR NEW.model_provider IS DISTINCT FROM identity ->> 'provider'
            OR NEW.model_parameters ->> 'requested_model' IS DISTINCT FROM identity ->> 'model'
        ))
    ) THEN
        RAISE EXCEPTION 'connected proof proposal receipt must match its configured provider and request'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER check_public_lean_model_receipt BEFORE INSERT ON rm.attempts
    FOR EACH ROW WHEN (NEW.role = 'prove') EXECUTE FUNCTION rm.guard_public_lean_model_receipt();

REVOKE ALL ON FUNCTION rm.claim_lean_progress_pass(uuid, text) FROM PUBLIC, rm_scheduler;
REVOKE ALL ON FUNCTION rm.propose_public_model_lean_progress(uuid, integer, integer, jsonb),
    rm.public_lean_policy_in_workspace(jsonb), rm.lean_progress_queue_for_model(integer, uuid[], boolean),
    rm.lean_progress_public_model_queue(integer, uuid[]), rm.lock_lean_progress(uuid, text),
    rm.lock_public_model_lean_progress(uuid, text), rm.require_public_lean_policy_job(rm.jobs, rm.research_progress_policies),
    rm.guard_public_lean_policy_job(), rm.guard_public_lean_model_receipt() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_public_model_lean_progress(uuid, integer, integer, jsonb) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.lean_progress_public_model_queue(integer, uuid[]), rm.lock_lean_progress(uuid, text),
    rm.lock_public_model_lean_progress(uuid, text) TO rm_scheduler;
