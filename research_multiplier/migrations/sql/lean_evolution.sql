-- Researcher decisions for the public Lean worker's evaluated strategy and author.
CREATE TABLE rm.lean_route_events (
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action text NOT NULL CHECK (action IN ('activate', 'rollback')),
    previous_event_id uuid REFERENCES rm.lean_route_events(id),
    campaign_sha256 rm.sha256_hex NOT NULL,
    review_sha256 rm.sha256_hex NOT NULL,
    baseline jsonb NOT NULL,
    effective jsonb NOT NULL,
    measurements jsonb NOT NULL,
    actor_ref text NOT NULL CHECK (length(btrim(actor_ref)) BETWEEN 1 AND 200),
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 4000),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON rm.lean_route_events FROM PUBLIC;
GRANT SELECT ON rm.lean_route_events TO rm_human;

CREATE VIEW rm.lean_current_route WITH (security_barrier = true) AS
    SELECT id, action, effective, campaign_sha256, recorded_at
    FROM rm.lean_route_events ORDER BY sequence_no DESC LIMIT 1;
REVOKE ALL ON rm.lean_current_route FROM PUBLIC;
GRANT SELECT ON rm.lean_current_route TO rm_scheduler, rm_human;

CREATE FUNCTION rm.valid_lean_route_binding(binding jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT coalesce(
        jsonb_typeof($1) = 'object'
        AND $1 ?& ARRAY['strategy','profile','configuration_sha256','effective_sha256','search_revision']
        AND $1 - ARRAY['strategy','profile','configuration_sha256','effective_sha256','search_revision'] = '{}'::jsonb
        AND $1 ->> 'strategy' IN ('legacy', 'ranked')
        AND ($1 -> 'profile' = 'null'::jsonb OR $1 ->> 'profile' ~ '^[A-Z][A-Z0-9_]{0,31}$')
        AND $1 ->> 'configuration_sha256' ~ '^[0-9a-f]{64}$'
        AND $1 ->> 'effective_sha256' ~ '^[0-9a-f]{64}$'
        AND $1 ->> 'search_revision' ~ '^[0-9a-f]{64}$'
    , false)
$$;

CREATE FUNCTION rm.activate_lean_route(expected_event uuid, campaign rm.sha256_hex, reviewed rm.sha256_hex,
    original jsonb, replacement jsonb, counts jsonb, actor text, explanation text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE active record;
DECLARE event_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.lean_route_evolution', 17));
    SELECT id, effective INTO active FROM lean_current_route;
    IF active.id IS DISTINCT FROM $1 OR (active.id IS NOT NULL AND active.effective IS DISTINCT FROM $4) THEN
        RAISE EXCEPTION 'the active Lean route changed since comparison' USING ERRCODE = '23514';
    END IF;
    IF NOT coalesce(valid_lean_route_binding($4) AND valid_lean_route_binding($5), false)
       OR $4 = $5 OR $4 ->> 'search_revision' IS DISTINCT FROM $5 ->> 'search_revision'
       OR jsonb_typeof($6) IS DISTINCT FROM 'object' OR octet_length($6::text) > 12000 THEN
        RAISE EXCEPTION 'activation requires an exact compared Lean route and bounded review summary' USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_route_events(action, previous_event_id, campaign_sha256, review_sha256, baseline,
                                  effective, measurements, actor_ref, reason)
    VALUES ('activate', active.id, $2, $3, $4, $5, $6, $7, $8)
    RETURNING id INTO event_id;
    RETURN event_id;
END $$;

CREATE FUNCTION rm.rollback_lean_route(activation_id uuid, actor text, explanation text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE activation lean_route_events;
DECLARE event_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.lean_route_evolution', 17));
    SELECT event.* INTO activation FROM lean_route_events event JOIN lean_current_route current_route USING (id)
    WHERE event.id = $1 AND event.action = 'activate';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'rollback requires the current Lean activation event' USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_route_events(action, previous_event_id, campaign_sha256, review_sha256, baseline,
                                  effective, measurements, actor_ref, reason)
    VALUES ('rollback', activation.id, activation.campaign_sha256, activation.review_sha256, activation.effective,
            activation.baseline, activation.measurements, $2, $3)
    RETURNING id INTO event_id;
    RETURN event_id;
END $$;

REVOKE ALL ON FUNCTION rm.valid_lean_route_binding(jsonb),
    rm.activate_lean_route(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb,text,text),
    rm.rollback_lean_route(uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.activate_lean_route(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb,text,text),
    rm.rollback_lean_route(uuid,text,text) TO rm_human;

CREATE FUNCTION rm.lean_route_observations(event_id uuid)
RETURNS TABLE (status text, passes bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT run.result ->> 'status', count(*)
    FROM lean_progress_runs run JOIN lean_route_events route ON route.id::text = run.result ->> 'lean_route_event_id'
    WHERE route.id = $1 AND run.recorded_at >= statement_timestamp() - interval '7 days'
    GROUP BY run.result ->> 'status'
    ORDER BY run.result ->> 'status'
$$;
REVOKE ALL ON FUNCTION rm.lean_route_observations(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_route_observations(uuid) TO rm_human;
