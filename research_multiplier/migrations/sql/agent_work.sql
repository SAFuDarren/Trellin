CREATE TABLE rm.agent_work_items (
    id uuid PRIMARY KEY,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES rm.local_users(id),
    spec jsonb NOT NULL CHECK (jsonb_typeof(spec) = 'object'),
    generation integer NOT NULL DEFAULT 1 CHECK (generation > 0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX agent_work_items_by_workspace ON rm.agent_work_items (workspace_id, user_id, created_at DESC, id);

CREATE TABLE rm.agent_work_attempts (
    task_id uuid NOT NULL REFERENCES rm.agent_work_items(id),
    generation integer NOT NULL CHECK (generation > 0),
    retry_key uuid UNIQUE,
    queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    worker_id uuid,
    started_at timestamptz,
    expires_at timestamptz,
    finished_at timestamptz,
    state text NOT NULL DEFAULT 'queued'
        CHECK (state IN ('queued', 'running', 'interrupted', 'blocked', 'failed', 'delivered')),
    result_sha256 rm.sha256_hex,
    error text CHECK (length(error) BETWEEN 1 AND 100 AND error ~ '^[A-Za-z][A-Za-z0-9_.-]*$'),
    PRIMARY KEY (task_id, generation),
    UNIQUE (task_id, generation, result_sha256),
    CHECK ((generation = 1) = (retry_key IS NULL)),
    CHECK (num_nonnulls(worker_id, started_at, expires_at) IN (0, 3)),
    CHECK (state NOT IN ('running', 'interrupted', 'failed', 'delivered') OR worker_id IS NOT NULL),
    CHECK (state NOT IN ('queued', 'blocked') OR worker_id IS NULL),
    CHECK ((state IN ('queued', 'running')) = (finished_at IS NULL)),
    CHECK ((state = 'delivered') = (result_sha256 IS NOT NULL)),
    CHECK ((state IN ('interrupted', 'blocked', 'failed')) = (error IS NOT NULL))
);
CREATE UNIQUE INDEX agent_work_one_active_attempt ON rm.agent_work_attempts (task_id)
    WHERE state IN ('queued', 'running');
CREATE INDEX agent_work_attempt_queue ON rm.agent_work_attempts (queued_at, task_id) WHERE state = 'queued';

CREATE TABLE rm.agent_work_reviews (
    id uuid PRIMARY KEY,
    task_id uuid NOT NULL,
    generation integer NOT NULL,
    delivery_sha256 rm.sha256_hex NOT NULL,
    user_id uuid NOT NULL REFERENCES rm.local_users(id),
    decision text NOT NULL CHECK (decision IN ('accept', 'return')),
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 2000),
    reviewed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (task_id, generation),
    FOREIGN KEY (task_id, generation, delivery_sha256)
        REFERENCES rm.agent_work_attempts(task_id, generation, result_sha256)
);
REVOKE ALL ON rm.agent_work_items, rm.agent_work_attempts, rm.agent_work_reviews FROM PUBLIC;
CREATE TRIGGER agent_work_reviews_append_only BEFORE UPDATE OR DELETE ON rm.agent_work_reviews
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.agent_work_workspace() RETURNS uuid
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid;
BEGIN
    SELECT binding.workspace_id INTO workspace FROM current_workspace_binding() binding;
    IF workspace IS NULL THEN
        RAISE EXCEPTION 'Agent work requires a bound workspace' USING ERRCODE = '42501';
    END IF;
    RETURN workspace;
END $$;

CREATE FUNCTION rm.agent_work_user() RETURNS uuid
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE actor uuid;
BEGIN
    IF is_local_human_session() THEN
        SELECT account.id INTO actor FROM current_local_user() account;
    END IF;
    IF actor IS NULL THEN
        RAISE EXCEPTION 'Agent work requires an authenticated local researcher' USING ERRCODE = '42501';
    END IF;
    RETURN actor;
END $$;

CREATE VIEW rm.agent_work_snapshot AS
    SELECT item.id, item.workspace_id, item.user_id, item.spec, item.generation, attempt.result_sha256,
           CASE review.decision
               WHEN 'accept' THEN 'accepted' WHEN 'return' THEN 'returned'
               ELSE CASE WHEN attempt.state = 'running' AND attempt.expires_at <= clock_timestamp()
                    THEN 'interrupted' ELSE attempt.state END
           END AS state,
           item.created_at,
           greatest(item.created_at, attempt.queued_at, attempt.started_at, attempt.finished_at, review.reviewed_at)
               AS updated_at,
           jsonb_build_object('generation', attempt.generation, 'started_at', attempt.started_at,
               'expires_at', attempt.expires_at, 'finished_at', attempt.finished_at,
               'result_sha256', attempt.result_sha256,
               'error', CASE WHEN attempt.state = 'running' AND attempt.expires_at <= clock_timestamp()
                            THEN 'lease_expired' ELSE attempt.error END) AS attempt,
           CASE WHEN review.id IS NOT NULL THEN jsonb_build_object('id', review.id, 'generation', review.generation,
               'delivery_sha256', review.delivery_sha256, 'decision', review.decision, 'reason', review.reason,
               'reviewed_at', review.reviewed_at) END AS review
    FROM rm.agent_work_items item
    JOIN rm.agent_work_attempts attempt ON attempt.task_id = item.id AND attempt.generation = item.generation
    LEFT JOIN rm.agent_work_reviews review ON review.task_id = attempt.task_id AND review.generation = attempt.generation;
REVOKE ALL ON rm.agent_work_snapshot FROM PUBLIC;

CREATE FUNCTION rm.agent_work_projection(target uuid, workspace uuid, actor uuid DEFAULT NULL) RETURNS jsonb
LANGUAGE sql VOLATILE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT to_jsonb(work) - 'result_sha256' FROM agent_work_snapshot work
        WHERE work.id = target AND work.workspace_id = workspace AND (actor IS NULL OR work.user_id = actor)
$$;

CREATE FUNCTION rm.list_agent_work() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid := agent_work_workspace();
DECLARE actor uuid := agent_work_user();
BEGIN
    RETURN (SELECT coalesce(jsonb_agg(to_jsonb(work) - 'result_sha256'
                                         ORDER BY work.updated_at DESC, work.id), '[]'::jsonb)
            FROM agent_work_snapshot work WHERE work.workspace_id = workspace AND work.user_id = actor);
END $$;

CREATE FUNCTION rm.read_agent_work(target uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT agent_work_projection(target, agent_work_workspace(), agent_work_user())
$$;

CREATE FUNCTION rm.agent_work_predecessor_current(workspace uuid, actor uuid, spec jsonb) RETURNS boolean
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE predecessor uuid := (spec->>'predecessor_id')::uuid;
BEGIN
    IF predecessor IS NULL THEN
        RETURN true;
    END IF;
    -- Every transition locks its task first. A dependency can only refer to a
    -- previously delivered task, so these locks always follow earlier tasks.
    PERFORM 1 FROM agent_work_items task
        WHERE task.id = predecessor AND task.workspace_id = $1 AND task.user_id = $2 FOR SHARE;
    IF NOT FOUND THEN
        RETURN false;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM agent_work_snapshot previous
        WHERE previous.id = predecessor AND previous.workspace_id = $1
          AND previous.user_id = $2 AND previous.result_sha256 = $3->>'predecessor_sha256'
          AND previous.state IN ('delivered', 'accepted')
    );
END
$$;

CREATE FUNCTION rm.submit_agent_work(target uuid, spec jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid := agent_work_workspace();
DECLARE actor uuid := agent_work_user();
DECLARE existing agent_work_items;
BEGIN
    IF $1 IS NULL OR $2 IS NULL OR jsonb_typeof($2) <> 'object'
        OR coalesce(length(btrim($2->>'title')), 0) NOT BETWEEN 1 AND 200
        OR coalesce($2->>'mode', '') NOT IN ('implement', 'review', 'draft')
        OR coalesce($2->>'agent_id', '') !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
        OR coalesce($2->>'agent_revision', '') !~ '^[0-9]{1,19}$'
        OR coalesce(length(btrim(coalesce($2->>'agent_name', $2->>'name'))), 0) NOT BETWEEN 1 AND 200
        OR coalesce(jsonb_typeof($2->'connection'), '') <> 'object'
        OR coalesce($2->>'context_sha256', '') !~ '^[0-9a-f]{64}$'
        OR (($2->>'predecessor_id' IS NULL) <> ($2->>'predecessor_sha256' IS NULL))
        OR ($2->>'predecessor_id') !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
        OR ($2->>'predecessor_sha256') !~ '^[0-9a-f]{64}$'
        OR ($2->>'mode' = 'review' AND $2->>'predecessor_id' IS NULL)
        OR $2->>'predecessor_id' = $1::text THEN
        RAISE EXCEPTION 'Invalid agent work scope' USING ERRCODE = '22023';
    END IF;

    -- Serialize duplicate task creation before checking that the input is current.
    -- A replay returns the original task even after its predecessor changes.
    PERFORM pg_advisory_xact_lock(hashtextextended($1::text, 9083));
    SELECT item.* INTO existing FROM agent_work_items item WHERE item.id = $1;
    IF FOUND THEN
        IF ROW(existing.workspace_id, existing.user_id) IS DISTINCT FROM ROW(workspace, actor) THEN
            RAISE EXCEPTION 'Agent work is unavailable' USING ERRCODE = 'P0002';
        END IF;
        IF existing.spec <> $2 THEN
            RAISE EXCEPTION 'Agent work request conflicts' USING ERRCODE = '23505';
        END IF;
        RETURN agent_work_projection($1, workspace, actor);
    END IF;
    IF NOT agent_work_predecessor_current(workspace, actor, $2) THEN
        RAISE EXCEPTION 'Agent work input is unavailable or superseded' USING ERRCODE = '23514';
    END IF;
    INSERT INTO agent_work_items (id, workspace_id, user_id, spec) VALUES ($1, workspace, actor, $2);
    INSERT INTO agent_work_attempts (task_id, generation) VALUES ($1, 1);
    RETURN agent_work_projection($1, workspace, actor);
END $$;

CREATE FUNCTION rm.claim_agent_work(worker uuid, lease_seconds integer) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid := agent_work_workspace();
DECLARE item agent_work_items;
DECLARE now_at timestamptz;
BEGIN
    IF $1 IS NULL OR $2 IS NULL OR $2 NOT BETWEEN 1 AND 3600 THEN
        RAISE EXCEPTION 'Invalid agent work lease' USING ERRCODE = '22023';
    END IF;
    -- An expired execution is visible and can be retried explicitly. Never
    -- restart it automatically because the tool might already have acted.
    WITH expired AS (
        SELECT task.id, task.generation FROM agent_work_attempts attempt
        JOIN agent_work_items task ON task.id = attempt.task_id AND task.generation = attempt.generation
        WHERE task.workspace_id = workspace AND attempt.state = 'running' AND attempt.expires_at <= clock_timestamp()
        FOR UPDATE OF task SKIP LOCKED
    )
    UPDATE agent_work_attempts attempt SET state = 'interrupted', finished_at = clock_timestamp(), error = 'lease_expired'
    FROM expired WHERE attempt.task_id = expired.id AND attempt.generation = expired.generation
      AND attempt.state = 'running' AND attempt.expires_at <= clock_timestamp();
    LOOP
        SELECT task.* INTO item FROM agent_work_attempts attempt
        JOIN agent_work_items task ON task.id = attempt.task_id AND task.generation = attempt.generation
        WHERE task.workspace_id = workspace AND attempt.state = 'queued'
        ORDER BY attempt.queued_at, task.id LIMIT 1 FOR UPDATE OF task SKIP LOCKED;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        now_at := clock_timestamp();
        IF NOT agent_work_predecessor_current(workspace, item.user_id, item.spec) THEN
            UPDATE agent_work_attempts SET state = 'blocked', finished_at = now_at, error = 'stale_predecessor'
            WHERE task_id = item.id AND generation = item.generation AND state = 'queued';
            CONTINUE;
        END IF;
        UPDATE agent_work_attempts SET state = 'running', worker_id = $1, started_at = now_at,
            expires_at = now_at + make_interval(secs => $2)
        WHERE task_id = item.id AND generation = item.generation AND state = 'queued';
        IF FOUND THEN
            RETURN agent_work_projection(item.id, workspace);
        END IF;
    END LOOP;
END $$;

CREATE FUNCTION rm.finish_agent_work(target uuid, generation integer, worker uuid, result_sha256 text, error text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid := agent_work_workspace();
DECLARE item agent_work_items;
DECLARE attempt agent_work_attempts;
DECLARE now_at timestamptz;
BEGIN
    IF $1 IS NULL OR $2 IS NULL OR $2 < 1 OR $3 IS NULL OR num_nonnulls($4, $5) <> 1
        OR $4 !~ '^[0-9a-f]{64}$' OR ($5 IS NOT NULL AND ($5 !~ '^[A-Za-z][A-Za-z0-9_.-]*$' OR length($5) > 100)) THEN
        RAISE EXCEPTION 'Invalid agent work receipt' USING ERRCODE = '22023';
    END IF;
    SELECT task.* INTO item FROM agent_work_items task
        WHERE task.id = $1 AND task.workspace_id = workspace FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Agent work is unavailable' USING ERRCODE = 'P0002';
    END IF;
    SELECT run.* INTO attempt FROM agent_work_attempts run
        WHERE run.task_id = item.id AND run.generation = $2;
    IF NOT FOUND OR attempt.worker_id IS DISTINCT FROM $3 THEN
        RAISE EXCEPTION 'Agent work lease is unavailable' USING ERRCODE = '23514';
    END IF;
    IF item.generation = $2 AND attempt.state IN ('delivered', 'failed')
        AND ROW(attempt.result_sha256, attempt.error) IS NOT DISTINCT FROM ROW($4, $5) THEN
        RETURN agent_work_projection(item.id, workspace);
    END IF;
    now_at := clock_timestamp();
    IF item.generation <> $2 OR attempt.state <> 'running' OR attempt.expires_at <= now_at THEN
        RAISE EXCEPTION 'Agent work lease expired or was superseded' USING ERRCODE = '23514';
    END IF;
    UPDATE agent_work_attempts SET state = CASE WHEN $4 IS NULL THEN 'failed' ELSE 'delivered' END,
        result_sha256 = $4, error = $5, finished_at = now_at
    WHERE task_id = item.id AND agent_work_attempts.generation = $2;
    RETURN agent_work_projection(item.id, workspace);
END $$;

CREATE FUNCTION rm.retry_agent_work(target uuid, action uuid, exact_context_sha256 text, expected_generation integer)
RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid := agent_work_workspace();
DECLARE actor uuid := agent_work_user();
DECLARE item agent_work_items;
DECLARE attempt agent_work_attempts;
BEGIN
    IF $1 IS NULL OR $2 IS NULL OR coalesce($3, '') !~ '^[0-9a-f]{64}$' OR $4 IS NULL OR $4 < 1 THEN
        RAISE EXCEPTION 'Invalid agent work retry' USING ERRCODE = '22023';
    END IF;
    SELECT task.* INTO item FROM agent_work_items task
        WHERE task.id = $1 AND task.workspace_id = workspace AND task.user_id = actor FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Agent work is unavailable' USING ERRCODE = 'P0002';
    END IF;
    IF item.spec->>'context_sha256' <> $3 THEN
        RAISE EXCEPTION 'Agent work scope changed' USING ERRCODE = '23514';
    END IF;
    SELECT run.* INTO attempt FROM agent_work_attempts run WHERE run.retry_key = $2;
    IF FOUND THEN
        IF attempt.task_id <> $1 OR attempt.generation - 1 <> $4 THEN
            RAISE EXCEPTION 'Agent work request conflicts' USING ERRCODE = '23505';
        END IF;
        RETURN agent_work_projection(item.id, workspace, actor)
            || jsonb_build_object('retry_generation', attempt.generation);
    END IF;
    IF item.generation <> $4 THEN
        RAISE EXCEPTION 'Agent work attempt changed before retry' USING ERRCODE = '23514';
    END IF;

    IF NOT agent_work_predecessor_current(workspace, actor, item.spec) THEN
        RAISE EXCEPTION 'Agent work input is unavailable or superseded' USING ERRCODE = '23514';
    END IF;
    SELECT run.* INTO STRICT attempt FROM agent_work_attempts run
        WHERE run.task_id = item.id AND run.generation = item.generation;
    IF attempt.state = 'running' AND attempt.expires_at <= clock_timestamp() THEN
        UPDATE agent_work_attempts SET state = 'interrupted', finished_at = clock_timestamp(), error = 'lease_expired'
            WHERE task_id = item.id AND generation = item.generation;
    ELSIF attempt.state NOT IN ('failed', 'interrupted') THEN
        RAISE EXCEPTION 'Agent work is not ready for retry' USING ERRCODE = '23514';
    END IF;
    INSERT INTO agent_work_attempts (task_id, generation, retry_key) VALUES (item.id, item.generation + 1, $2);
    UPDATE agent_work_items SET generation = item.generation + 1 WHERE id = item.id;
    RETURN agent_work_projection(item.id, workspace, actor)
        || jsonb_build_object('retry_generation', item.generation + 1);
END $$;

CREATE FUNCTION rm.review_agent_work(target uuid, action uuid, exact_delivery_sha256 text, decision text, reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid := agent_work_workspace();
DECLARE actor uuid := agent_work_user();
DECLARE item agent_work_items;
DECLARE previous agent_work_reviews;
BEGIN
    IF $1 IS NULL OR $2 IS NULL OR coalesce($3, '') !~ '^[0-9a-f]{64}$'
        OR coalesce($4, '') NOT IN ('accept', 'return') OR coalesce(length(btrim($5)), 0) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'Invalid agent work review' USING ERRCODE = '22023';
    END IF;
    SELECT task.* INTO item FROM agent_work_items task
        WHERE task.id = $1 AND task.workspace_id = workspace AND task.user_id = actor FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Agent work is unavailable' USING ERRCODE = 'P0002';
    END IF;
    SELECT review.* INTO previous FROM agent_work_reviews review WHERE review.id = $2;
    IF FOUND THEN
        IF ROW(previous.task_id, previous.delivery_sha256, previous.decision, previous.reason)
            IS DISTINCT FROM ROW($1, $3, $4, $5) THEN
            RAISE EXCEPTION 'Agent work request conflicts' USING ERRCODE = '23505';
        END IF;
        RETURN agent_work_projection(item.id, workspace, actor);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM agent_work_snapshot work WHERE work.id = item.id
                    AND work.state = 'delivered' AND work.result_sha256 = $3) THEN
        RAISE EXCEPTION 'Agent work delivery changed or has already been reviewed' USING ERRCODE = '23514';
    END IF;
    IF $4 = 'accept' AND NOT agent_work_predecessor_current(workspace, actor, item.spec) THEN
        RAISE EXCEPTION 'Agent work input is unavailable or superseded' USING ERRCODE = '23514';
    END IF;
    INSERT INTO agent_work_reviews (id, task_id, generation, delivery_sha256, user_id, decision, reason)
        VALUES ($2, item.id, item.generation, $3, actor, $4, $5);
    RETURN agent_work_projection(item.id, workspace, actor);
END $$;

CREATE FUNCTION rm.capture_agent_work_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE actor uuid;
DECLARE state text;
BEGIN
    IF TG_TABLE_NAME = 'agent_work_reviews' THEN
        actor := NEW.user_id;
        state := CASE NEW.decision WHEN 'accept' THEN 'accepted' ELSE 'returned' END;
    ELSE
        IF is_local_human_session() THEN
            actor := agent_work_user();
        END IF;
        state := NEW.state;
    END IF;
    PERFORM append_workspace_event('agent_work.changed', clock_timestamp(), actor, 'agent_work', NEW.task_id,
                                   jsonb_build_object('state', state, 'generation', NEW.generation));
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER publish_agent_work_attempt AFTER INSERT OR UPDATE ON rm.agent_work_attempts
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rm.capture_agent_work_event();
CREATE CONSTRAINT TRIGGER publish_agent_work_review AFTER INSERT ON rm.agent_work_reviews
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rm.capture_agent_work_event();

REVOKE ALL ON FUNCTION rm.agent_work_workspace(), rm.agent_work_user(),
    rm.agent_work_projection(uuid,uuid,uuid), rm.agent_work_predecessor_current(uuid,uuid,jsonb),
    rm.list_agent_work(), rm.read_agent_work(uuid), rm.submit_agent_work(uuid,jsonb),
    rm.claim_agent_work(uuid,integer), rm.finish_agent_work(uuid,integer,uuid,text,text),
    rm.retry_agent_work(uuid,uuid,text,integer), rm.review_agent_work(uuid,uuid,text,text,text),
    rm.capture_agent_work_event() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.list_agent_work(), rm.read_agent_work(uuid), rm.submit_agent_work(uuid,jsonb),
    rm.retry_agent_work(uuid,uuid,text,integer), rm.review_agent_work(uuid,uuid,text,text,text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.claim_agent_work(uuid,integer), rm.finish_agent_work(uuid,integer,uuid,text,text)
    TO rm_scheduler;
