CREATE TABLE rm.route_condition_local_derivations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL REFERENCES rm.research_condition_requests(id),
    lemma_id uuid NOT NULL REFERENCES rm.local_lean_lemmas(id),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (request_id, lemma_id)
);
CREATE INDEX route_condition_local_latest ON rm.route_condition_local_derivations (request_id, recorded_at DESC, id DESC);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.route_condition_local_derivations
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.local_route_condition_current(request_id uuid, lemma_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM research_condition_requests requested
        JOIN research_goal_selections selected ON selected.goal_id = requested.goal_id
        JOIN local_lean_lemmas stored ON stored.id = $2
        JOIN lean_statements statement ON statement.id = stored.statement_id
             AND (statement.claim_id, statement.packet_id) = (selected.claim_id, selected.packet_id)
        JOIN claims claim ON (claim.id, claim.contract_sha256) = (statement.claim_id, statement.contract_sha256)
        JOIN LATERAL local_lean_lemma_status(statement.id) current ON current.id = stored.id
        WHERE requested.id = $1 AND requested.body ->> 'kind' = 'route_condition'
          AND requested.body -> 'formal' = jsonb_build_object('lean', jsonb_build_object('proposition', stored.proposition))
          AND current.status = 'verified'
    )
$$;

CREATE FUNCTION rm.latest_route_condition_local_derivation(request_id uuid)
RETURNS TABLE (id uuid, lemma_id uuid, recorded_at timestamptz, live boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT derivation.id, derivation.lemma_id, derivation.recorded_at,
           local_route_condition_current($1, derivation.lemma_id) AS live
    FROM route_condition_local_derivations derivation WHERE derivation.request_id = $1
    ORDER BY live DESC, derivation.recorded_at DESC, derivation.id DESC LIMIT 1
$$;

CREATE FUNCTION rm.record_route_condition_local_lemma(request_id uuid, lemma_id uuid)
RETURNS rm.route_condition_local_derivations
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target_goal uuid;
DECLARE latest record;
DECLARE saved route_condition_local_derivations;
BEGIN
    SELECT request.goal_id INTO STRICT target_goal FROM research_condition_requests request WHERE request.id = $1;
    -- The ordinary decision writer uses this same lock for the goal.
    PERFORM FROM research_goals goal WHERE goal.id = target_goal FOR UPDATE;
    PERFORM FROM research_goal_selections selected
    JOIN formalization_packets packet ON packet.id = selected.packet_id
    JOIN formalization_reviews review ON review.id = packet.review_id
    WHERE selected.goal_id = target_goal FOR SHARE OF review;
    PERFORM lock_live_foundation();
    IF NOT local_route_condition_current($1, $2) THEN
        RAISE EXCEPTION 'discharging a route condition requires a current local lemma with the exact proposition and the same frozen scope'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT FROM research_condition_decisions decision WHERE decision.request_id = $1) THEN
        RAISE EXCEPTION 'the condition already has a separate decision' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO latest FROM latest_route_condition_local_derivation($1);
    IF FOUND AND latest.live THEN
        IF latest.lemma_id <> $2 THEN
            RAISE EXCEPTION 'the condition already has another current local lemma'
                USING ERRCODE = '23514';
        END IF;
        SELECT * INTO STRICT saved FROM route_condition_local_derivations r WHERE r.id = latest.id;
        RETURN saved;
    END IF;
    INSERT INTO route_condition_local_derivations (request_id, lemma_id)
        VALUES ($1, $2) RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE FUNCTION rm.guard_local_route_condition_decision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT FROM latest_route_condition_local_derivation(NEW.request_id) latest WHERE latest.live) THEN
        RAISE EXCEPTION 'the condition already has a current local lemma'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_local_derivation BEFORE INSERT ON rm.research_condition_decisions
    FOR EACH ROW WHEN (NEW.action = 'derived') EXECUTE FUNCTION rm.guard_local_route_condition_decision();

-- These route-only dispositions are intentionally separate from interpretation
-- readiness. Local lemma validity reads the packet's existing proof readiness.
CREATE OR REPLACE VIEW rm.research_condition_status WITH (security_barrier = true) AS
    SELECT q.*,
           coalesce(decision.action, CASE WHEN latest.live THEN 'derived' END) AS action,
           coalesce(decision.actor_ref, CASE WHEN latest.id IS NOT NULL THEN 'exact verifier' END) AS actor_ref,
           coalesce(decision.reason, CASE WHEN latest.live
                    THEN 'The exact proposition was independently checked under the same frozen scope.'
                    WHEN latest.id IS NOT NULL THEN 'The previously checked local lemma needs rechecking.' END) AS reason,
           decision.version_id, decision.evidence_id, decision.revised_goal_id,
           coalesce(decision.decided_at, latest.recorded_at) AS decided_at,
           CASE WHEN decision.evidence_id IS NOT NULL THEN
                  recheck.request_id IS NULL AND rm.condition_derivation_current(q.id, decision.evidence_id)
                WHEN decision.request_id IS NULL AND latest.id IS NOT NULL THEN latest.live
           END AS derivation_current,
           recheck.replacement_request_id, recheck.reason AS derivation_recheck_reason,
           latest.id AS local_derivation_id, latest.lemma_id AS local_lemma_id,
           lemma.verification_attempt_id AS local_verification_attempt_id, latest.live AS local_lemma_current
    FROM rm.research_condition_requests q
    LEFT JOIN rm.research_condition_decisions decision ON decision.request_id = q.id
    LEFT JOIN rm.research_condition_rechecks recheck ON recheck.request_id = q.id
    LEFT JOIN LATERAL rm.latest_route_condition_local_derivation(q.id) latest ON true
    LEFT JOIN rm.local_lean_lemmas lemma ON lemma.id = latest.lemma_id;

REVOKE ALL ON FUNCTION rm.local_route_condition_current(uuid,uuid), rm.record_route_condition_local_lemma(uuid,uuid),
    rm.latest_route_condition_local_derivation(uuid),
    rm.guard_local_route_condition_decision() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_route_condition_local_lemma(uuid,uuid) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.latest_route_condition_local_derivation(uuid) TO rm_human;
GRANT SELECT ON rm.route_condition_local_derivations TO rm_human;
