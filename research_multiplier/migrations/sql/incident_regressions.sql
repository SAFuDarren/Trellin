CREATE TABLE rm.incident_regressions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id uuid NOT NULL REFERENCES rm.verifier_reports(id),
    test_case text NOT NULL CHECK (btrim(test_case) <> '' AND length(test_case) <= 240),
    artifact_uri text NOT NULL,
    artifact_sha256 rm.sha256_hex NOT NULL,
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (artifact_uri = 'urn:sha256:' || artifact_sha256),
    CONSTRAINT incident_regression_snapshot UNIQUE (report_id, test_case, artifact_sha256)
);
CREATE TRIGGER incident_regressions_append_only BEFORE UPDATE OR DELETE ON rm.incident_regressions
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.record_incident_regression(report_id uuid, test_case text, artifact jsonb, actor_ref text, reason text)
RETURNS rm.incident_regressions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE result incident_regressions;
BEGIN
    INSERT INTO incident_regressions AS r (report_id, test_case, artifact_uri, artifact_sha256, actor_ref, reason)
    VALUES ($1, $2, $3 ->> 'uri', $3 ->> 'sha256', $4, $5)
    ON CONFLICT ON CONSTRAINT incident_regression_snapshot DO NOTHING RETURNING * INTO result;
    IF NOT FOUND THEN
        SELECT * INTO STRICT result FROM incident_regressions r
        WHERE r.report_id = $1 AND r.test_case = $2 AND r.artifact_sha256 = $3 ->> 'sha256';
    END IF;
    RETURN result;
END $$;
REVOKE ALL ON FUNCTION rm.record_incident_regression(uuid,text,jsonb,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_incident_regression(uuid,text,jsonb,text,text) TO rm_human;
GRANT SELECT ON rm.incident_regressions TO rm_human;
