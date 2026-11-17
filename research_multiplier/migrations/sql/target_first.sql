CREATE TABLE rm.research_targets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    target_sha256 rm.sha256_hex NOT NULL UNIQUE,
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    frozen_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.research_target_bindings (
    target_id uuid PRIMARY KEY REFERENCES rm.research_targets(id),
    intake_id uuid NOT NULL UNIQUE REFERENCES rm.research_intake_confirmations(intake_id),
    goal_id uuid NOT NULL UNIQUE REFERENCES rm.research_goal_selections(goal_id),
    claim_id uuid NOT NULL UNIQUE REFERENCES rm.research_goal_selections(claim_id)
);

CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.research_targets
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.research_target_bindings
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE VIEW rm.research_target_status WITH (security_barrier = true) AS
    SELECT t.*, b.intake_id, b.goal_id, b.claim_id, s.packet_id, s.conjecture_id
    FROM rm.research_targets t
    LEFT JOIN rm.research_target_bindings b ON b.target_id = t.id
    LEFT JOIN rm.research_goal_selections s ON s.goal_id = b.goal_id;

CREATE FUNCTION rm.preview_research_target(body jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['title','domain','sensitivity','statement','formal',
                        'quantifiers','parameters','constant_dependencies'] <> '{}'::jsonb
        OR jsonb_typeof(body -> 'title') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'title'), '') IS NULL OR length(body ->> 'title') > 300
        OR jsonb_typeof(body -> 'domain') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'domain'), '') IS NULL OR length(body ->> 'domain') > 100
        OR coalesce(body ->> 'sensitivity', '') NOT IN ('public','restricted')
        OR jsonb_typeof(body -> 'statement') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'statement'), '') IS NULL OR length(body ->> 'statement') > 4000
        OR jsonb_typeof(body -> 'formal') IS DISTINCT FROM 'object' OR body -> 'formal' = '{}'::jsonb
        OR (body ->> 'domain' = 'lean-core' AND (
            jsonb_typeof(body #> '{formal,lean,proposition}') IS DISTINCT FROM 'string'
            OR nullif(btrim(body #>> '{formal,lean,proposition}'), '') IS NULL
            OR length(body #>> '{formal,lean,proposition}') > 16000
            OR (body -> 'formal') - 'lean' <> '{}'::jsonb
            OR (body #> '{formal,lean}') - 'proposition' <> '{}'::jsonb))
        OR (body ? 'quantifiers' AND jsonb_typeof(body -> 'quantifiers') IS DISTINCT FROM 'array')
        OR (body ? 'parameters' AND jsonb_typeof(body -> 'parameters') IS DISTINCT FROM 'array')
        OR (body ? 'constant_dependencies' AND jsonb_typeof(body -> 'constant_dependencies') IS DISTINCT FROM 'object')
        OR octet_length(body::text) > 65000 THEN
        RAISE EXCEPTION 'preview the exact natural and formal target with its scope'
            USING ERRCODE = '22023';
    END IF;
    body := jsonb_build_object('quantifiers','[]'::jsonb,'parameters','[]'::jsonb,
                               'constant_dependencies','{}'::jsonb) || body;
    RETURN jsonb_build_object('body',body,'target_sha256',encode(sha256(convert_to(body::text, 'UTF8')), 'hex'));
END $$;

CREATE FUNCTION rm.freeze_research_target(body jsonb, expected_sha text, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE preview jsonb := preview_research_target(body);
DECLARE checksum text := preview ->> 'target_sha256';
DECLARE saved research_targets;
BEGIN
    IF checksum IS DISTINCT FROM expected_sha
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'freeze the reviewed target hash with its researcher and reason' USING ERRCODE = '23514';
    END IF;
    body := preview -> 'body';
    INSERT INTO research_targets (body, target_sha256, actor_ref, reason) VALUES (body, checksum, actor_ref, reason)
        ON CONFLICT (target_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_targets t WHERE t.target_sha256 = checksum;
        IF ROW(saved.actor_ref, saved.authenticated_role, saved.reason) IS DISTINCT FROM
            ROW(actor_ref, session_user, reason) THEN
            RAISE EXCEPTION 'this exact target already has a different freeze record' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN saved.id;
END $$;

CREATE FUNCTION rm.target_as_basis(target jsonb, candidate jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
    SELECT coalesce(
        btrim(target ->> 'statement') = btrim(candidate ->> 'natural')
        OR target -> 'formal' = candidate -> 'formal'
        OR btrim(target #>> '{formal,lean,proposition}') = btrim(candidate #>> '{formal,lean,proposition}'),
        false)
$$;

CREATE FUNCTION rm.research_target_for_goal(goal_id uuid) RETURNS rm.research_targets
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE lineage AS (
        SELECT g.id, g.revision_of, 0 AS depth
        FROM research_goals g WHERE g.id = goal_id
        UNION ALL
        SELECT g.id, g.revision_of, parent.depth + 1
        FROM lineage parent JOIN research_goals g ON g.id = parent.revision_of
    )
    SELECT target.* FROM lineage
    JOIN research_intake_confirmations confirmation ON confirmation.goal_id = lineage.id
    JOIN research_intake_drafts draft ON draft.id = confirmation.intake_id
    JOIN research_targets target ON target.id = (draft.body #>> '{target,id}')::uuid
    ORDER BY lineage.depth DESC LIMIT 1
$$;

CREATE FUNCTION rm.guard_target_conjecture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_targets := research_target_for_goal(NEW.goal_id);
DECLARE frame research_goals;
BEGIN
    IF target.id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO STRICT frame FROM research_goals g WHERE g.id = NEW.goal_id;
    IF jsonb_build_object('quantifiers','[]'::jsonb,'parameters','[]'::jsonb,'constant_dependencies','{}'::jsonb)
        || (NEW.body - 'provenance') IS DISTINCT FROM target.body - ARRAY['title','domain','sensitivity']
        OR NEW.body #>> '{provenance,kind}' IS DISTINCT FROM 'human'
        OR frame.body -> 'domain' IS DISTINCT FROM target.body -> 'domain'
        OR frame.body -> 'sensitivity' IS DISTINCT FROM target.body -> 'sensitivity' THEN
        RAISE EXCEPTION 'keep the exact frozen target; freeze a separate target to change its statement or scope'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements_text(frame.body -> 'basis_version_ids') item(id)
        JOIN formal_item_versions version ON version.id = item.id::uuid
        WHERE target_as_basis(target.body, version.body)
    ) THEN
        RAISE EXCEPTION 'the frozen target cannot be used as a starting assumption, definition or theorem'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER frozen_target BEFORE INSERT ON rm.research_conjectures
    FOR EACH ROW EXECUTE FUNCTION rm.guard_target_conjecture();

CREATE FUNCTION rm.guard_target_library_item() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE selected_goal uuid;
BEGIN
    SELECT selection.goal_id INTO selected_goal FROM research_goal_selections selection WHERE selection.packet_id = NEW.packet_id;
    IF EXISTS (
        SELECT FROM research_target_for_goal(selected_goal) target
        JOIN formal_item_versions version ON version.id = NEW.version_id
        WHERE target_as_basis(target.body, version.body)
    ) THEN
        RAISE EXCEPTION 'the frozen target cannot be attached as a proof input' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER frozen_target BEFORE INSERT ON rm.packet_library_items
    FOR EACH ROW EXECUTE FUNCTION rm.guard_target_library_item();
CREATE TRIGGER frozen_target BEFORE INSERT ON rm.lean_core_imports
    FOR EACH ROW EXECUTE FUNCTION rm.guard_target_library_item();

CREATE OR REPLACE FUNCTION rm.draft_research_intake(body jsonb) RETURNS rm.research_intake_drafts
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE compared jsonb;
DECLARE checksum text;
DECLARE saved research_intake_drafts;
DECLARE target research_targets;
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['format_version','title','domain','objective','sensitivity','notes','model','items','target'] <> '{}'::jsonb
        OR body -> 'format_version' IS DISTINCT FROM '1'::jsonb
        OR jsonb_typeof(body -> 'title') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'title'), '') IS NULL OR length(body ->> 'title') > 300
        OR jsonb_typeof(body -> 'domain') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'domain'), '') IS NULL OR length(body ->> 'domain') > 100
        OR jsonb_typeof(body -> 'objective') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'objective'), '') IS NULL OR length(body ->> 'objective') > 4000
        OR coalesce(body ->> 'sensitivity', '') NOT IN ('public','restricted')
        OR NOT lean_artifact_ref_valid(body -> 'notes')
        OR (body ? 'model' AND (body ->> 'sensitivity' <> 'public'
            OR jsonb_typeof(body -> 'model') IS DISTINCT FROM 'object'
            OR NOT lean_artifact_ref_valid(body #> '{model,transcript}')))
        OR jsonb_typeof(body -> 'items') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'intake needs a scoped objective, notes archive and review items' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(body -> 'items') > 50 OR EXISTS (
        SELECT FROM jsonb_array_elements(body -> 'items') item
        WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
           OR item - ARRAY['id','kind','natural','formal','question','origin'] <> '{}'::jsonb
           OR NOT coalesce(item ->> 'id' ~ '^i[1-9][0-9]?$' , false)
           OR coalesce(item ->> 'kind','') NOT IN ('definition','hypothesis','theorem')
           OR coalesce(item ->> 'origin','') NOT IN ('notes','model')
           OR (item ->> 'origin' = 'model' AND NOT body ? 'model')
           OR jsonb_typeof(item -> 'natural') IS DISTINCT FROM 'string'
           OR nullif(btrim(item ->> 'natural'), '') IS NULL OR length(item ->> 'natural') > 4000
           OR jsonb_typeof(item -> 'formal') NOT IN ('object','null')
           OR NOT item ? 'formal'
           OR (item -> 'formal' <> 'null'::jsonb AND
               (item -> 'formal' = '{}'::jsonb OR pg_column_size(item -> 'formal') > 12000))
           OR (item ? 'question' AND (jsonb_typeof(item -> 'question') IS DISTINCT FROM 'string'
               OR nullif(btrim(item ->> 'question'), '') IS NULL OR length(item ->> 'question') > 2000))
    ) OR EXISTS (
        SELECT FROM jsonb_array_elements(body -> 'items') item GROUP BY item ->> 'id' HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'invalid intake review items' USING ERRCODE = '22023';
    END IF;
    IF body ? 'target' THEN
        IF jsonb_typeof(body -> 'target') IS DISTINCT FROM 'object'
            OR (body -> 'target') - ARRAY['id','sha256'] <> '{}'::jsonb
            OR NOT coalesce(body #>> '{target,id}' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$', false) THEN
            RAISE EXCEPTION 'select the exact frozen target for this intake' USING ERRCODE = '22023';
        END IF;
        SELECT * INTO target FROM research_targets t
        WHERE t.id = (draft_research_intake.body #>> '{target,id}')::uuid
          AND t.target_sha256 = draft_research_intake.body #>> '{target,sha256}';
        IF NOT FOUND OR body -> 'title' IS DISTINCT FROM target.body -> 'title'
            OR body -> 'domain' IS DISTINCT FROM target.body -> 'domain'
            OR body -> 'sensitivity' IS DISTINCT FROM target.body -> 'sensitivity' THEN
            RAISE EXCEPTION 'intake requires the exact frozen target and its scope' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT FROM research_target_bindings b WHERE b.target_id = target.id) THEN
            RAISE EXCEPTION 'this target already has a confirmed starting basis' USING ERRCODE = '23514';
        END IF;
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
        'id', item ->> 'id', 'match_version_id', matched,
        'question', item ->> 'question',
        'status', CASE WHEN target_as_basis(target.body, item) THEN 'omit_frozen_target'
                       WHEN item ? 'question' THEN 'clarify'
                       WHEN item ->> 'origin' = 'model' THEN 'confirm_scope'
                       WHEN matched IS NOT NULL THEN 'reuse'
                       WHEN item -> 'formal' = 'null'::jsonb THEN 'formalization_required'
                       ELSE 'confirm_meaning' END
    )) ORDER BY position), '[]'::jsonb)
    INTO compared
    FROM jsonb_array_elements(body -> 'items') WITH ORDINALITY AS entry(item, position)
    CROSS JOIN LATERAL (SELECT intake_exact_match(item) AS matched) exact;
    checksum := encode(sha256(convert_to(jsonb_build_object('body', body, 'comparison', compared)::text, 'UTF8')), 'hex');
    INSERT INTO research_intake_drafts (body, comparison, intake_sha256)
        VALUES (body, compared, checksum) ON CONFLICT (intake_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_intake_drafts WHERE intake_sha256 = checksum;
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.bind_intake_target() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_targets;
DECLARE draft research_intake_drafts;
DECLARE candidate research_conjectures;
DECLARE selection research_goal_selections;
BEGIN
    SELECT * INTO STRICT draft FROM research_intake_drafts d WHERE d.id = NEW.intake_id;
    IF NOT draft.body ? 'target' THEN
        RETURN NEW;
    END IF;
    SELECT * INTO STRICT target FROM research_targets t WHERE t.id = (draft.body #>> '{target,id}')::uuid FOR UPDATE;
    IF EXISTS (SELECT FROM research_target_bindings b WHERE b.target_id = target.id) THEN
        RAISE EXCEPTION 'this target already has a confirmed starting basis' USING ERRCODE = '23514';
    END IF;
    candidate := propose_research_conjecture(NEW.goal_id,
        (target.body - ARRAY['title','domain','sensitivity']) ||
        jsonb_build_object('provenance', jsonb_build_object('kind','human','target_id',target.id,
                                                          'target_sha256',target.target_sha256)));
    selection := select_research_conjecture(candidate.id, candidate.conjecture_sha256, NEW.actor_ref, NEW.reason);
    INSERT INTO research_target_bindings (target_id, intake_id, goal_id, claim_id)
        VALUES (target.id, NEW.intake_id, NEW.goal_id, selection.claim_id);
    RETURN NEW;
END $$;

CREATE TRIGGER bind_target AFTER INSERT ON rm.research_intake_confirmations
    FOR EACH ROW EXECUTE FUNCTION rm.bind_intake_target();

CREATE OR REPLACE VIEW rm.research_review_inbox WITH (security_barrier = true) AS
    SELECT * FROM rm.research_review_inbox_before_intake
    UNION ALL
    SELECT 'confirm_intake', intake.id, NULL::uuid, intake.recorded_at,
        'Do the outstanding items and reused versions match the starting research scope?',
        jsonb_build_object('title', intake.body ->> 'title', 'sha256', intake.intake_sha256,
            'target', intake.body -> 'target',
            'outstanding', (SELECT count(*) FROM jsonb_array_elements(intake.comparison) review
                            WHERE review ->> 'status' <> 'reuse'))
    FROM rm.research_intake_status intake WHERE intake.goal_id IS NULL
    AND NOT EXISTS (
        SELECT FROM rm.research_target_bindings b WHERE b.target_id = (intake.body #>> '{target,id}')::uuid
    );

REVOKE EXECUTE ON FUNCTION rm.preview_research_target(jsonb), rm.freeze_research_target(jsonb,text,text,text),
    rm.target_as_basis(jsonb,jsonb),
    rm.research_target_for_goal(uuid), rm.guard_target_conjecture(), rm.guard_target_library_item(),
    rm.bind_intake_target() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.preview_research_target(jsonb), rm.freeze_research_target(jsonb,text,text,text) TO rm_human;
GRANT SELECT ON rm.research_target_status TO rm_human;
