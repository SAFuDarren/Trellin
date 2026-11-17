CREATE FUNCTION rm.propose_lean_route_progress(statement_id uuid, model_rounds integer, priority integer,
    route_parallelism integer, route_stall_limit integer, private_identity jsonb)
RETURNS rm.research_progress_policies
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE packet formalization_packets;
DECLARE settings jsonb;
DECLARE lean_settings jsonb;
DECLARE digest text;
DECLARE saved research_progress_policies;
BEGIN
    anchor := require_lean_review($1, true);
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = anchor.packet_id;
    IF anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR NOT coalesce($4 BETWEEN 2 AND 3, false)
        OR NOT coalesce($2 BETWEEN $4 AND 8, false)
        OR NOT coalesce($3 BETWEEN -100 AND 100, false)
        OR NOT coalesce($5 BETWEEN 1 AND 3, false)
        OR ($6 IS NULL AND NOT EXISTS (
            SELECT FROM claims c WHERE c.id = anchor.claim_id AND c.sensitivity = 'public'
        )) OR ($6 IS NOT NULL AND NOT private_model_identity_valid($6)) THEN
        RAISE EXCEPTION 'parallel Lean advancement needs confirmed scope, bounded rounds and signed lane settings'
            USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    IF $6 IS NULL THEN
        PERFORM require_current_basis(packet.id, '[]'::jsonb);
    ELSE
        PERFORM require_private_model_packet(packet.id);
    END IF;
    lean_settings := jsonb_build_object('statement_id', anchor.id, 'statement_sha256', anchor.statement_sha256,
        'model_rounds', $2, 'route_parallelism', $4, 'route_stall_limit', $5);
    IF $6 IS NOT NULL THEN
        lean_settings := lean_settings || jsonb_build_object('private_model', $6);
    END IF;
    settings := jsonb_build_object('metric', 'exact-closure', 'actions', '[]'::jsonb, 'max_rounds', 0,
        'proof_timeout_seconds', 0, 'proof_max_attempts', 0, 'budget_seconds', 0, 'priority', $3,
        'lean', lean_settings);
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

CREATE FUNCTION rm.reserve_lean_route_policy_call() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE bound_policy_id uuid := (NEW.payload ->> 'lean_progress_policy_id')::uuid;
DECLARE signed research_progress_policies;
DECLARE allowance integer;
BEGIN
    -- Serialize reservations for this policy, including a replacement lease.
    SELECT p.* INTO signed FROM research_progress_policies p WHERE p.id = bound_policy_id FOR UPDATE;
    IF NOT FOUND OR signed.claim_id <> NEW.claim_id OR signed.contract_sha256 <> NEW.contract_sha256
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
        RAISE EXCEPTION 'Signed Lean route policy call allowance reached.' USING ERRCODE = 'RMB01';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER reserve_lean_route_policy_call BEFORE UPDATE OF lease_generation ON rm.jobs
    FOR EACH ROW WHEN (NEW.kind = 'prove' AND NEW.payload -> 'lean_search' = 'true'::jsonb
        AND NEW.payload ? 'lean_progress_policy_id' AND NEW.lease_generation > OLD.lease_generation)
    EXECUTE FUNCTION rm.reserve_lean_route_policy_call();

REVOKE ALL ON FUNCTION rm.propose_lean_route_progress(uuid,integer,integer,integer,integer,jsonb),
    rm.reserve_lean_route_policy_call() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_lean_route_progress(uuid,integer,integer,integer,integer,jsonb) TO rm_human;
