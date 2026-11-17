CREATE TABLE rm.research_review_policies (
    sequence_no bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    max_pending integer CHECK (max_pending BETWEEN 1 AND 1000),
    weekly_minutes integer CHECK (weekly_minutes BETWEEN 1 AND 10080),
    require_understanding boolean NOT NULL,
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER research_review_policy_append_only
    BEFORE UPDATE OR DELETE ON rm.research_review_policies
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.record_research_review_policy(max_pending integer, weekly_minutes integer,
    require_understanding boolean, actor_ref text, reason text)
RETURNS rm.research_review_policies
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    INSERT INTO research_review_policies(max_pending, weekly_minutes, require_understanding, actor_ref, reason)
    VALUES ($1, $2, $3, $4, $5) RETURNING *
$$;

-- Return only available slots, never contents of the private review inbox.
CREATE FUNCTION rm.research_review_capacity() RETURNS integer
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE policy research_review_policies;
DECLARE pending integer;
BEGIN
    SELECT * INTO policy FROM research_review_policies ORDER BY sequence_no DESC LIMIT 1;
    IF policy.weekly_minutes IS NOT NULL AND (
        SELECT coalesce(sum(minutes), 0) FROM human_time_entries
        WHERE activity = 'review' AND logged_at >= statement_timestamp() - interval '7 days'
    ) >= policy.weekly_minutes THEN
        RETURN 0;
    END IF;
    IF policy.max_pending IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT count(*) INTO pending FROM (
        SELECT 1 FROM research_actionable_review_inbox WHERE kind = 'review_evidence'
        LIMIT policy.max_pending
    ) AS actionable;
    RETURN policy.max_pending - pending;
END $$;

CREATE FUNCTION rm.require_proof_understanding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF (SELECT require_understanding FROM research_review_policies ORDER BY sequence_no DESC LIMIT 1)
       AND EXISTS (SELECT 1 FROM evidence e WHERE e.id = NEW.evidence_id AND e.kind IN ('lean', 'audited_proof'))
       AND NOT EXISTS (
           SELECT 1 FROM research_understanding u WHERE u.evidence_id = NEW.evidence_id
             AND u.actor_ref = NEW.actor_ref AND u.authenticated_role = session_user
       ) THEN
        RAISE EXCEPTION 'review this proof with a researcher explanation and takeaway' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER require_proof_understanding BEFORE INSERT ON rm.decisions
    FOR EACH ROW WHEN (NEW.action = 'approve') EXECUTE FUNCTION rm.require_proof_understanding();

CREATE FUNCTION rm.review_with_notes(evidence_id uuid, action text, actor_ref text, reason text,
    explanation text, lesson text, minutes numeric) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target uuid;
DECLARE decision_id uuid;
BEGIN
    SELECT e.claim_id INTO STRICT target FROM evidence e WHERE e.id = $1;
    -- Match the lock order of approval, return and promotion.
    PERFORM 1 FROM claims WHERE id = target FOR UPDATE;
    IF $5 IS NOT NULL OR $6 IS NOT NULL THEN
        IF $2 IS DISTINCT FROM 'approve' THEN
            RAISE EXCEPTION 'an understanding record belongs to an approval' USING ERRCODE = '22023';
        END IF;
        PERFORM record_research_understanding($1, $3, $5, $6);
    END IF;
    decision_id := review_evidence($1, $2, $3, $4);
    IF $7 IS NOT NULL THEN
        IF $7 <= 0 OR $7 > 10080 THEN
            RAISE EXCEPTION 'enter a positive review duration of at most one week' USING ERRCODE = '22023';
        END IF;
        PERFORM log_human_time(target, 'review', $7);
    END IF;
    RETURN decision_id;
END $$;

REVOKE ALL ON FUNCTION rm.record_research_review_policy(integer, integer, boolean, text, text),
    rm.research_review_capacity(), rm.require_proof_understanding(),
    rm.review_with_notes(uuid, text, text, text, text, text, numeric) FROM PUBLIC;
GRANT SELECT ON rm.research_review_policies TO rm_human;
GRANT EXECUTE ON FUNCTION rm.record_research_review_policy(integer, integer, boolean, text, text),
    rm.review_with_notes(uuid, text, text, text, text, text, numeric) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.research_review_capacity() TO rm_human, rm_scheduler;
