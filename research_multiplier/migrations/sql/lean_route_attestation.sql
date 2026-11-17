CREATE TABLE rm.lean_route_attestations (
    campaign_sha256 rm.sha256_hex NOT NULL,
    review_sha256 rm.sha256_hex NOT NULL,
    expected_event_id uuid REFERENCES rm.lean_route_events(id),
    baseline jsonb NOT NULL,
    replacement jsonb NOT NULL,
    measurements jsonb NOT NULL,
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (campaign_sha256, review_sha256),
    CHECK (rm.valid_lean_route_binding(baseline) AND rm.valid_lean_route_binding(replacement)),
    CHECK (baseline <> replacement AND baseline ->> 'search_revision' = replacement ->> 'search_revision'),
    CHECK (jsonb_typeof(measurements) = 'object' AND octet_length(measurements::text) <= 12000)
);
REVOKE ALL ON rm.lean_route_attestations FROM PUBLIC;
CREATE TRIGGER lean_route_attestations_append_only
    BEFORE UPDATE OR DELETE ON rm.lean_route_attestations
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.attest_lean_route(expected_event uuid, campaign rm.sha256_hex, reviewed rm.sha256_hex,
    original jsonb, replacement jsonb, counts jsonb) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    INSERT INTO lean_route_attestations(campaign_sha256, review_sha256, expected_event_id, baseline,
                                       replacement, measurements)
    VALUES ($2, $3, $1, $4, $5, $6)
    ON CONFLICT (campaign_sha256, review_sha256) DO NOTHING;
    IF NOT EXISTS (
        SELECT 1 FROM lean_route_attestations attested
        WHERE attested.campaign_sha256 = $2 AND attested.review_sha256 = $3
          AND attested.expected_event_id IS NOT DISTINCT FROM $1
          AND attested.baseline = $4 AND attested.replacement = $5 AND attested.measurements = $6
    ) THEN
        RAISE EXCEPTION 'this Lean campaign and review already have a different attestation' USING ERRCODE = '23514';
    END IF;
END $$;
REVOKE ALL ON FUNCTION rm.attest_lean_route(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.attest_lean_route(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb) TO rm_verifier;

ALTER FUNCTION rm.activate_lean_route(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb,text,text)
    RENAME TO commit_lean_route_activation;
REVOKE ALL ON FUNCTION rm.commit_lean_route_activation(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb,text,text)
    FROM rm_human;

CREATE FUNCTION rm.activate_lean_route(expected_event uuid, campaign rm.sha256_hex, reviewed rm.sha256_hex,
    original jsonb, replacement jsonb, counts jsonb, actor text, explanation text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM lean_route_attestations attested
        WHERE attested.campaign_sha256 = $2 AND attested.review_sha256 = $3
          AND attested.expected_event_id IS NOT DISTINCT FROM $1
          AND attested.baseline = $4 AND attested.replacement = $5 AND attested.measurements = $6
    ) THEN
        RAISE EXCEPTION 'Lean route activation requires the exact verifier attestation' USING ERRCODE = '23514';
    END IF;
    RETURN commit_lean_route_activation($1, $2, $3, $4, $5, $6, $7, $8);
END $$;
REVOKE ALL ON FUNCTION rm.activate_lean_route(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.activate_lean_route(uuid,rm.sha256_hex,rm.sha256_hex,jsonb,jsonb,jsonb,text,text) TO rm_human;
