ALTER TABLE rm.verifier_reports
    ADD COLUMN checker_revision rm.sha256_hex,
    ADD COLUMN summary text,
    ADD COLUMN authenticated_role name,
    ADD COLUMN record_sha256 rm.sha256_hex UNIQUE;

CREATE TABLE rm.verifier_report_decisions (
    report_id uuid PRIMARY KEY REFERENCES rm.verifier_reports(id),
    action text NOT NULL CHECK (action IN ('contain', 'dismiss')),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX verifier_reports_checker_revision ON rm.verifier_reports(checker_revision);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.verifier_reports
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.verifier_report_decisions
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.report_verifier_incident(kind text, summary text, artifact jsonb,
    job_id uuid DEFAULT NULL, checker_revision text DEFAULT NULL) RETURNS rm.verifier_reports
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE task jobs;
DECLARE result verifier_reports;
DECLARE revision text := checker_revision;
DECLARE expected text;
DECLARE document_sha text;
DECLARE human_role boolean := pg_has_role(session_user, 'rm_human', 'USAGE');
DECLARE verifier_role boolean := pg_has_role(session_user, 'rm_verifier', 'USAGE');
DECLARE scheduler_role boolean := pg_has_role(session_user, 'rm_scheduler', 'USAGE');
BEGIN
    IF coalesce(kind, '') NOT IN ('verifier_vulnerability', 'workflow_blocked')
        OR nullif(btrim(summary), '') IS NULL
        OR NOT lean_artifact_ref_valid(artifact) THEN
        RAISE EXCEPTION 'an incident needs a concrete summary and an archived report' USING ERRCODE = '22023';
    END IF;
    IF job_id IS NOT NULL THEN
        SELECT * INTO STRICT task FROM jobs j WHERE j.id = job_id;
        expected := task.payload ->> 'checker_revision';
    END IF;
    IF NOT (human_role OR verifier_role) AND (
        kind <> 'workflow_blocked' OR task.id IS NULL OR (
            NOT scheduler_role AND NOT EXISTS (
                SELECT 1 FROM attempts a WHERE a.job_id = task.id AND a.submitted_by = session_user
            )
        )
    ) THEN
        RAISE EXCEPTION 'this caller can report only a workflow block for an authorized job' USING ERRCODE = '42501';
    END IF;
    IF expected IS NOT NULL AND revision IS NOT NULL AND revision <> expected THEN
        RAISE EXCEPTION 'the supplied checker revision differs from the exact verifier job revision'
            USING ERRCODE = '23514';
    END IF;
    revision := coalesce(revision, expected);
    IF kind = 'verifier_vulnerability' AND revision IS NULL THEN
        RAISE EXCEPTION 'a verifier vulnerability must identify the exact checker revision' USING ERRCODE = '23514';
    END IF;
    IF revision IS NOT NULL AND (revision !~ '^[0-9a-f]{64}$' OR (
        revision IS DISTINCT FROM expected
        AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.code_revision = revision)
        AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = 'verify' AND j.payload ->> 'checker_revision' = revision)
    )) THEN
        RAISE EXCEPTION 'the checker revision does not match a recorded verification'
            USING ERRCODE = '23514';
    END IF;
    document_sha := encode(sha256(convert_to(jsonb_build_object('kind',kind,'job_id',job_id,
        'revision',revision,'summary',summary,'artifact',artifact,'actor',session_user)::text,'UTF8')), 'hex');
    INSERT INTO verifier_reports (job_id, kind, report_uri, report_sha256, submitted_by,
        checker_revision, summary, authenticated_role, record_sha256)
    VALUES (job_id, kind, artifact ->> 'uri', artifact ->> 'sha256', session_user,
        revision, summary, session_user, document_sha)
    ON CONFLICT (record_sha256) DO NOTHING RETURNING * INTO result;
    IF NOT FOUND THEN
        SELECT * INTO STRICT result FROM verifier_reports r WHERE r.record_sha256 = document_sha;
    END IF;
    RETURN result;
END $$;

CREATE FUNCTION rm.decide_verifier_incident(report_id uuid, action text, actor_ref text, reason text)
RETURNS rm.verifier_report_decisions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE report verifier_reports;
DECLARE decision verifier_report_decisions;
BEGIN
    IF coalesce(action, '') NOT IN ('contain', 'dismiss') OR nullif(btrim(actor_ref), '') IS NULL
        OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'review requires a disposition, a researcher and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT report FROM verifier_reports r WHERE r.id = report_id FOR UPDATE;
    SELECT * INTO decision FROM verifier_report_decisions d WHERE d.report_id = report.id;
    IF FOUND THEN
        IF ROW(decision.action,decision.actor_ref,decision.reason,decision.authenticated_role)
            IS DISTINCT FROM ROW(action,actor_ref,reason,session_user) THEN
            RAISE EXCEPTION 'this incident already has a different decision' USING ERRCODE = '23514';
        END IF;
        RETURN decision;
    END IF;
    IF action = 'contain' THEN
        IF report.kind <> 'verifier_vulnerability' OR report.checker_revision IS NULL THEN
            RAISE EXCEPTION 'containment requires a concrete verifier vulnerability with an exact checker revision'
                USING ERRCODE = '23514';
        END IF;
        PERFORM retire_verification_input('verifier', report.checker_revision,
            format('Incident %s: %s', report.id, reason));
    END IF;
    INSERT INTO verifier_report_decisions (report_id, action, actor_ref, reason)
    VALUES (report.id, action, actor_ref, reason) RETURNING * INTO decision;
    RETURN decision;
