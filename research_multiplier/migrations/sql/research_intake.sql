CREATE TABLE rm.research_intake_drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    comparison jsonb NOT NULL CHECK (jsonb_typeof(comparison) = 'array'),
    intake_sha256 rm.sha256_hex NOT NULL UNIQUE,
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.research_intake_confirmations (
    intake_id uuid PRIMARY KEY REFERENCES rm.research_intake_drafts(id),
    goal_id uuid NOT NULL UNIQUE REFERENCES rm.research_goals(id),
    decisions jsonb NOT NULL CHECK (jsonb_typeof(decisions) = 'object'),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE VIEW rm.research_intake_status WITH (security_barrier = true) AS
    SELECT draft.*, confirmation.goal_id, confirmation.decisions,
           confirmation.actor_ref, confirmation.reason, confirmation.confirmed_at
    FROM rm.research_intake_drafts draft
    LEFT JOIN rm.research_intake_confirmations confirmation ON confirmation.intake_id = draft.id;

ALTER VIEW rm.research_review_inbox RENAME TO research_review_inbox_before_intake;
CREATE VIEW rm.research_review_inbox WITH (security_barrier = true) AS
    SELECT * FROM rm.research_review_inbox_before_intake
    UNION ALL
    SELECT 'confirm_intake', intake.id, NULL::uuid, intake.recorded_at,
        'Do the outstanding items and reused versions match the starting research scope?',
        jsonb_build_object('title', intake.body ->> 'title', 'sha256', intake.intake_sha256,
            'outstanding', (SELECT count(*) FROM jsonb_array_elements(intake.comparison) review
                            WHERE review ->> 'status' <> 'reuse'))
    FROM rm.research_intake_status intake WHERE intake.goal_id IS NULL;
GRANT SELECT ON rm.research_review_inbox TO rm_human;

-- Matching anchored JSON and wording permits reuse. Wording similarity alone
-- cannot establish equality or justify admitting a statement.
CREATE FUNCTION rm.intake_exact_match(item jsonb) RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT stored.id
    FROM formal_item_status stored
    JOIN formal_item_approvals approval ON approval.version_id = stored.id
    WHERE NOT stored.needs_recheck
      AND stored.body ->> 'kind' = item ->> 'kind'
      AND stored.body -> 'natural' = item -> 'natural'
      AND stored.body -> 'formal' = item -> 'formal'
    ORDER BY approval.approved_at DESC, stored.id
    LIMIT 1
$$;

CREATE FUNCTION rm.draft_research_intake(body jsonb) RETURNS rm.research_intake_drafts
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE compared jsonb;
DECLARE checksum text;
DECLARE saved research_intake_drafts;
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['format_version','title','domain','objective','sensitivity','notes','model','items'] <> '{}'::jsonb
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
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
        'id', item ->> 'id', 'match_version_id', matched,
        'question', item ->> 'question',
        'status', CASE WHEN item ? 'question' THEN 'clarify'
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

CREATE FUNCTION rm.confirm_research_intake(intake_id uuid, expected_sha text, decisions jsonb,
                                            actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE draft research_intake_drafts;
DECLARE prior research_intake_confirmations;
DECLARE item jsonb;
DECLARE comparison jsonb;
DECLARE decision jsonb;
DECLARE basis jsonb := '[]'::jsonb;
DECLARE proposal jsonb;
DECLARE selected uuid;
DECLARE new_item formal_item_versions;
DECLARE goal research_goals;
DECLARE logical_key text;
BEGIN
    SELECT * INTO STRICT draft FROM research_intake_drafts d WHERE d.id = intake_id FOR UPDATE;
    IF draft.intake_sha256 IS DISTINCT FROM expected_sha OR jsonb_typeof(decisions) IS DISTINCT FROM 'object'
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'confirm the exact intake and provide the outstanding decisions, actor and reason'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO prior FROM research_intake_confirmations c
        WHERE c.intake_id = confirm_research_intake.intake_id;
    IF FOUND THEN
        IF ROW(prior.decisions, prior.actor_ref, prior.authenticated_role, prior.reason)
            IS DISTINCT FROM ROW(decisions, actor_ref, session_user, reason) THEN
            RAISE EXCEPTION 'the intake already has a different confirmation' USING ERRCODE = '23514';
        END IF;
        RETURN prior.goal_id;
    END IF;
    IF EXISTS (SELECT FROM jsonb_object_keys(decisions) AS choice(id)
               WHERE NOT EXISTS (SELECT FROM jsonb_array_elements(draft.comparison) AS pending
                                 WHERE pending ->> 'id' = choice.id AND pending ->> 'status' <> 'reuse')) THEN
        RAISE EXCEPTION 'decisions must identify outstanding items in the review' USING ERRCODE = '22023';
    END IF;
    -- Finish the goal and its individually approved new versions in the same transaction.
    PERFORM advance_foundation();
    FOR item, comparison IN
        SELECT notes.value, compared.value FROM jsonb_array_elements(draft.body -> 'items') WITH ORDINALITY notes
        JOIN jsonb_array_elements(draft.comparison) WITH ORDINALITY compared USING (ordinality)
        ORDER BY notes.ordinality
    LOOP
        selected := (comparison ->> 'match_version_id')::uuid;
        IF comparison ->> 'status' <> 'reuse' THEN
            decision := decisions -> (item ->> 'id');
            IF jsonb_typeof(decision) IS DISTINCT FROM 'object'
                OR decision - ARRAY['action','formal','reason'] <> '{}'::jsonb
                OR coalesce(decision ->> 'action', '') NOT IN ('accept','omit')
                OR nullif(btrim(decision ->> 'reason'), '') IS NULL
                OR jsonb_typeof(decision -> 'reason') IS DISTINCT FROM 'string'
                OR length(decision ->> 'reason') > 2000 THEN
                RAISE EXCEPTION 'item % needs an explicit decision and reason', item ->> 'id'
                    USING ERRCODE = '23514';
            END IF;
            IF decision ->> 'action' = 'omit' THEN
                IF decision ? 'formal' THEN
                    RAISE EXCEPTION 'an omitted item cannot carry an approval' USING ERRCODE = '22023';
                END IF;
                CONTINUE;
            END IF;
            IF item -> 'formal' = 'null'::jsonb THEN
                IF jsonb_typeof(decision -> 'formal') IS DISTINCT FROM 'object'
                    OR decision -> 'formal' = '{}'::jsonb OR pg_column_size(decision -> 'formal') > 12000 THEN
                    RAISE EXCEPTION 'item % needs an exact structured formal statement', item ->> 'id'
                        USING ERRCODE = '23514';
                END IF;
                item := jsonb_set(item, '{formal}', decision -> 'formal');
            ELSIF decision ? 'formal' AND decision -> 'formal' IS DISTINCT FROM item -> 'formal' THEN
                RAISE EXCEPTION 're-draft item % before confirming changed meaning', item ->> 'id'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        IF selected IS NOT NULL THEN
            IF NOT EXISTS (SELECT FROM formal_item_status v JOIN formal_item_approvals a ON a.version_id = v.id
                           WHERE v.id = selected AND NOT v.needs_recheck
                               AND v.body ->> 'kind' = item ->> 'kind'
                               AND v.body -> 'natural' = item -> 'natural'
                               AND v.body -> 'formal' = item -> 'formal') THEN
                RAISE EXCEPTION 'the approved version for item % changed; refresh the draft', item ->> 'id'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            selected := intake_exact_match(item);
            IF selected IS NULL THEN
                proposal := jsonb_build_object('kind', item -> 'kind', 'natural', item -> 'natural',
                    'formal', item -> 'formal');
                logical_key := 'intake:' || encode(sha256(convert_to(proposal::text, 'UTF8')), 'hex');
                new_item := propose_formal_item(logical_key, proposal ||
                    jsonb_build_object('source', jsonb_build_object('kind','research_intake',
                        'id',draft.id,'item',item ->> 'id','sha256',draft.body #>> '{notes,sha256}')));
                selected := approve_formal_item(new_item.id, new_item.version_sha256,
                                                actor_ref, decision ->> 'reason');
            END IF;
        END IF;
        IF NOT basis ? selected::text THEN
            basis := basis || jsonb_build_array(selected);
        END IF;
    END LOOP;
    goal := create_research_goal(jsonb_build_object('title', draft.body -> 'title', 'domain', draft.body -> 'domain',
        'objective', draft.body -> 'objective', 'sensitivity', draft.body -> 'sensitivity',
        'basis_version_ids', basis, 'intake_id', draft.id));
    PERFORM confirm_research_goal(goal.id, goal.frame_sha256, actor_ref, reason);
    INSERT INTO research_intake_confirmations (intake_id, goal_id, decisions, actor_ref, reason)
        VALUES (draft.id, goal.id, decisions, actor_ref, reason);
    RETURN goal.id;
END $$;

CREATE TABLE rm.literature_searches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL,
    contract_sha256 rm.sha256_hex NOT NULL,
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES rm.claims(id, contract_sha256)
);
CREATE INDEX literature_search_claim ON rm.literature_searches(claim_id, recorded_at DESC);

CREATE TABLE rm.literature_search_hits (
    search_id uuid NOT NULL REFERENCES rm.literature_searches(id),
    rank integer NOT NULL CHECK (rank >= 1),
    identifier text NOT NULL CHECK (identifier ~ '^doi:10[.][0-9]{4,9}/[^[:space:][:cntrl:]]+$'),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    PRIMARY KEY (search_id, rank),
    UNIQUE (search_id, identifier)
);

CREATE TABLE rm.literature_search_links (
    search_id uuid NOT NULL,
    rank integer NOT NULL,
    source_id uuid NOT NULL REFERENCES rm.literature_sources(id),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (search_id, rank, source_id),
    FOREIGN KEY (search_id, rank) REFERENCES rm.literature_search_hits(search_id, rank)
);

CREATE FUNCTION rm.record_literature_search(claim_id uuid, expected_sha text, body jsonb, hits jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved uuid;
BEGIN
    IF NOT EXISTS (SELECT FROM claims c WHERE c.id = record_literature_search.claim_id
        AND c.contract_sha256 = expected_sha AND c.sensitivity = 'public' AND c.frozen_at IS NOT NULL) THEN
        RAISE EXCEPTION 'online literature search requires the exact frozen public claim' USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['provider','query','parameters','status','response','error','total_results'] <> '{}'::jsonb
        OR body ->> 'provider' IS DISTINCT FROM 'crossref'
        OR jsonb_typeof(body -> 'query') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'query'), '') IS NULL OR length(body ->> 'query') > 300
        OR jsonb_typeof(body -> 'parameters') IS DISTINCT FROM 'object'
        OR pg_column_size(body -> 'parameters') > 2000
        OR coalesce(body ->> 'status', '') NOT IN ('complete','provider_error')
        OR (body ->> 'status' = 'complete' AND (NOT lean_artifact_ref_valid(body -> 'response') OR body ? 'error'))
        OR (body ? 'response' AND NOT lean_artifact_ref_valid(body -> 'response'))
        OR (body ->> 'status' = 'provider_error'
            AND (jsonb_typeof(body -> 'error') IS DISTINCT FROM 'string' OR length(body ->> 'error') > 500))
        OR jsonb_typeof(hits) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'literature search needs scoped query and response provenance' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(hits) > 50 OR (body ->> 'status' = 'provider_error' AND jsonb_array_length(hits) <> 0)
        OR EXISTS (SELECT FROM jsonb_array_elements(hits) item WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
            OR NOT coalesce(item ->> 'identifier' ~ '^doi:10[.][0-9]{4,9}/[^[:space:][:cntrl:]]+$', false)
            OR pg_column_size(item) > 8000) THEN
        RAISE EXCEPTION 'invalid normalized literature hits' USING ERRCODE = '22023';
    END IF;
    INSERT INTO literature_searches (claim_id, contract_sha256, body) VALUES (claim_id, expected_sha, body)
        RETURNING id INTO saved;
    INSERT INTO literature_search_hits (search_id, rank, identifier, body)
        SELECT saved, rank::integer, item ->> 'identifier', item
        FROM jsonb_array_elements(hits) WITH ORDINALITY h(item, rank);
    RETURN saved;
END $$;

CREATE FUNCTION rm.link_literature_search_hit(search_id uuid, rank integer, source_id uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM literature_search_hits hit JOIN literature_searches search ON search.id = hit.search_id
        JOIN literature_source_status source ON source.claim_id = search.claim_id
            AND source.body ->> 'identifier' = hit.identifier
        WHERE hit.search_id = link_literature_search_hit.search_id AND hit.rank = link_literature_search_hit.rank
            AND source.id = link_literature_search_hit.source_id AND NOT source.retired
    ) THEN
        RAISE EXCEPTION 'link the corresponding original for the same claim and exact identifier'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO literature_search_links (search_id, rank, source_id) VALUES ($1, $2, $3)
        ON CONFLICT DO NOTHING;
    RETURN source_id;
END $$;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['research_intake_drafts', 'research_intake_confirmations',
        'literature_searches', 'literature_search_hits', 'literature_search_links']
    LOOP
        EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.%I
                        FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation()', table_name);
    END LOOP;
END $$;
REVOKE EXECUTE ON FUNCTION rm.intake_exact_match(jsonb), rm.draft_research_intake(jsonb),
    rm.confirm_research_intake(uuid, text, jsonb, text, text),
    rm.record_literature_search(uuid, text, jsonb, jsonb), rm.link_literature_search_hit(uuid, integer, uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.draft_research_intake(jsonb),
    rm.confirm_research_intake(uuid, text, jsonb, text, text),
    rm.record_literature_search(uuid, text, jsonb, jsonb), rm.link_literature_search_hit(uuid, integer, uuid)
    TO rm_human;
GRANT SELECT ON rm.research_intake_status, rm.literature_searches, rm.literature_search_hits,
    rm.literature_search_links TO rm_human;
