-- A sequence can allocate a lower ID to a transaction that commits later.
-- This workspace-local locked row makes the visible cursor follow commit order.
CREATE TABLE rm.workspace_event_heads (
    workspace_id uuid PRIMARY KEY,
    last_id bigint NOT NULL CHECK (last_id > 0)
);
CREATE TABLE rm.workspace_events (
    workspace_id uuid NOT NULL REFERENCES rm.workspace_event_heads(workspace_id),
    id bigint NOT NULL CHECK (id > 0),
    type text NOT NULL CHECK (type ~ '^[a-z][a-z0-9_]*[.][a-z][a-z0-9_]*$'),
    schema_version integer NOT NULL CHECK (schema_version > 0),
    occurred_at timestamptz NOT NULL,
    user_id uuid NOT NULL REFERENCES rm.local_users(id),
    subject_kind text NOT NULL CHECK (subject_kind ~ '^[a-z][a-z0-9_]*$'),
    subject_id uuid NOT NULL,
    data jsonb NOT NULL CHECK (jsonb_typeof(data) = 'object'),
    PRIMARY KEY (workspace_id, id)
);
REVOKE ALL ON rm.workspace_event_heads, rm.workspace_events FROM PUBLIC;
CREATE TRIGGER workspace_events_append_only BEFORE UPDATE OR DELETE ON rm.workspace_events
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.require_workspace_event_reader(target_workspace uuid) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT is_local_human_session() OR NOT EXISTS (SELECT 1 FROM current_local_user())
        OR NOT EXISTS (SELECT 1 FROM current_workspace_binding() b WHERE b.workspace_id = target_workspace) THEN
        RAISE EXCEPTION 'the requested workspace event stream is unavailable to this session'
            USING ERRCODE = '42501';
    END IF;
END $$;

CREATE FUNCTION rm.workspace_event_cursor(target_workspace uuid) RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_workspace_event_reader(target_workspace);
    RETURN coalesce((SELECT head.last_id FROM workspace_event_heads head WHERE head.workspace_id = target_workspace), 0);
END $$;

CREATE FUNCTION rm.read_workspace_events(target_workspace uuid, after_id bigint, page_limit integer)
RETURNS TABLE (
    id bigint, type text, schema_version integer, workspace_id uuid, occurred_at timestamptz,
    user_id uuid, subject_kind text, subject_id uuid, data jsonb
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE latest bigint;
BEGIN
    latest := workspace_event_cursor(target_workspace);
    IF after_id IS NULL OR after_id < 0 OR after_id > latest
        OR page_limit IS NULL OR page_limit NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION 'invalid event cursor or page size' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
        SELECT e.id, e.type, e.schema_version, e.workspace_id, e.occurred_at,
               e.user_id, e.subject_kind, e.subject_id, e.data
        FROM workspace_events e
        WHERE e.workspace_id = target_workspace AND e.id > after_id
        ORDER BY e.id LIMIT page_limit;
END $$;

CREATE FUNCTION rm.capture_evidence_review_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid;
DECLARE event_id bigint;
BEGIN
    SELECT b.workspace_id INTO STRICT workspace FROM current_workspace_binding() b;
    IF workspace IS NULL THEN
        RAISE EXCEPTION 'an attributed review requires a bound workspace' USING ERRCODE = '23514';
    END IF;
    INSERT INTO workspace_event_heads AS head (workspace_id, last_id) VALUES (workspace, 1)
    ON CONFLICT (workspace_id) DO UPDATE SET last_id = head.last_id + 1
    RETURNING head.last_id INTO event_id;
    INSERT INTO workspace_events (
        workspace_id, id, type, schema_version, occurred_at, user_id, subject_kind, subject_id, data
    ) VALUES (
        workspace, event_id, 'evidence.reviewed', 1, NEW.decided_at, NEW.user_id, 'evidence', NEW.evidence_id,
        jsonb_build_object('decision_id', NEW.id, 'claim_id', NEW.claim_id, 'action', NEW.action)
    );
    -- Notifications can coalesce. Readers fetch the committed rows after their
    -- durable cursor, so no notification payload or one-to-one delivery is needed.
    PERFORM pg_notify('rm_workspace_events', '');
    RETURN NEW;
END $$;

-- Acquire the workspace cursor after ordinary evidence-review validation and
-- locks. The deferred trigger still writes inside the same review transaction.
CREATE CONSTRAINT TRIGGER publish_evidence_review_event
    AFTER INSERT ON rm.decisions DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (
        NEW.user_id IS NOT NULL AND NEW.decided_by = 'human'
        AND NEW.evidence_id IS NOT NULL AND NEW.action IN ('approve', 'return')
    )
    EXECUTE FUNCTION rm.capture_evidence_review_event();

REVOKE ALL ON FUNCTION rm.require_workspace_event_reader(uuid), rm.capture_evidence_review_event(),
    rm.workspace_event_cursor(uuid), rm.read_workspace_events(uuid, bigint, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.workspace_event_cursor(uuid), rm.read_workspace_events(uuid, bigint, integer) TO rm_human;
