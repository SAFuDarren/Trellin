-- The local final checker wraps a still-required base checker. Keep both
-- inputs for containment, while counting each attempt or receipt exactly once.
CREATE FUNCTION rm.local_final_checker_revision(payload jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT CASE WHEN $1 ? 'lean_statement_id' AND $1 ->> 'target' IN ('goal', 'refutation')
        AND $1 -> 'checker' = '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
        AND $1 ->> 'local_checker_revision' ~ '^[0-9a-f]{64}$'
        AND $1 -> 'local_lemma_ids' <> '[]'::jsonb AND valid_final_lemma_ids($1 -> 'local_lemma_ids')
    THEN $1 ->> 'local_checker_revision' END
$$;

CREATE FUNCTION rm.local_receipt_checker_revision(check_id uuid, attempt_id uuid) RETURNS text
LANGUAGE sql STABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT local_final_checker_revision(job.payload)
    FROM lean_execution_links execution
    JOIN attempts attempt ON attempt.id = execution.verification_attempt_id
    JOIN jobs job ON job.id = attempt.job_id
    WHERE execution.check_id = $1 AND execution.verification_attempt_id = $2
$$;

REVOKE ALL ON FUNCTION rm.local_final_checker_revision(jsonb), rm.local_receipt_checker_revision(uuid,uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.report_verifier_incident(kind text, summary text, artifact jsonb,
    job_id uuid DEFAULT NULL, checker_revision text DEFAULT NULL) RETURNS rm.verifier_reports
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE task jobs;
DECLARE result verifier_reports;
DECLARE revision text := checker_revision;
DECLARE base_revision text;
DECLARE scoped_revision text;
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
        base_revision := task.payload ->> 'checker_revision';
        IF task.kind = 'verify' THEN
            scoped_revision := local_final_checker_revision(task.payload);
        END IF;
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
    IF job_id IS NOT NULL AND revision IS NOT NULL
        AND revision IS DISTINCT FROM base_revision AND revision IS DISTINCT FROM scoped_revision THEN
        RAISE EXCEPTION 'the supplied checker revision differs from the exact verifier job revision'
            USING ERRCODE = '23514';
    END IF;
    revision := coalesce(revision, scoped_revision, base_revision);
    IF kind = 'verifier_vulnerability' AND revision IS NULL THEN
        RAISE EXCEPTION 'a verifier vulnerability must identify the exact checker revision' USING ERRCODE = '23514';
    END IF;
    IF revision IS NOT NULL AND (revision !~ '^[0-9a-f]{64}$' OR (
        job_id IS NULL
        AND NOT EXISTS (SELECT 1 FROM evidence e WHERE e.code_revision = revision)
        AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = 'verify' AND revision IN (
            j.payload ->> 'checker_revision', local_final_checker_revision(j.payload)))
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

CREATE OR REPLACE FUNCTION rm.scan_verifier_window(start_at timestamptz, end_at timestamptz)
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
        SELECT selected.revision, a.finding,
               a.submitted_at >= start_at AS current_window
        FROM attempts a JOIN jobs j ON j.id = a.job_id
        CROSS JOIN LATERAL (
            SELECT coalesce(local_final_checker_revision(j.payload), j.payload ->> 'checker_revision') AS revision
        ) selected
        WHERE a.role = 'verify' AND selected.revision ~ '^[0-9a-f]{64}$'
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
        SELECT coalesce(local_receipt_checker_revision(e.lean_check_id, e.verification_attempt_id), e.code_revision) AS revision,
            count(*) FILTER (WHERE e.verdict = 'verified') AS verified,
            count(*) FILTER (WHERE e.review_status = 'returned') AS returned,
            count(*) FILTER (WHERE e.needs_recheck) AS stale,
            count(*) FILTER (WHERE e.verdict = 'verified' AND e.diff_status <> 'exact') AS different_scope
        FROM evidence_foundation_status e WHERE e.issued_at >= start_at AND e.issued_at < end_at
        GROUP BY 1
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

CREATE OR REPLACE FUNCTION rm.check_checker_hold() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE base_revision text;
DECLARE scoped_revision text;
BEGIN
    IF TG_TABLE_NAME = 'jobs' THEN
        base_revision := NEW.payload ->> 'checker_revision';
        scoped_revision := local_final_checker_revision(NEW.payload);
    ELSIF TG_TABLE_NAME = 'claims' THEN
        SELECT e.code_revision, local_receipt_checker_revision(e.lean_check_id, e.verification_attempt_id)
        INTO base_revision, scoped_revision
        FROM evidence e WHERE e.id = NEW.decisive_evidence_id;
    ELSE
        base_revision := NEW.code_revision;
        scoped_revision := local_receipt_checker_revision(NEW.lean_check_id, NEW.verification_attempt_id);
    END IF;
    IF base_revision IS NOT NULL OR scoped_revision IS NOT NULL THEN
        PERFORM lock_live_foundation();
        IF EXISTS (SELECT 1 FROM checker_hold_status h WHERE h.released_at IS NULL
            AND h.checker_revision IN (base_revision, scoped_revision)) THEN
            RAISE EXCEPTION 'checker revision awaiting researcher review after a monitoring alert' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