END $$;

CREATE VIEW rm.verifier_incident_status WITH (security_barrier = true) AS
    SELECT r.*, coalesce(d.action, 'open') AS status, d.actor_ref AS decided_by, d.reason AS decision_reason,
           d.decided_at,
           EXISTS (SELECT 1 FROM rm.verification_input_retirements retired
                WHERE retired.input_kind = 'verifier' AND retired.sha256 = r.checker_revision) AS checker_retired
    FROM rm.verifier_reports r
    LEFT JOIN rm.verifier_report_decisions d ON d.report_id = r.id;

CREATE FUNCTION rm.scan_verifier_window(start_at timestamptz, end_at timestamptz)
RETURNS TABLE (
    checker_revision text, attempts bigint, successful_checks bigint, failed_checks bigint,
    prior_attempts bigint, prior_successful_checks bigint, prior_failed_checks bigint,
    verified_receipts bigint, returned_receipts bigint, stale_receipts bigint,
    verified_different_scope bigint, open_reports bigint, retired boolean
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF start_at IS NULL OR end_at IS NULL OR NOT isfinite(start_at) OR NOT isfinite(end_at)
        OR start_at >= end_at THEN
        RAISE EXCEPTION 'a scan requires a finite, increasing time window' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    WITH relevant_attempts AS (
        SELECT j.payload ->> 'checker_revision' AS revision, a.finding,
               a.submitted_at >= start_at AS current_window
        FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.role = 'verify' AND j.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$'
          AND a.submitted_at >= start_at - (end_at - start_at) AND a.submitted_at < end_at
    ), attempt_counts AS (
        SELECT a.revision,
            count(*) FILTER (WHERE a.current_window) AS now_attempts,
            count(*) FILTER (WHERE a.current_window AND a.finding IN ('exact_proof','counterexample')) AS now_success,
            count(*) FILTER (WHERE a.current_window AND a.finding = 'no_result') AS now_failed,
            count(*) FILTER (WHERE NOT a.current_window) AS past_attempts,
            count(*) FILTER (WHERE NOT a.current_window AND a.finding IN ('exact_proof','counterexample')) AS past_success,
            count(*) FILTER (WHERE NOT a.current_window AND a.finding = 'no_result') AS past_failed
        FROM relevant_attempts a GROUP BY a.revision
    ), receipt_counts AS (
        SELECT e.code_revision::text AS revision,
            count(*) FILTER (WHERE e.verdict = 'verified') AS verified,
            count(*) FILTER (WHERE e.review_status = 'returned') AS returned,
            count(*) FILTER (WHERE e.needs_recheck) AS stale,
            count(*) FILTER (WHERE e.verdict = 'verified' AND e.diff_status <> 'exact') AS different_scope
        FROM evidence_foundation_status e WHERE e.issued_at >= start_at AND e.issued_at < end_at
        GROUP BY e.code_revision
    ), report_counts AS (
        SELECT i.checker_revision::text AS revision, count(*) FILTER (WHERE i.status = 'open') AS open_count
        FROM verifier_incident_status i WHERE i.created_at >= start_at AND i.created_at < end_at
            AND i.checker_revision IS NOT NULL GROUP BY i.checker_revision
    ), keys AS (
        SELECT a.revision FROM attempt_counts a UNION SELECT e.revision FROM receipt_counts e
        UNION SELECT i.revision FROM report_counts i
    )
    SELECT k.revision, coalesce(a.now_attempts,0), coalesce(a.now_success,0), coalesce(a.now_failed,0),
           coalesce(a.past_attempts,0), coalesce(a.past_success,0), coalesce(a.past_failed,0),
           coalesce(e.verified,0), coalesce(e.returned,0), coalesce(e.stale,0), coalesce(e.different_scope,0),
           coalesce(i.open_count,0), EXISTS (SELECT 1 FROM verification_input_retirements retired_input
                WHERE retired_input.input_kind = 'verifier' AND retired_input.sha256 = k.revision)
    FROM keys k LEFT JOIN attempt_counts a USING (revision) LEFT JOIN receipt_counts e USING (revision)
    LEFT JOIN report_counts i USING (revision) ORDER BY k.revision;
END $$;

REVOKE ALL ON FUNCTION rm.report_verifier_incident(text, text, jsonb, uuid, text),
    rm.decide_verifier_incident(uuid, text, text, text), rm.scan_verifier_window(timestamptz, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.report_verifier_incident(text, text, jsonb, uuid, text)
    TO rm_human, rm_verifier, rm_scheduler, rm_agent;
GRANT EXECUTE ON FUNCTION rm.decide_verifier_incident(uuid, text, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.scan_verifier_window(timestamptz, timestamptz) TO rm_human, rm_verifier;
GRANT SELECT ON rm.verifier_incident_status TO rm_human, rm_verifier;
