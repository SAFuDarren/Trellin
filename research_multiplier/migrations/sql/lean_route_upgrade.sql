ALTER TABLE rm.lean_route_events
    DROP CONSTRAINT lean_route_events_action_check,
    ALTER COLUMN campaign_sha256 DROP NOT NULL,
    ALTER COLUMN review_sha256 DROP NOT NULL,
    ADD CONSTRAINT lean_route_events_action_check CHECK (
        (action IN ('activate', 'rollback') AND campaign_sha256 IS NOT NULL AND review_sha256 IS NOT NULL)
        OR (action = 'upgrade_reset' AND previous_event_id IS NOT NULL
            AND campaign_sha256 IS NULL AND review_sha256 IS NULL)
    );

CREATE FUNCTION rm.reset_lean_route_after_upgrade(expected_event uuid, installed_revision rm.sha256_hex,
    actor text, explanation text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE active record;
DECLARE event_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.lean_route_evolution', 17));
    SELECT id, effective INTO active FROM lean_current_route;
    IF active.id IS NULL OR active.id IS DISTINCT FROM $1 THEN
        RAISE EXCEPTION 'an upgrade reset requires the current Lean route event' USING ERRCODE = '23514';
    END IF;
    IF $2 IS NULL OR active.effective ->> 'search_revision' = $2 THEN
        RAISE EXCEPTION 'an upgrade reset requires a different installed Lean implementation' USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_route_events(action, previous_event_id, baseline, effective, measurements, actor_ref, reason)
    VALUES ('upgrade_reset', active.id, active.effective,
            jsonb_build_object('strategy', 'ranked', 'profile', NULL, 'configuration_sha256', NULL,
                               'effective_sha256', NULL, 'search_revision', $2), '{}'::jsonb, $3, $4)
    RETURNING id INTO event_id;
    RETURN event_id;
END $$;

REVOKE ALL ON FUNCTION rm.reset_lean_route_after_upgrade(uuid,rm.sha256_hex,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.reset_lean_route_after_upgrade(uuid,rm.sha256_hex,text,text) TO rm_human;

CREATE OR REPLACE FUNCTION rm.activate_lean_route(expected_event uuid, campaign rm.sha256_hex, reviewed rm.sha256_hex,
    original jsonb, replacement jsonb, counts jsonb, actor text, explanation text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE active record;
DECLARE event_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.lean_route_evolution', 17));
    SELECT id, action, effective INTO active FROM lean_current_route;
    IF active.id IS DISTINCT FROM $1 THEN
        RAISE EXCEPTION 'the active Lean route changed since comparison' USING ERRCODE = '23514';
    END IF;
    IF NOT coalesce(valid_lean_route_binding($4) AND valid_lean_route_binding($5), false)
       OR $4 = $5 OR $4 ->> 'search_revision' IS DISTINCT FROM $5 ->> 'search_revision'
       OR jsonb_typeof($6) IS DISTINCT FROM 'object' OR octet_length($6::text) > 12000 THEN
        RAISE EXCEPTION 'activation requires an exact compared Lean route and bounded review summary' USING ERRCODE = '23514';
    END IF;
    IF active.id IS NULL OR active.action = 'upgrade_reset' THEN
        IF $4 ->> 'strategy' <> 'ranked' OR $4 ->> 'profile' IS NOT NULL
           OR (active.action = 'upgrade_reset'
               AND $4 ->> 'search_revision' IS DISTINCT FROM active.effective ->> 'search_revision') THEN
            RAISE EXCEPTION 'compare against the current installed default Lean route' USING ERRCODE = '23514';
        END IF;
    ELSIF active.effective IS DISTINCT FROM $4 THEN
        RAISE EXCEPTION 'the active Lean route changed since comparison' USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_route_events(action, previous_event_id, campaign_sha256, review_sha256, baseline,
                                  effective, measurements, actor_ref, reason)
    VALUES ('activate', active.id, $2, $3, $4, $5, $6, $7, $8)
    RETURNING id INTO event_id;
    RETURN event_id;
END $$;
