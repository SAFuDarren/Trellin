-- Lean advancement uses an existing signed progress policy and the ordinary
-- proof/model queues. An observation suppresses rerunning the same inputs.
CREATE TABLE rm.lean_progress_runs (
    policy_id uuid NOT NULL REFERENCES rm.research_progress_policies(id),
    inputs_sha256 rm.sha256_hex NOT NULL,
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (policy_id, inputs_sha256)
);
CREATE TRIGGER lean_progress_runs_immutable BEFORE UPDATE OR DELETE ON rm.lean_progress_runs
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
REVOKE ALL ON rm.lean_progress_runs FROM PUBLIC;

CREATE VIEW rm.lean_progress_last_run WITH (security_barrier = true) AS
    SELECT DISTINCT ON (policy_id) policy_id, inputs_sha256, result, recorded_at
    FROM rm.lean_progress_runs ORDER BY policy_id, recorded_at DESC, inputs_sha256;
REVOKE ALL ON rm.lean_progress_last_run FROM PUBLIC;
GRANT SELECT ON rm.lean_progress_last_run TO rm_human, rm_scheduler, rm_verifier;

CREATE FUNCTION rm.propose_lean_progress(statement_id uuid, model_rounds integer, priority integer)
RETURNS rm.research_progress_policies
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE packet formalization_packets;
DECLARE settings jsonb;
DECLARE digest text;
DECLARE saved research_progress_policies;
BEGIN
    anchor := require_lean_review($1, true);
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = anchor.packet_id;
    IF anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR NOT coalesce($2 BETWEEN 0 AND 8, false)
        OR NOT coalesce($3 BETWEEN -100 AND 100, false)
        OR ($2 > 0 AND NOT EXISTS (
            SELECT FROM claims c WHERE c.id = anchor.claim_id AND c.sensitivity = 'public'
        )) THEN
        RAISE EXCEPTION 'Lean advancement needs a confirmed statement and a bounded public model policy'
            USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(packet.id, '[]'::jsonb);
    settings := jsonb_build_object('metric', 'exact-closure', 'actions', '[]'::jsonb, 'max_rounds', 0,
        'proof_timeout_seconds', 0, 'proof_max_attempts', 0, 'budget_seconds', 0, 'priority', $3,
        'lean', jsonb_build_object('statement_id', anchor.id, 'statement_sha256', anchor.statement_sha256,
                                  'model_rounds', $2));
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

CREATE FUNCTION rm.lean_progress_inputs(policy_id uuid)
RETURNS TABLE (statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT anchor.id, (policy.policy #>> '{lean,model_rounds}')::integer,
        encode(sha256(convert_to(jsonb_build_array(anchor.statement_sha256, basis.ids,
            lemmas.ids, exports.ids, reductions.signals)::text, 'UTF8')), 'hex')
    FROM research_progress_status policy
    JOIN lean_statement_status anchor ON anchor.id::text = policy.policy #>> '{lean,statement_id}'
        AND anchor.statement_sha256 = policy.policy #>> '{lean,statement_sha256}' AND anchor.check_ready
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(b.item_version_id ORDER BY b.item_version_id), '[]'::jsonb) AS ids
        FROM get_packet_basis(policy.packet_id) b WHERE b.body ->> 'format' IS DISTINCT FROM 'lean-native-proof'
    ) basis
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(l.id ORDER BY l.id), '[]'::jsonb) AS ids
        FROM local_lean_lemma_status(anchor.id) l WHERE l.status = 'verified'
    ) lemmas
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(native.id ORDER BY native.id), '[]'::jsonb) AS ids
        FROM lean_search_native_theorems(policy.packet_id, (policy.policy #>> '{lean,model_rounds}')::integer > 0) native
    ) exports
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(jsonb_build_array(r.implication_id, r.status) ORDER BY r.implication_id),
                        '[]'::jsonb) AS signals FROM lean_reduction_status(anchor.id) r
    ) reductions
    WHERE policy.id = $1 AND policy.policy ? 'lean' AND policy.state = 'awaiting_evidence'
$$;

CREATE FUNCTION rm.lean_progress_queue(batch integer, policy_ids uuid[])
RETURNS TABLE (id uuid, statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT policy.id, inputs.* FROM research_progress_status policy
    CROSS JOIN LATERAL lean_progress_inputs(policy.id) inputs
    WHERE policy.policy ? 'lean' AND policy.state = 'awaiting_evidence'
      AND ($2 IS NULL OR policy.id = ANY($2))
      AND NOT EXISTS (SELECT FROM lean_progress_runs completed
                       WHERE completed.policy_id = policy.id AND completed.inputs_sha256 = inputs.inputs_sha256)
    ORDER BY (policy.policy ->> 'priority')::integer DESC, policy.approved_at, policy.id
    LIMIT greatest(0, least($1, 64))
$$;

CREATE FUNCTION rm.lock_lean_progress(policy_id uuid, inputs_sha256 text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE lock_key bigint := hashtextextended($1::text, 4801);
BEGIN
    IF NOT pg_try_advisory_lock(lock_key) THEN
        RETURN false;
    END IF;
    IF EXISTS (SELECT FROM lean_progress_inputs($1) i WHERE i.inputs_sha256 = $2)
        AND NOT EXISTS (SELECT FROM lean_progress_runs done WHERE done.policy_id = $1 AND done.inputs_sha256 = $2)
    THEN
        RETURN true;
    END IF;
    PERFORM pg_advisory_unlock(lock_key);
    RETURN false;
END $$;

CREATE FUNCTION rm.unlock_lean_progress(policy_id uuid) RETURNS boolean
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT pg_advisory_unlock(hashtextextended($1::text, 4801))
$$;

CREATE FUNCTION rm.finish_lean_progress(policy_id uuid, inputs_sha256 text, result jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT FROM research_progress_policies p JOIN research_progress_approvals a ON a.policy_id = p.id
                   WHERE p.id = $1 AND p.policy ? 'lean')
        OR NOT coalesce($2 ~ '^[0-9a-f]{64}$', false)
        OR jsonb_typeof($3) IS DISTINCT FROM 'object'
        OR NOT $3 ? 'status'
        OR octet_length($3::text) > 64000 THEN
        RAISE EXCEPTION 'a Lean advancement observation needs a signed policy and a bounded result'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_progress_runs (policy_id, inputs_sha256, result) VALUES ($1, $2, $3)
    ON CONFLICT ON CONSTRAINT lean_progress_runs_pkey DO NOTHING;
END $$;

REVOKE ALL ON FUNCTION rm.propose_lean_progress(uuid,integer,integer), rm.lean_progress_inputs(uuid),
    rm.lean_progress_queue(integer,uuid[]), rm.lock_lean_progress(uuid,text),
    rm.unlock_lean_progress(uuid), rm.finish_lean_progress(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_lean_progress(uuid,integer,integer) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.lean_progress_queue(integer,uuid[]), rm.lock_lean_progress(uuid,text),
    rm.unlock_lean_progress(uuid), rm.finish_lean_progress(uuid,text,jsonb) TO rm_scheduler;
