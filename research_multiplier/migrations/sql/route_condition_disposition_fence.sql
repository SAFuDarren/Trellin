-- Locking an unchanged goal row serializes access but cannot invalidate an
-- older REPEATABLE READ snapshot. All writers of a route disposition also
-- update this per-request row, leaving the research records immutable.
CREATE TABLE rm.route_condition_disposition_fences (
    request_id uuid PRIMARY KEY REFERENCES rm.research_condition_requests(id),
    revision bigint NOT NULL DEFAULT 1
);

CREATE FUNCTION rm.fence_route_condition_disposition(request_id uuid) RETURNS void
LANGUAGE sql VOLATILE SET search_path = pg_catalog, rm, pg_temp AS $$
    INSERT INTO route_condition_disposition_fences (request_id) VALUES ($1)
    ON CONFLICT ON CONSTRAINT route_condition_disposition_fences_pkey
    DO UPDATE SET revision = route_condition_disposition_fences.revision + 1
$$;
REVOKE ALL ON FUNCTION rm.fence_route_condition_disposition(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.record_route_condition_local_lemma(request_id uuid, lemma_id uuid)
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
    PERFORM fence_route_condition_disposition($1);
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

CREATE OR REPLACE FUNCTION rm.guard_local_route_condition_decision() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT FROM research_condition_requests request
               WHERE request.id = NEW.request_id AND request.body ->> 'kind' = 'route_condition') THEN
        PERFORM fence_route_condition_disposition(NEW.request_id);
        IF NEW.action = 'derived' AND EXISTS (
            SELECT FROM latest_route_condition_local_derivation(NEW.request_id) latest WHERE latest.live
        ) THEN
            RAISE EXCEPTION 'the condition already has a current local lemma'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER check_local_derivation ON rm.research_condition_decisions;
CREATE TRIGGER check_route_condition_disposition BEFORE INSERT ON rm.research_condition_decisions
    FOR EACH ROW EXECUTE FUNCTION rm.guard_local_route_condition_decision();
