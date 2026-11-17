CREATE TABLE rm.shadow_experiments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_sha256 rm.sha256_hex NOT NULL UNIQUE,
    report_id uuid NOT NULL REFERENCES rm.evaluation_reports(id),
    route text NOT NULL CHECK (route = 'PROVER'),
    baseline_event_id uuid,
    baseline_sha256 rm.sha256_hex NOT NULL,
    candidate_profile text NOT NULL CHECK (candidate_profile ~ '^[A-Z][A-Z0-9_]{0,31}$'),
    candidate_sha256 rm.sha256_hex NOT NULL,
    auditor_sha256 rm.sha256_hex NOT NULL,
    case_ids jsonb NOT NULL CHECK (jsonb_typeof(case_ids) = 'object'),
    baseline_audit_scope_drifts integer NOT NULL CHECK (baseline_audit_scope_drifts >= 0),
    candidate_audit_scope_drifts integer NOT NULL CHECK (candidate_audit_scope_drifts >= 0),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (candidate_sha256 <> baseline_sha256)
);

CREATE TABLE rm.shadow_live_trials (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trial_sha256 rm.sha256_hex NOT NULL UNIQUE,
    baseline_event_id uuid,
    baseline_sha256 rm.sha256_hex NOT NULL,
    candidate_sha256 rm.sha256_hex NOT NULL,
    auditor_sha256 rm.sha256_hex NOT NULL,
    counts jsonb NOT NULL CHECK (jsonb_typeof(counts) = 'object'),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (baseline_sha256 <> candidate_sha256)
);

CREATE TABLE rm.shadow_route_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    route text NOT NULL CHECK (route = 'PROVER'),
    action text NOT NULL CHECK (action IN ('activate', 'rollback')),
    experiment_id uuid NOT NULL REFERENCES rm.shadow_experiments(id),
    live_trial_id uuid NOT NULL REFERENCES rm.shadow_live_trials(id),
    previous_event_id uuid REFERENCES rm.shadow_route_events(id),
    profile text CHECK (profile ~ '^[A-Z][A-Z0-9_]{0,31}$'),
    configuration_sha256 rm.sha256_hex NOT NULL,
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    decided_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE rm.shadow_experiments ADD FOREIGN KEY (baseline_event_id) REFERENCES rm.shadow_route_events(id);
ALTER TABLE rm.shadow_live_trials ADD FOREIGN KEY (baseline_event_id) REFERENCES rm.shadow_route_events(id);

CREATE TABLE rm.shadow_review_times (
    experiment_id uuid NOT NULL REFERENCES rm.shadow_experiments(id),
    case_id text NOT NULL,
    side text NOT NULL CHECK (side IN ('baseline', 'candidate')),
    minutes numeric NOT NULL CHECK (minutes > 0 AND minutes <= 1440),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (experiment_id, case_id, side)
);

CREATE TRIGGER shadow_experiments_immutable BEFORE UPDATE OR DELETE ON rm.shadow_experiments
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER shadow_route_events_immutable BEFORE UPDATE OR DELETE ON rm.shadow_route_events
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER shadow_live_trials_immutable BEFORE UPDATE OR DELETE ON rm.shadow_live_trials
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER shadow_review_times_immutable BEFORE UPDATE OR DELETE ON rm.shadow_review_times
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE VIEW rm.shadow_current_routes WITH (security_barrier = true) AS
    SELECT DISTINCT ON (route) id, route, profile, configuration_sha256
    FROM rm.shadow_route_events ORDER BY route, sequence_no DESC;

CREATE VIEW rm.shadow_review_summary WITH (security_barrier = true) AS
    SELECT e.id AS experiment_id, count(*) FILTER (WHERE a.minutes IS NOT NULL OR b.minutes IS NOT NULL) AS measured_cases,
           count(*) FILTER (WHERE a.minutes IS NOT NULL AND b.minutes IS NOT NULL) AS paired_cases,
           sum(a.minutes) FILTER (WHERE a.minutes IS NOT NULL AND b.minutes IS NOT NULL) AS baseline_minutes,
           sum(b.minutes) FILTER (WHERE a.minutes IS NOT NULL AND b.minutes IS NOT NULL) AS candidate_minutes
    FROM rm.shadow_experiments e CROSS JOIN LATERAL jsonb_object_keys(e.case_ids) c(case_id)
    LEFT JOIN rm.shadow_review_times a ON a.experiment_id = e.id AND a.case_id = c.case_id AND a.side = 'baseline'
    LEFT JOIN rm.shadow_review_times b ON b.experiment_id = e.id AND b.case_id = c.case_id AND b.side = 'candidate'
    GROUP BY e.id;

