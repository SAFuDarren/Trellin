CREATE TABLE rm.exploration_goal_links (
    goal_review_id uuid PRIMARY KEY REFERENCES rm.formalization_reviews(id),
    basis_packet_id uuid NOT NULL UNIQUE REFERENCES rm.formalization_packets(id)
);
CREATE TRIGGER exploration_goal_links_append_only BEFORE UPDATE OR DELETE ON rm.exploration_goal_links
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE TABLE rm.flow_derivation_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    verification_attempt_id uuid NOT NULL REFERENCES rm.attempts(id),
    proof_attempt_id uuid NOT NULL REFERENCES rm.attempts(id),
    item_key text NOT NULL CHECK (item_key IN ('flow_attains_target', 'cut_upper_bound')),
    "natural" text NOT NULL,
    formal jsonb NOT NULL CHECK (jsonb_typeof(formal) = 'object'),
    premise_item_ids text[] NOT NULL,
    artifact_uri text NOT NULL,
    artifact_sha256 rm.sha256_hex NOT NULL,
    verification_uri text NOT NULL,
    verification_sha256 rm.sha256_hex NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT flow_derivations_attempt_item UNIQUE (packet_id, verification_attempt_id, item_key)
);
CREATE TRIGGER flow_derivation_records_append_only BEFORE UPDATE OR DELETE ON rm.flow_derivation_records
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

-- Review history remains visible; the reusable view only exposes results under a ready packet.
CREATE VIEW rm.verified_derivations WITH (security_barrier = true) AS
    SELECT d.* FROM rm.flow_derivation_records d
    JOIN rm.formalization_status p ON p.id = d.packet_id WHERE p.proof_ready;

