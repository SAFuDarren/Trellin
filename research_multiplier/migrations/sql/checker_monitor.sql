CREATE TABLE rm.checker_monitor_policies (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    min_attempts integer NOT NULL CHECK (min_attempts BETWEEN 5 AND 100000),
    jump_percent integer NOT NULL CHECK (jump_percent BETWEEN 1 AND 100),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.checker_holds (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_id uuid NOT NULL REFERENCES rm.checker_monitor_policies(id),
    checker_revision rm.sha256_hex NOT NULL,
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    attempts bigint NOT NULL CHECK (attempts > 0),
    successful_checks bigint NOT NULL CHECK (successful_checks >= 0 AND successful_checks <= attempts),
    prior_attempts bigint NOT NULL CHECK (prior_attempts > 0),
    prior_successful_checks bigint NOT NULL CHECK (prior_successful_checks >= 0 AND prior_successful_checks <= prior_attempts),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (policy_id, checker_revision, window_start, window_end)
);
CREATE INDEX checker_holds_by_revision ON rm.checker_holds(checker_revision);

CREATE TABLE rm.checker_hold_releases (
    hold_id uuid PRIMARY KEY REFERENCES rm.checker_holds(id),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    released_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TRIGGER monitor_policy_immutable BEFORE UPDATE OR DELETE ON rm.checker_monitor_policies
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER monitor_hold_immutable BEFORE UPDATE OR DELETE ON rm.checker_holds
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER monitor_release_immutable BEFORE UPDATE OR DELETE ON rm.checker_hold_releases
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE VIEW rm.checker_hold_status WITH (security_barrier = true) AS
    SELECT h.*, released.released_at, released.actor_ref, released.reason
    FROM rm.checker_holds h LEFT JOIN rm.checker_hold_releases released ON released.hold_id = h.id;

CREATE FUNCTION rm.record_checker_monitor_policy(min_attempts integer, jump_percent integer, actor_ref text, reason text)
RETURNS rm.checker_monitor_policies LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    INSERT INTO checker_monitor_policies(min_attempts, jump_percent, actor_ref, reason)
    VALUES ($1, $2, $3, $4) RETURNING *
$$;

CREATE FUNCTION rm.monitor_checker_window(start_at timestamptz, end_at timestamptz)
RETURNS SETOF rm.checker_holds LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE policy checker_monitor_policies;
BEGIN
    SELECT * INTO policy FROM checker_monitor_policies p ORDER BY p.sequence_no DESC LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'checker monitoring requires a reviewed policy' USING ERRCODE = '23514';
    END IF;
    PERFORM advance_foundation();
    RETURN QUERY INSERT INTO checker_holds (policy_id, checker_revision, window_start, window_end,
        attempts, successful_checks, prior_attempts, prior_successful_checks)
    SELECT policy.id, s.checker_revision, start_at, end_at, s.attempts, s.successful_checks,
           s.prior_attempts, s.prior_successful_checks
    FROM scan_verifier_window(start_at, end_at) s
    WHERE NOT s.retired
      AND s.attempts >= policy.min_attempts AND s.prior_attempts >= policy.min_attempts
      AND 100 * (s.successful_checks::numeric * s.prior_attempts - s.prior_successful_checks::numeric * s.attempts)
          >= policy.jump_percent::numeric * s.attempts * s.prior_attempts
      AND NOT EXISTS (SELECT 1 FROM checker_hold_status h WHERE h.checker_revision = s.checker_revision
                      AND h.released_at IS NULL)
    ON CONFLICT DO NOTHING RETURNING *;
END $$;

CREATE FUNCTION rm.release_checker_hold(hold_id uuid, actor_ref text, reason text)
RETURNS rm.checker_hold_releases LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved checker_hold_releases;
BEGIN
    PERFORM advance_foundation();
    INSERT INTO checker_hold_releases (hold_id, actor_ref, reason) VALUES (hold_id, actor_ref, reason)
    ON CONFLICT ON CONSTRAINT checker_hold_releases_pkey DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM checker_hold_releases r WHERE r.hold_id = release_checker_hold.hold_id;
        IF saved.actor_ref IS DISTINCT FROM actor_ref OR saved.reason IS DISTINCT FROM reason THEN
            RAISE EXCEPTION 'checker hold already released under a different decision' USING ERRCODE = '23505';
        END IF;
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.check_checker_hold() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE revision text;
BEGIN
    IF TG_TABLE_NAME = 'jobs' THEN
        revision := NEW.payload ->> 'checker_revision';
    ELSIF TG_TABLE_NAME = 'claims' THEN
        SELECT e.code_revision::text INTO revision FROM evidence e WHERE e.id = NEW.decisive_evidence_id;
    ELSE
        revision := NEW.code_revision::text;
    END IF;
    IF revision IS NOT NULL THEN
        PERFORM lock_live_foundation();
        IF EXISTS (SELECT 1 FROM checker_hold_status h WHERE h.checker_revision = revision AND h.released_at IS NULL) THEN
            RAISE EXCEPTION 'checker revision awaiting researcher review after a monitoring alert' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER checker_hold_on_verification BEFORE INSERT ON rm.jobs FOR EACH ROW
    WHEN (NEW.kind = 'verify') EXECUTE FUNCTION rm.check_checker_hold();
CREATE TRIGGER checker_hold_on_lease BEFORE UPDATE OF lease_generation ON rm.jobs FOR EACH ROW
    WHEN (NEW.kind = 'verify' AND NEW.lease_generation > OLD.lease_generation) EXECUTE FUNCTION rm.check_checker_hold();
CREATE TRIGGER checker_hold_on_receipt BEFORE INSERT ON rm.evidence FOR EACH ROW
    WHEN (NEW.verdict = 'verified') EXECUTE FUNCTION rm.check_checker_hold();
CREATE TRIGGER checker_hold_on_promotion BEFORE UPDATE OF status ON rm.claims FOR EACH ROW
    WHEN (OLD.status IS DISTINCT FROM NEW.status AND NEW.status IN ('proved', 'refuted'))
    EXECUTE FUNCTION rm.check_checker_hold();

REVOKE ALL ON FUNCTION rm.record_checker_monitor_policy(integer, integer, text, text),
    rm.monitor_checker_window(timestamptz, timestamptz), rm.release_checker_hold(uuid, text, text),
    rm.check_checker_hold() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_checker_monitor_policy(integer, integer, text, text),
    rm.release_checker_hold(uuid, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.monitor_checker_window(timestamptz, timestamptz) TO rm_verifier;
GRANT SELECT ON rm.checker_hold_status, rm.checker_monitor_policies TO rm_human, rm_verifier;