CREATE FUNCTION rm.register_shadow_experiment(experiment_sha text, report_id uuid, baseline_event_id uuid,
    baseline_sha text, candidate_profile text, candidate_sha text, auditor_sha text, case_ids jsonb,
    baseline_audit_scope_drifts integer, candidate_audit_scope_drifts integer)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE report evaluation_reports;
DECLARE current_route shadow_current_routes;
DECLARE saved shadow_experiments;
BEGIN
    SELECT * INTO STRICT report FROM evaluation_reports r WHERE r.id = $2;
    SELECT * INTO current_route FROM shadow_current_routes r WHERE r.route = 'PROVER';
    IF NOT coalesce($1 ~ '^[0-9a-f]{64}$' AND $4 ~ '^[0-9a-f]{64}$' AND $6 ~ '^[0-9a-f]{64}$'
                AND $7 ~ '^[0-9a-f]{64}$', false)
        OR NOT coalesce($5 ~ '^[A-Z][A-Z0-9_]{0,31}$', false)
        OR $4 = $6 OR $3 IS DISTINCT FROM current_route.id
        OR (current_route.id IS NOT NULL AND current_route.configuration_sha256::text IS DISTINCT FROM $4)
        OR report.summary ->> 'split' IS DISTINCT FROM 'holdout'
        OR report.summary #>> '{baseline,version,harness}' IS DISTINCT FROM 'production-prover-shadow'
        OR report.summary #>> '{candidate,version,harness}' IS DISTINCT FROM 'production-prover-shadow'
        OR report.summary #>> '{baseline,version,revision}' IS DISTINCT FROM $4
        OR report.summary #>> '{candidate,version,revision}' IS DISTINCT FROM $6
        OR report.summary #> '{baseline,version}' = report.summary #> '{candidate,version}'
        OR jsonb_typeof($8) IS DISTINCT FROM 'object'
        OR NOT coalesce($9 BETWEEN 0 AND (report.summary #>> '{baseline,counts,cases}')::integer
           AND $10 BETWEEN 0 AND (report.summary #>> '{candidate,counts,cases}')::integer, false)
        OR (SELECT count(*) FROM jsonb_each_text($8)) <> (report.summary #>> '{baseline,counts,cases}')::integer
        OR EXISTS (SELECT FROM jsonb_each_text($8) item WHERE length(item.key) NOT BETWEEN 1 AND 180
            OR length(btrim(item.key)) = 0 OR NOT coalesce(item.value ~ '^[0-9a-f]{64}$', false))
    THEN
        RAISE EXCEPTION 'shadow experiment requires matching fingerprints, frozen cases and current route'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO shadow_experiments (experiment_sha256, report_id, route, baseline_event_id,
        baseline_sha256, candidate_profile, candidate_sha256, auditor_sha256, case_ids,
        baseline_audit_scope_drifts, candidate_audit_scope_drifts)
    VALUES ($1,$2,'PROVER',$3,$4,$5,$6,$7,$8,$9,$10)
    ON CONFLICT (experiment_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM shadow_experiments a WHERE a.experiment_sha256 = $1;
        IF ROW(saved.report_id, saved.baseline_event_id, saved.baseline_sha256::text,
            saved.candidate_profile, saved.candidate_sha256::text, saved.auditor_sha256::text, saved.case_ids,
            saved.baseline_audit_scope_drifts, saved.candidate_audit_scope_drifts)
            IS DISTINCT FROM ROW($2,$3,$4,$5,$6,$7,$8,$9,$10) THEN
            RAISE EXCEPTION 'the same experiment digest is already bound to different inputs' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN saved.id;
END $$;

CREATE FUNCTION rm.register_shadow_live_trial(trial_sha text, baseline_event_id uuid,
    baseline_sha text, candidate_sha text, auditor_sha text, counts jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE current_route shadow_current_routes;
DECLARE saved shadow_live_trials;
DECLARE key text;
DECLARE cases integer;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.shadow_route:PROVER', 0));
    SELECT * INTO current_route FROM shadow_current_routes r WHERE r.route = 'PROVER';
    IF NOT coalesce($1 ~ '^[0-9a-f]{64}$' AND $3 ~ '^[0-9a-f]{64}$'
                AND $4 ~ '^[0-9a-f]{64}$' AND $5 ~ '^[0-9a-f]{64}$', false)
        OR $2 IS DISTINCT FROM current_route.id
        OR (current_route.id IS NOT NULL AND $3 IS DISTINCT FROM current_route.configuration_sha256::text)
        OR $3 = $4 OR jsonb_typeof($6) IS DISTINCT FROM 'object'
        OR NOT coalesce($6 ->> 'cases' ~ '^[0-9]{1,2}$', false) THEN
        RAISE EXCEPTION 'live shadow requires exact configurations, current baseline and a bounded case count'
            USING ERRCODE = '23514';
    END IF;
    cases := ($6 ->> 'cases')::integer;
    IF cases NOT BETWEEN 1 AND 32 OR (SELECT count(*) FROM jsonb_object_keys($6)) <> 7 THEN
        RAISE EXCEPTION 'live shadow requires all measured counts' USING ERRCODE = '23514';
    END IF;
    FOREACH key IN ARRAY ARRAY['reviewed_cases','baseline_conclusive','candidate_conclusive',
        'candidate_reviewed_conflicts','baseline_audit_scope_drifts','candidate_audit_scope_drifts'] LOOP
        IF NOT coalesce(jsonb_typeof($6 -> key) = 'number' AND $6 ->> key ~ '^[0-9]{1,2}$'
                  AND ($6 ->> key)::integer <= cases, false) THEN
            RAISE EXCEPTION 'each live shadow measurement must fit the exact case count' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    INSERT INTO shadow_live_trials (trial_sha256, baseline_event_id, baseline_sha256, candidate_sha256,
        auditor_sha256, counts) VALUES ($1,$2,$3,$4,$5,$6)
    ON CONFLICT (trial_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM shadow_live_trials t WHERE t.trial_sha256 = $1;
        IF ROW(saved.baseline_event_id, saved.baseline_sha256::text, saved.candidate_sha256::text,
               saved.auditor_sha256::text, saved.counts) IS DISTINCT FROM ROW($2,$3,$4,$5,$6) THEN
            RAISE EXCEPTION 'the same live trial digest is already bound to different inputs' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN saved.id;
END $$;

CREATE FUNCTION rm.record_shadow_review(experiment_id uuid, case_id text, side text, minutes numeric, actor_ref text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.shadow_route:PROVER', 0));
    IF NOT EXISTS (SELECT FROM shadow_experiments e WHERE e.id = $1 AND e.case_ids ? $2)
        OR EXISTS (SELECT FROM shadow_route_events r WHERE r.experiment_id = $1) THEN
        RAISE EXCEPTION 'review time must identify a frozen case before any route decision' USING ERRCODE = '23514';
    END IF;
    INSERT INTO shadow_review_times (experiment_id, case_id, side, minutes, actor_ref)
    VALUES ($1,$2,$3,$4,$5);
END $$;

CREATE FUNCTION rm.activate_shadow_route(experiment_id uuid, expected_sha text,
    live_trial_id uuid, expected_trial_sha text, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE study shadow_experiments;
DECLARE current_route shadow_current_routes;
DECLARE times shadow_review_summary;
DECLARE trial shadow_live_trials;
DECLARE event uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.shadow_route:PROVER', 0));
    SELECT * INTO STRICT study FROM shadow_experiments e WHERE e.id = $1;
    SELECT * INTO current_route FROM shadow_current_routes r WHERE r.route = study.route;
    SELECT * INTO STRICT times FROM shadow_review_summary t WHERE t.experiment_id = $1;
    SELECT * INTO STRICT trial FROM shadow_live_trials t WHERE t.id = $3;
    IF study.experiment_sha256::text IS DISTINCT FROM $2 OR study.baseline_event_id IS DISTINCT FROM current_route.id
        OR trial.trial_sha256::text IS DISTINCT FROM $4
        OR trial.baseline_event_id IS DISTINCT FROM study.baseline_event_id
        OR trial.baseline_sha256 <> study.baseline_sha256 OR trial.candidate_sha256 <> study.candidate_sha256
        OR trial.auditor_sha256 <> study.auditor_sha256
        OR (trial.counts ->> 'candidate_conclusive')::integer < greatest(1, (trial.counts ->> 'baseline_conclusive')::integer)
        OR (trial.counts ->> 'candidate_reviewed_conflicts')::integer <> 0
        OR (trial.counts ->> 'candidate_audit_scope_drifts')::integer > (trial.counts ->> 'baseline_audit_scope_drifts')::integer
        OR NOT EXISTS (SELECT FROM evaluation_approvals a WHERE a.report_id = study.report_id)
        OR study.candidate_audit_scope_drifts > study.baseline_audit_scope_drifts
        OR times.measured_cases <> times.paired_cases
        OR coalesce(times.candidate_minutes > times.baseline_minutes, false)
    THEN
        RAISE EXCEPTION 'activation requires the approved experiment, exact eligible live trial, current baseline and paired review time without regression'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO shadow_route_events (route, action, experiment_id, live_trial_id, previous_event_id,
        profile, configuration_sha256, actor_ref, reason)
    VALUES (study.route,'activate',study.id,trial.id,current_route.id,study.candidate_profile,study.candidate_sha256,$5,$6)
    RETURNING id INTO event;
    RETURN event;
END $$;

CREATE FUNCTION rm.rollback_shadow_route(expected_event uuid, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE current_route shadow_route_events;
DECLARE prior_route shadow_route_events;
DECLARE study shadow_experiments;
DECLARE event uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.shadow_route:PROVER', 0));
    SELECT * INTO STRICT current_route FROM shadow_route_events r
        WHERE r.id = (SELECT s.id FROM shadow_current_routes s WHERE s.route = 'PROVER');
    IF current_route.id IS DISTINCT FROM $1 OR current_route.action <> 'activate' THEN
        RAISE EXCEPTION 'rollback requires the current activation event' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO STRICT study FROM shadow_experiments e WHERE e.id = current_route.experiment_id;
    SELECT * INTO prior_route FROM shadow_route_events r WHERE r.id = current_route.previous_event_id;
    INSERT INTO shadow_route_events (route, action, experiment_id, live_trial_id, previous_event_id,
        profile, configuration_sha256, actor_ref, reason)
    VALUES (current_route.route,'rollback',study.id,current_route.live_trial_id,current_route.id,
        prior_route.profile,study.baseline_sha256,$2,$3)
    RETURNING id INTO event;
    RETURN event;
END $$;

REVOKE ALL ON TABLE rm.shadow_experiments, rm.shadow_route_events, rm.shadow_review_times, rm.shadow_live_trials,
    rm.shadow_current_routes, rm.shadow_review_summary FROM PUBLIC;
REVOKE ALL ON FUNCTION rm.register_shadow_experiment(text,uuid,uuid,text,text,text,text,jsonb,integer,integer),
    rm.register_shadow_live_trial(text,uuid,text,text,text,jsonb),
    rm.record_shadow_review(uuid,text,text,numeric,text), rm.activate_shadow_route(uuid,text,uuid,text,text,text),
    rm.rollback_shadow_route(uuid,text,text) FROM PUBLIC;
GRANT SELECT ON rm.shadow_current_routes TO rm_human, rm_verifier, rm_scheduler;
GRANT SELECT ON rm.shadow_experiments, rm.shadow_route_events, rm.shadow_review_summary, rm.shadow_live_trials TO rm_human;
GRANT EXECUTE ON FUNCTION rm.register_shadow_experiment(text,uuid,uuid,text,text,text,text,jsonb,integer,integer) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.register_shadow_live_trial(text,uuid,text,text,text,jsonb) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.record_shadow_review(uuid,text,text,numeric,text), rm.activate_shadow_route(uuid,text,uuid,text,text,text),
    rm.rollback_shadow_route(uuid,text,text) TO rm_human;
