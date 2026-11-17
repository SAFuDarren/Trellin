-- A statement can be mathematically admitted for local research while its
-- wording, formalization and proofs still belong to a private workspace.
CREATE TABLE rm.formal_item_public_releases (
    version_id uuid PRIMARY KEY REFERENCES rm.formal_item_versions(id),
    version_sha256 rm.sha256_hex NOT NULL,
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.formal_item_public_releases
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.release_formal_item(version_id uuid, expected_sha text, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved formal_item_public_releases;
BEGIN
    PERFORM advance_foundation();
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL
        OR NOT EXISTS (SELECT FROM formal_item_status item WHERE item.id = $1
            AND item.version_sha256 = $2 AND item.admitted AND NOT item.needs_recheck
            AND item.body ->> 'kind' IN ('definition','hypothesis','theorem')) THEN
        RAISE EXCEPTION 'release the selected admitted version with its reviewed hash and disclosure reason'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO formal_item_public_releases (version_id, version_sha256, actor_ref, reason)
        VALUES ($1,$2,$3,$4) ON CONFLICT ON CONSTRAINT formal_item_public_releases_pkey DO NOTHING
        RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM formal_item_public_releases release WHERE release.version_id = $1;
        IF ROW(saved.version_sha256,saved.actor_ref,saved.reason,saved.authenticated_role)
            IS DISTINCT FROM ROW($2,$3,$4,session_user) THEN
            RAISE EXCEPTION 'this version already has a different public release decision' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN $1;
END $$;

-- Derive origin from durable edges. A source label supplied by an operator is
-- insufficient without its matching intake confirmation or frozen packet.
CREATE VIEW rm.formal_item_sensitivity_origins AS
    SELECT v.id AS version_id, draft.body ->> 'sensitivity' AS sensitivity
    FROM rm.formal_item_versions v
    JOIN rm.research_intake_drafts draft ON draft.id::text = v.body #>> '{source,id}'
       AND draft.body #>> '{notes,sha256}' = v.body #>> '{source,sha256}'
    JOIN rm.research_intake_confirmations admitted ON admitted.intake_id = draft.id
    JOIN rm.research_goals goal ON goal.id = admitted.goal_id
       AND (goal.body -> 'basis_version_ids') ? v.id::text
    WHERE v.origin = 'human' AND v.body #>> '{source,kind}' = 'research_intake'
    UNION ALL
    SELECT used.version_id, coalesce(p.claim_contract ->> 'sensitivity', p.packet ->> 'sensitivity')
    FROM rm.packet_basis_items used
    JOIN rm.formal_item_versions v ON v.id = used.version_id AND v.origin = 'packet'
    JOIN rm.formalization_packets p ON p.id = used.packet_id
    UNION ALL
    SELECT exported.version_id, c.sensitivity FROM rm.lean_core_exports exported
    JOIN rm.evidence e ON e.id = exported.evidence_id JOIN rm.claims c ON c.id = e.claim_id
    UNION ALL
    SELECT derived.version_id, p.claim_contract ->> 'sensitivity' FROM rm.formal_lean_derivations derived
    JOIN rm.formalization_packets p ON p.id = derived.packet_id
    UNION ALL
    SELECT derived.version_id, p.claim_contract ->> 'sensitivity' FROM rm.formal_certificate_derivations derived
    JOIN rm.formalization_packets p ON p.id = derived.packet_id;

CREATE FUNCTION rm.require_public_model_basis(versions jsonb) RETURNS void
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF jsonb_typeof(versions) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'public model basis requires exact version IDs' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        WITH RECURSIVE edges(theorem_id, source_id) AS NOT MATERIALIZED (
            SELECT exported.version_id, used.version_id FROM lean_core_exports exported
            JOIN evidence e ON e.id = exported.evidence_id
            JOIN verification_basis_use used ON used.check_id = e.lean_check_id
            UNION ALL
            SELECT derived.version_id, used.version_id FROM formal_lean_derivations derived
            JOIN verification_basis_use used ON used.check_id = derived.check_id
            UNION ALL
            SELECT derived.version_id, used.version_id FROM formal_certificate_derivations derived
            JOIN flow_derivation_records fact ON fact.id = derived.derivation_id
            JOIN packet_basis_items used ON used.packet_id = fact.packet_id AND used.item_label = ANY(fact.premise_item_ids)
        ), needed(version_id) AS (
            SELECT id::uuid FROM jsonb_array_elements_text(versions) item(id)
            UNION
            SELECT edge.source_id FROM needed n JOIN edges edge ON edge.theorem_id = n.version_id
            WHERE NOT EXISTS (SELECT FROM formal_item_public_releases released WHERE released.version_id = n.version_id)
        )
        SELECT FROM needed n LEFT JOIN formal_item_versions item ON item.id = n.version_id
        WHERE NOT EXISTS (SELECT FROM formal_item_public_releases released WHERE released.version_id = n.version_id)
        AND (
            NOT EXISTS (SELECT FROM formal_item_sensitivity_origins origin WHERE origin.version_id = n.version_id
                        AND origin.sensitivity = 'public')
            OR EXISTS (
                SELECT FROM formal_item_sensitivity_origins origin
                JOIN formal_item_versions other ON other.id = origin.version_id
                WHERE origin.sensitivity = 'restricted'
                  AND (other.id = n.version_id OR (item.body ? 'formal'
                      AND other.body -> 'kind' = item.body -> 'kind'
                      AND other.body -> 'natural' = item.body -> 'natural'
                      AND other.body -> 'formal' = item.body -> 'formal'))
            )
        )
    ) THEN
        RAISE EXCEPTION 'the public model request includes restricted or unclassified basis content; release the exact version explicitly or check locally'
            USING ERRCODE = '23514';
    END IF;
END $$;

CREATE FUNCTION rm.require_public_model_packet(packet_id uuid) RETURNS void
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT require_public_model_basis(coalesce(jsonb_agg(item.version_id), '[]'::jsonb)) FROM (
        SELECT item_version_id AS version_id FROM get_packet_basis($1)
        UNION SELECT version_id FROM lean_core_imports WHERE packet_id = $1
        UNION SELECT version_id FROM packet_library_items WHERE packet_id = $1
        UNION SELECT version_id FROM packet_basis_items WHERE packet_id = $1
    ) item
$$;

CREATE FUNCTION rm.require_public_model_claim(claim_id uuid) RETURNS void
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT require_public_model_packet(packet.id) FROM formalization_status packet
    WHERE packet.claim_id = $1 AND NOT packet.superseded
$$;

CREATE FUNCTION rm.guard_job_model_inputs() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.budget -> 'max_model_calls' IS NOT NULL AND NEW.budget ->> 'max_model_calls' <> '0'
        AND (TG_OP = 'INSERT' OR NEW.status = 'running')
        AND EXISTS (SELECT FROM claims c WHERE c.id = NEW.claim_id AND c.sensitivity = 'public') THEN
        PERFORM lock_live_foundation();
        PERFORM require_public_model_claim(NEW.claim_id);
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER check_public_model_inputs BEFORE INSERT OR UPDATE OF status ON rm.jobs
    FOR EACH ROW EXECUTE FUNCTION rm.guard_job_model_inputs();

REVOKE ALL ON FUNCTION rm.release_formal_item(uuid,text,text,text), rm.require_public_model_basis(jsonb),
    rm.require_public_model_packet(uuid), rm.require_public_model_claim(uuid), rm.guard_job_model_inputs() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.release_formal_item(uuid,text,text,text), rm.require_public_model_basis(jsonb) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.require_public_model_packet(uuid), rm.require_public_model_claim(uuid) TO rm_human, rm_verifier;
GRANT SELECT ON rm.formal_item_public_releases TO rm_human;
