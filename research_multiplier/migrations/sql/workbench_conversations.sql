-- A project database has one workspace binding. Still require the bound,
-- authenticated local researcher to read or write conversation content.
CREATE TABLE rm.conversation_threads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES rm.local_users(id),
    title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    claim_id uuid REFERENCES rm.claims(id),
    statement_id uuid REFERENCES rm.lean_statements(id),
    node_id text CHECK (length(node_id) BETWEEN 1 AND 200),
    revision bigint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    archived_at timestamptz,
    CHECK (claim_id IS NOT NULL OR (statement_id IS NULL AND node_id IS NULL))
);
CREATE INDEX conversation_threads_by_scope ON rm.conversation_threads (workspace_id, user_id, updated_at DESC)
    WHERE archived_at IS NULL;

CREATE TABLE rm.conversation_turns (
    id uuid PRIMARY KEY,
    thread_id uuid NOT NULL REFERENCES rm.conversation_threads(id),
    position bigint NOT NULL,
    text text NOT NULL CHECK (length(btrim(text)) BETWEEN 1 AND 8000),
    connection jsonb NOT NULL CHECK (jsonb_typeof(connection) = 'object'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_sha256 rm.sha256_hex NOT NULL,
    external boolean NOT NULL,
    authorized_by uuid NOT NULL REFERENCES rm.local_users(id),
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    deadline timestamptz NOT NULL DEFAULT clock_timestamp() + interval '120 seconds',
    finished_at timestamptz,
    reply text CHECK (length(btrim(reply)) BETWEEN 1 AND 16000),
    model jsonb CHECK (model IS NULL OR jsonb_typeof(model) = 'object'),
    error_code text CHECK (length(error_code) BETWEEN 1 AND 100),
    UNIQUE (thread_id, position),
    CHECK ((finished_at IS NULL AND reply IS NULL AND error_code IS NULL)
        OR (finished_at IS NOT NULL AND num_nonnulls(reply, error_code) = 1))
);
CREATE INDEX conversation_turns_in_flight ON rm.conversation_turns (deadline) WHERE finished_at IS NULL;

CREATE VIEW rm.visible_conversation_threads WITH (security_barrier = true) AS
    SELECT t.* FROM rm.conversation_threads t
    -- Restore may reidentify the bound database. The database and restored
    -- local researcher remain the scope; workspace_id records original origin.
    JOIN rm.current_workspace_binding() b ON b.workspace_id IS NOT NULL
    JOIN rm.current_local_user() u ON u.id = t.user_id
    WHERE t.archived_at IS NULL;

CREATE VIEW rm.visible_conversation_turns WITH (security_barrier = true) AS
    SELECT t.* FROM rm.conversation_turns t
    JOIN rm.visible_conversation_threads c ON c.id = t.thread_id;
GRANT SELECT ON rm.visible_conversation_threads, rm.visible_conversation_turns TO rm_human;

CREATE FUNCTION rm.create_conversation_thread(title text, claim uuid, statement uuid, node text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE actor uuid;
DECLARE workspace uuid;
DECLARE created uuid;
BEGIN
    SELECT u.id INTO actor FROM current_local_user() u;
    SELECT b.workspace_id INTO workspace FROM current_workspace_binding() b;
    IF actor IS NULL OR workspace IS NULL OR NOT is_local_human_session() THEN
        RAISE EXCEPTION 'Conversation requires a local researcher' USING ERRCODE = '42501';
    END IF;
    IF ($2 IS NOT NULL AND NOT EXISTS (SELECT FROM claims c WHERE c.id = $2 AND frozen_at IS NOT NULL))
        OR ($3 IS NOT NULL AND NOT EXISTS (
            SELECT FROM lean_statement_status s WHERE s.id = $3 AND s.claim_id = $2 AND s."current"
        )) THEN
        RAISE EXCEPTION 'Conversation scope changed' USING ERRCODE = '23514';
    END IF;
    INSERT INTO conversation_threads (workspace_id,user_id,title,claim_id,statement_id,node_id)
        VALUES (workspace,actor,$1,$2,$3,$4) RETURNING id INTO created;
    RETURN created;
END $$;

CREATE FUNCTION rm.archive_conversation_thread(target uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE thread conversation_threads;
BEGIN
    SELECT t.* INTO thread FROM conversation_threads t JOIN visible_conversation_threads v USING (id)
        WHERE t.id = $1 AND t.archived_at IS NULL FOR UPDATE OF t;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    IF EXISTS (SELECT FROM conversation_turns WHERE thread_id = $1 AND finished_at IS NULL AND deadline > clock_timestamp()) THEN
        RAISE EXCEPTION 'Conversation has an active turn' USING ERRCODE = '55006';
    END IF;
    UPDATE conversation_threads SET archived_at = clock_timestamp(), revision = revision + 1 WHERE id = $1;
    RETURN true;
END $$;

CREATE FUNCTION rm.begin_conversation_turn(target uuid, request uuid, expected_revision bigint,
    text text, connection jsonb, payload jsonb, payload_sha256 text, external boolean)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE thread conversation_threads;
DECLARE existing conversation_turns;
BEGIN
    SELECT t.* INTO thread FROM conversation_threads t JOIN visible_conversation_threads v USING (id)
        WHERE t.id = $1 AND t.archived_at IS NULL FOR UPDATE OF t;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Conversation unavailable' USING ERRCODE = 'P0002';
    END IF;
    SELECT * INTO existing FROM conversation_turns WHERE id = $2;
    IF FOUND THEN
        IF (existing.thread_id, existing.text, existing.connection) IS DISTINCT FROM ($1, $4, $5) THEN
            RAISE EXCEPTION 'Conversation request conflicts' USING ERRCODE = '23505';
        END IF;
        RETURN false;
    END IF;
    IF thread.revision <> $3 THEN
        RAISE EXCEPTION 'Conversation preview expired' USING ERRCODE = '23514';
    END IF;
    -- A workspace can have one active request at a time. This bound also
    -- applies across windows and avoids unbounded native model processes.
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.workbench_conversations', 49));
    IF EXISTS (SELECT FROM conversation_turns
        WHERE finished_at IS NULL AND deadline > clock_timestamp()) THEN
        RAISE EXCEPTION 'A conversation turn is already active' USING ERRCODE = '55006';
    END IF;
    INSERT INTO conversation_turns (id,thread_id,position,text,connection,payload,payload_sha256,external,authorized_by)
        VALUES ($2,$1,thread.revision + 1,$4,$5,$6,$7,$8,thread.user_id);
    UPDATE conversation_threads SET updated_at = clock_timestamp(), revision = revision + 1 WHERE id = $1;
    RETURN true;
END $$;

CREATE FUNCTION rm.finish_conversation_turn(target uuid, request uuid, answer text, reported jsonb, failure text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    -- Lock order matches begin/archive. Finished turns are immutable.
    PERFORM FROM conversation_threads t JOIN visible_conversation_threads v USING (id)
        WHERE t.id = $1 AND t.archived_at IS NULL FOR UPDATE OF t;
    IF NOT FOUND THEN
        RETURN;
    END IF;
    UPDATE conversation_turns SET finished_at = clock_timestamp(), reply = $3, model = $4, error_code = $5
    WHERE id = $2 AND thread_id = $1 AND finished_at IS NULL;
    IF FOUND THEN
        UPDATE conversation_threads SET updated_at = clock_timestamp(), revision = revision + 1 WHERE id = $1;
    END IF;
END $$;

CREATE CONSTRAINT TRIGGER conversation_update_event AFTER INSERT OR UPDATE ON rm.conversation_threads
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rm.capture_workspace_change_event('activity', 'claim');

REVOKE ALL ON FUNCTION rm.create_conversation_thread(text,uuid,uuid,text), rm.archive_conversation_thread(uuid),
    rm.begin_conversation_turn(uuid,uuid,bigint,text,jsonb,jsonb,text,boolean),
    rm.finish_conversation_turn(uuid,uuid,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.create_conversation_thread(text,uuid,uuid,text), rm.archive_conversation_thread(uuid),
    rm.begin_conversation_turn(uuid,uuid,bigint,text,jsonb,jsonb,text,boolean),
    rm.finish_conversation_turn(uuid,uuid,text,jsonb,text) TO rm_human;