CREATE FUNCTION rm.register_flow_derivations(packet_id uuid, verification_attempt_id uuid, verified_result jsonb)
RETURNS SETOF rm.flow_derivation_records
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE source record;
DECLARE existing_cut jsonb;
DECLARE side jsonb;
DECLARE target jsonb;
BEGIN
    -- This boundary is verifier-only. The verifier replays the content-addressed
    -- certificate; PostgreSQL binds its result to the immutable run and packet.
    SELECT packet.claim_id, packet.packet, claim.contract_snapshot, proof_job.payload AS basis,
           proof.id AS proof_id, proof.artifact_uri, proof.artifact_sha256,
           verification.artifact_uri AS verification_uri, verification.artifact_sha256 AS verification_sha256
    INTO source FROM formalization_packets packet
    JOIN claims claim ON claim.id = packet.claim_id
    JOIN attempts verification ON verification.claim_id = claim.id
      AND verification.contract_sha256 = claim.contract_sha256
    JOIN jobs verification_job ON verification_job.id = verification.job_id
    JOIN attempts proof ON proof.id = (verification_job.payload ->> 'candidate_attempt_id')::uuid
      AND proof.claim_id = claim.id AND proof.contract_sha256 = claim.contract_sha256
    JOIN jobs proof_job ON proof_job.id = proof.job_id
    WHERE packet.id = register_flow_derivations.packet_id
      AND verification.id = register_flow_derivations.verification_attempt_id
      AND claim.domain = 'fixed-directed-max-flow' AND claim.sensitivity = 'public'
      AND verification.role = 'verify' AND verification.finding = 'exact_proof'
      AND verification.model_provider IS NULL AND verification_job.status = 'succeeded'
      AND verification_job.payload ->> 'checker_name' = 'exact-flow-cut'
      AND verification_job.payload ->> 'checker_version' = 'v0a-2'
      AND verification_job.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$'
      AND proof.role = 'prove' AND proof.finding = 'exact_proof'
      AND proof.artifact_uri = verification_job.payload #>> '{candidate,uri}'
      AND proof.artifact_sha256 = verification_job.payload #>> '{candidate,sha256}'
      AND proof_job.payload @> jsonb_build_object('formalization_id', packet.id,
          'formalization_sha256', packet.packet_sha256);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'derived facts require a successful exact verification of this packet and proof'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(source.claim_id, source.basis);
    IF NOT source.packet -> 'items' @> '[{"id":"H1","kind":"hypothesis"},
        {"id":"H2","kind":"hypothesis"},{"id":"H3","kind":"hypothesis"}]'::jsonb THEN
        RAISE EXCEPTION 'exact flow derivation requires the network, feasibility and value premises'
            USING ERRCODE = '23514';
    END IF;
    target := source.contract_snapshot #> '{small_case_spec,target_value}';
    side := verified_result -> 'source_side';
    IF jsonb_typeof(target) IS DISTINCT FROM 'number'
        OR verified_result -> 'value' IS DISTINCT FROM target
        OR verified_result -> 'flow_value' IS DISTINCT FROM target
        OR verified_result -> 'cut_capacity' IS DISTINCT FROM target
        OR jsonb_typeof(side) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'exact flow facts must match the frozen value and certificate scope' USING ERRCODE = '23514';
    END IF;
    IF NOT side ? (source.contract_snapshot #>> '{small_case_spec,source}')
        OR side ? (source.contract_snapshot #>> '{small_case_spec,sink}')
        OR NOT (source.contract_snapshot #> '{small_case_spec,vertices}') @> side
        OR jsonb_array_length(side) <> (SELECT count(DISTINCT vertex) FROM jsonb_array_elements_text(side) vertex)
        OR verified_result -> 'edges_checked' IS DISTINCT FROM
            to_jsonb(jsonb_array_length(source.contract_snapshot #> '{small_case_spec,edges}')) THEN
        RAISE EXCEPTION 'exact cut must partition the frozen network' USING ERRCODE = '23514';
    END IF;
    IF (SELECT coalesce(sum((edge ->> 2)::numeric), 0)
        FROM jsonb_array_elements(source.contract_snapshot #> '{small_case_spec,edges}') edge
        WHERE side ? (edge ->> 0) AND NOT side ? (edge ->> 1)) IS DISTINCT FROM (target::text)::numeric THEN
        RAISE EXCEPTION 'derived cut capacity does not match the frozen network' USING ERRCODE = '23514';
    END IF;

    -- The same committed verification always recovers the same pair of facts.
    PERFORM pg_advisory_xact_lock(hashtext('rm-flow-derivations'), hashtext(verification_attempt_id::text));
    SELECT d.formal -> 'source_side' INTO existing_cut FROM flow_derivation_records d
    WHERE d.packet_id = register_flow_derivations.packet_id
      AND d.verification_attempt_id = register_flow_derivations.verification_attempt_id
      AND d.item_key = 'cut_upper_bound';
    IF FOUND AND existing_cut IS DISTINCT FROM side THEN
        RAISE EXCEPTION 'verification attempt already has different derived facts' USING ERRCODE = '23514';
    END IF;
    INSERT INTO flow_derivation_records (packet_id, verification_attempt_id, proof_attempt_id, item_key, "natural",
        formal, premise_item_ids, artifact_uri, artifact_sha256, verification_uri, verification_sha256)
    SELECT register_flow_derivations.packet_id, register_flow_derivations.verification_attempt_id,
        source.proof_id, node.item_key, node."natural", node.formal, ARRAY['H1', 'H2', 'H3'],
        source.artifact_uri, source.artifact_sha256, source.verification_uri, source.verification_sha256
    FROM (VALUES
        ('flow_attains_target', format('A feasible real flow attains value %s.', target),
            jsonb_build_object('type', 'feasible_flow_value', 'quantity', 'flow_value',
                'value_type', 'real', 'quantifier', 'exists', 'relation', 'eq', 'value', target)),
        ('cut_upper_bound', format('The exhibited cut bounds the value of every feasible real flow above by %s.', target),
            jsonb_build_object('type', 'cut_upper_bound', 'quantity', 'flow_value',
                'value_type', 'real', 'quantifier', 'forall', 'relation', 'le', 'value', target, 'source_side', side))
    ) AS node(item_key, "natural", formal)
    ON CONFLICT ON CONSTRAINT flow_derivations_attempt_item DO NOTHING;

    RETURN QUERY SELECT d.* FROM flow_derivation_records d WHERE d.packet_id = register_flow_derivations.packet_id
        AND d.verification_attempt_id = register_flow_derivations.verification_attempt_id ORDER BY d.item_key;
END $$;

CREATE FUNCTION rm.matches_exploration_premises(basis jsonb, goal jsonb)
RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM (VALUES ('H1'), ('H2'), ('H3')) required(id) WHERE NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(basis -> 'items') source
            CROSS JOIN jsonb_array_elements(CASE WHEN jsonb_typeof(goal -> 'items') = 'array'
                THEN goal -> 'items' ELSE '[]'::jsonb END) selected
            WHERE source ->> 'id' = required.id AND selected ->> 'id' = required.id
              AND source ->> 'kind' = 'hypothesis' AND selected ->> 'kind' = 'hypothesis'
              AND source -> 'natural' = selected -> 'natural' AND source -> 'formal' = selected -> 'formal'
        )
    )
$$;

-- A basis has no selected goal. Human choice creates a separate, still-unapproved
-- goal packet and remembers the already-approved network it came from.
CREATE FUNCTION rm.choose_exploration(basis_packet_id uuid, expected_version integer, expected_sha256 text,
                                      goal_packet jsonb, claim_contract jsonb)
RETURNS rm.formalization_packets
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE basis formalization_packets;
DECLARE chosen formalization_packets;
DECLARE original formalization_packets;
BEGIN
    SELECT * INTO STRICT basis FROM formalization_packets p WHERE p.id = basis_packet_id;
    IF basis.version IS DISTINCT FROM expected_version OR basis.packet_sha256 IS DISTINCT FROM expected_sha256
        OR basis.claim_id IS NOT NULL OR basis.packet ->> 'mode' IS DISTINCT FROM 'exploration'
        OR basis.packet ->> 'sensitivity' IS DISTINCT FROM 'public' THEN
        RAISE EXCEPTION 'goal selection requires the exact approved public exploration basis' USING ERRCODE = '23514';
    END IF;
    PERFORM 1 FROM formalization_reviews r WHERE r.id = basis.review_id AND r.active_version = basis.version FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM formalization_status s WHERE s.id = basis.id
                                AND s.approved AND s.pending_clarifications = 0) THEN
        RAISE EXCEPTION 'goal selection requires a current approved basis without open clarifications'
            USING ERRCODE = '23514';
    END IF;
    IF claim_contract ->> 'domain' IS DISTINCT FROM basis.packet ->> 'domain'
        OR claim_contract ->> 'sensitivity' IS DISTINCT FROM 'public'
        OR (claim_contract -> 'small_case_spec') - 'target_value' IS DISTINCT FROM basis.packet -> 'network'
        OR NOT matches_exploration_premises(basis.packet, goal_packet) THEN
        RAISE EXCEPTION 'selected goal must keep the network and premises of its approved exploration basis' USING ERRCODE = '23514';
    END IF;
    SELECT p.* INTO original FROM exploration_goal_links link
    JOIN formalization_packets p ON p.review_id = link.goal_review_id AND p.version = 1
    WHERE link.basis_packet_id = basis.id;
    IF FOUND THEN
        IF original.packet - 'claim_id' IS DISTINCT FROM goal_packet - 'claim_id'
            OR original.claim_contract - 'claim_id' IS DISTINCT FROM claim_contract - 'claim_id' THEN
            RAISE EXCEPTION 'the exploration basis is already linked to a different goal selection'
                USING ERRCODE = '23514';
        END IF;
        SELECT p.* INTO STRICT chosen FROM formalization_packets p
        JOIN formalization_reviews r ON r.id = p.review_id AND r.active_version = p.version
        WHERE p.review_id = original.review_id;
        RETURN chosen;
    END IF;
    UPDATE formalization_reviews SET gate_revision = gate_revision + 1 WHERE id = basis.review_id;
    chosen := propose_formalization(goal_packet, claim_contract);
    INSERT INTO exploration_goal_links (goal_review_id, basis_packet_id) VALUES (chosen.review_id, basis.id);
    RETURN chosen;
END $$;

CREATE OR REPLACE FUNCTION rm.propose_formalization(packet jsonb, canonical_claim jsonb DEFAULT NULL,
                                         previous_packet_id uuid DEFAULT NULL)
RETURNS rm.formalization_packets
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE previous formalization_packets;
DECLARE saved formalization_packets;
DECLARE series_id uuid;
DECLARE series_version integer;
DECLARE subject_id uuid;
DECLARE exploration boolean := coalesce(packet ->> 'mode' = 'exploration', false);
DECLARE original_basis jsonb;
BEGIN
    IF packet -> 'format_version' IS DISTINCT FROM '1'::jsonb
        OR jsonb_typeof(packet -> 'items') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'expected a version 1 formalization packet with an items array' USING ERRCODE = '22023';
    END IF;
    IF exploration = EXISTS (SELECT 1 FROM jsonb_array_elements(packet -> 'items') i WHERE i ->> 'kind' = 'goal') THEN
        RAISE EXCEPTION 'a goal review requires a goal; an exploration review only records its basis'
            USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(packet -> 'items') = 0
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(packet -> 'items') i WHERE
            jsonb_typeof(i -> 'id') IS DISTINCT FROM 'string' OR nullif(btrim(i ->> 'id'), '') IS NULL
            OR jsonb_typeof(i -> 'kind') IS DISTINCT FROM 'string'
            OR i ->> 'kind' NOT IN ('goal', 'definition', 'hypothesis', 'theorem')
            OR jsonb_typeof(i -> 'natural') IS DISTINCT FROM 'string' OR nullif(btrim(i ->> 'natural'), '') IS NULL
            OR jsonb_typeof(i -> 'formal') IS DISTINCT FROM 'object'
            OR jsonb_typeof(i -> 'status') IS DISTINCT FROM 'string'
            OR i ->> 'status' NOT IN ('stated', 'needs_confirmation')
            OR (i ? 'question' AND (i ->> 'status' <> 'needs_confirmation'
                OR jsonb_typeof(i -> 'question') IS DISTINCT FROM 'string'
                OR nullif(btrim(i ->> 'question'), '') IS NULL)))
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(packet -> 'items') i GROUP BY i ->> 'id' HAVING count(*) > 1)
    THEN
        RAISE EXCEPTION 'packet requires nonempty, uniquely named items with structured stated or unresolved content'
            USING ERRCODE = '22023';
    END IF;

    IF previous_packet_id IS NULL THEN
        INSERT INTO formalization_reviews DEFAULT VALUES RETURNING id, active_version INTO series_id, series_version;
    ELSE
        SELECT * INTO STRICT previous FROM formalization_packets p WHERE p.id = previous_packet_id;
        IF exploration IS DISTINCT FROM coalesce(previous.packet ->> 'mode' = 'exploration', false) THEN
            RAISE EXCEPTION 'a packet revision cannot change its review mode' USING ERRCODE = '23514';
        END IF;
        UPDATE formalization_reviews r SET active_version = r.active_version + 1
        WHERE r.id = previous.review_id AND r.active_version = previous.version
        RETURNING r.id, r.active_version INTO series_id, series_version;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM formalization_approvals a WHERE a.packet_id = previous.id) THEN
            RAISE EXCEPTION 'revision requires the current unapproved packet' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF canonical_claim IS NULL THEN
        canonical_claim := previous.claim_contract;
        subject_id := previous.claim_id;
    ELSE
        subject_id := insert_claim(canonical_claim, 'researcher_claim');
    END IF;
    IF exploration AND (subject_id IS NOT NULL OR packet ->> 'sensitivity' IS DISTINCT FROM 'public'
        OR packet ->> 'domain' IS DISTINCT FROM 'fixed-directed-max-flow'
        OR jsonb_typeof(packet -> 'network') IS DISTINCT FROM 'object') THEN
        RAISE EXCEPTION 'exploration review needs a public network without a bound claim' USING ERRCODE = '23514';
    END IF;
    SELECT p.packet INTO original_basis FROM exploration_goal_links link
    JOIN formalization_packets p ON p.id = link.basis_packet_id WHERE link.goal_review_id = series_id;
    IF FOUND AND ((canonical_claim -> 'small_case_spec') - 'target_value' IS DISTINCT FROM original_basis -> 'network'
        OR canonical_claim ->> 'domain' IS DISTINCT FROM original_basis ->> 'domain'
        OR canonical_claim ->> 'sensitivity' IS DISTINCT FROM 'public'
        OR NOT matches_exploration_premises(original_basis, packet)) THEN
        RAISE EXCEPTION 'selected goal must keep its approved exploration basis' USING ERRCODE = '23514';
    END IF;
    IF packet ->> 'claim_id' IS DISTINCT FROM subject_id::text THEN
        RAISE EXCEPTION 'packet claim_id does not match the proposed claim' USING ERRCODE = '23514';
    END IF;

    INSERT INTO formalization_packets (review_id, claim_id, version, packet, claim_contract, packet_sha256)
    VALUES (series_id, subject_id, series_version, packet, canonical_claim,
        encode(sha256(convert_to(jsonb_build_object('packet', packet, 'claim_contract', canonical_claim)::text, 'UTF8')), 'hex'))
    RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE OR REPLACE VIEW rm.formalization_status WITH (security_barrier = true) AS
    SELECT p.id, p.review_id, p.claim_id, p.version, p.packet, p.packet_sha256, p.proposed_by, p.proposed_at,
        a.packet_id IS NOT NULL AS approved, a.approved_by, a.approved_at,
        p.version <> r.active_version AS superseded, pending.count AS pending_clarifications,
        p.claim_id IS NOT NULL AND p.version = r.active_version AND a.packet_id IS NOT NULL
            AND pending.count = 0 AS proof_ready,
        a.confirmed_item_ids, a.reason AS approval_reason, link.basis_packet_id, basis.packet_sha256 AS basis_packet_sha256
    FROM rm.formalization_packets p
    JOIN rm.formalization_reviews r ON r.id = p.review_id
    LEFT JOIN rm.exploration_goal_links link ON link.goal_review_id = p.review_id
    LEFT JOIN rm.formalization_packets basis ON basis.id = link.basis_packet_id
    LEFT JOIN rm.formalization_approvals a ON a.packet_id = p.id
    CROSS JOIN LATERAL (
        SELECT count(*)::integer FROM rm.formalization_clarification_status c WHERE c.packet_id = p.id AND NOT c.resolved
    ) pending;

REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA rm FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.register_flow_derivations(uuid, uuid, jsonb) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.choose_exploration(uuid, integer, text, jsonb, jsonb) TO rm_human;
GRANT SELECT ON rm.verified_derivations TO rm_human, rm_scheduler, rm_verifier;
