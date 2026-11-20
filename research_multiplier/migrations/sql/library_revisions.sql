-- A separately confirmed target can start a new branch of an existing claim's
-- lineage. Ordinary frame revisions still inherit the original frozen target.
CREATE TABLE rm.research_statement_revisions (
    goal_id uuid PRIMARY KEY REFERENCES rm.research_goals(id),
    target_id uuid REFERENCES rm.research_targets(id),
    prior_item_id uuid NOT NULL REFERENCES rm.formal_item_versions(id),
    revision integer NOT NULL CHECK (revision > 1)
);
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.research_statement_revisions
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE CONSTRAINT TRIGGER publish_statement_revision AFTER INSERT ON rm.research_statement_revisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_workspace_change_event('intake', 'goal');

CREATE OR REPLACE FUNCTION rm.research_target_for_goal(goal_id uuid) RETURNS rm.research_targets
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE lineage AS (
        SELECT g.id, g.revision_of, 0 AS depth
        FROM research_goals g WHERE g.id = goal_id
        UNION ALL
        SELECT g.id, g.revision_of, parent.depth + 1
        FROM lineage parent JOIN research_goals g ON g.id = parent.revision_of
    ), revision AS (
        SELECT t.id FROM lineage
        JOIN research_statement_revisions r ON r.goal_id = lineage.id
        JOIN research_targets t ON t.id = r.target_id
        ORDER BY lineage.depth LIMIT 1
    ), original AS (
        SELECT t.id FROM lineage
        JOIN research_intake_confirmations c ON c.goal_id = lineage.id
        JOIN research_intake_drafts d ON d.id = c.intake_id
        JOIN research_targets t ON t.id = (d.body #>> '{target,id}')::uuid
        ORDER BY lineage.depth DESC LIMIT 1
    )
    SELECT t.* FROM research_targets t
    WHERE t.id = coalesce((SELECT id FROM revision), (SELECT id FROM original))
$$;

CREATE FUNCTION rm.library_statement_revisions(item_ids uuid[])
RETURNS TABLE (id uuid, revision integer)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT b.version_id, r.revision FROM packet_basis_items b
    JOIN research_goal_selections selected ON selected.packet_id = b.packet_id
    JOIN research_statement_revisions r ON r.goal_id = selected.goal_id
    WHERE b.version_id = ANY(item_ids) AND b.item_label = 'goal'
$$;

CREATE FUNCTION rm.library_revision_item(base_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT jsonb_build_object(
        'id', i.id, 'version', coalesce(source.revision, i.version),
        'sha256', i.version_sha256, 'kind', i.body ->> 'kind',
        'natural', i.body ->> 'natural',
        'lean', CASE jsonb_typeof(i.body #> '{formal,lean}')
                    WHEN 'object' THEN i.body #>> '{formal,lean,proposition}'
                    WHEN 'string' THEN i.body #>> '{formal,lean}' END,
        'scope', CASE WHEN jsonb_typeof(i.body #> '{formal,lean}') = 'object'
                    THEN (i.body #> '{formal,lean}') - ARRAY['proposition','declaration'] END,
        'mode', mode.name,
        'claim_id', source.claim_id,
        'goal_id', source.id,
        'available', i.version = k.last_version AND NOT i.needs_recheck
            AND NOT EXISTS (SELECT FROM formal_item_retirements r WHERE r.version_id = i.id)
            AND CASE mode.name
                WHEN 'goal' THEN source.basis_current
                    AND source.claim_status NOT IN ('superseded', 'archived')
                    AND NOT EXISTS (
                        SELECT FROM research_goals g JOIN research_goal_selections s ON s.goal_id = g.id
                        WHERE g.revision_of = source.id
                    )
                WHEN 'successor' THEN NOT EXISTS (
                    SELECT FROM formal_item_keys x WHERE x.logical_key = 'library-successor:' || i.id
                )
                ELSE true END
    )
    FROM formal_item_status i
    JOIN formal_item_keys k ON k.logical_key = i.logical_key
    LEFT JOIN LATERAL (
        SELECT g.id, g.claim_id, g.basis_current, g.claim_status, r.revision
        FROM packet_basis_items b JOIN research_goal_status g ON g.packet_id = b.packet_id
        LEFT JOIN research_statement_revisions r ON r.goal_id = g.id
        WHERE b.version_id = i.id AND b.item_label = 'goal' AND i.body ->> 'kind' = 'goal'
        ORDER BY g.id LIMIT 1
    ) source ON true
    CROSS JOIN LATERAL (
        SELECT CASE
            WHEN source.id IS NOT NULL THEN 'goal'
            WHEN i.body ->> 'kind' = 'goal' THEN 'unsupported'
            WHEN i.origin = 'human' THEN 'basis'
            ELSE 'successor'
        END AS name
    ) mode
    WHERE i.id = $1
$$;

CREATE FUNCTION rm.library_revision_preview(base_id uuid, expected_sha text, wording text, proposition text)
RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE base jsonb := library_revision_item(base_id);
DECLARE affected jsonb;
DECLARE preview jsonb;
BEGIN
    IF base IS NULL THEN
        RAISE EXCEPTION 'library item missing' USING ERRCODE = 'P0002';
    END IF;
    IF NOT coalesce((base ->> 'available')::boolean, false)
        OR base ->> 'sha256' IS DISTINCT FROM expected_sha THEN
        RAISE EXCEPTION 'library version is no longer current' USING ERRCODE = '23514';
    END IF;
    IF base ->> 'mode' = 'unsupported' OR base ->> 'lean' IS NULL
        OR nullif(btrim(wording), '') IS NULL OR length(wording) > 4000
        OR nullif(btrim(proposition), '') IS NULL OR length(proposition) > 16000
        OR (wording, proposition) IS NOT DISTINCT FROM (base ->> 'natural', base ->> 'lean') THEN
        RAISE EXCEPTION 'revision requires a changed natural or Lean statement' USING ERRCODE = '22023';
    END IF;

    WITH RECURSIVE dependent(kind, id) AS (
        SELECT 'item'::text, base_id WHERE base ->> 'mode' = 'basis'
        UNION
        SELECT e.child_kind, e.child_id FROM dependent d
        JOIN foundation_dependency_edges e ON (e.parent_kind, e.parent_id) = (d.kind, d.id)
    ), checks AS (
        SELECT id FROM dependent WHERE kind = 'lean'
        UNION SELECT k.id FROM lean_checks k JOIN lean_statements s ON s.id = k.statement_id
        WHERE s.claim_id = (base ->> 'claim_id')::uuid
    ), receipts AS (
        SELECT id FROM dependent WHERE kind = 'evidence'
        UNION SELECT e.id FROM evidence e WHERE e.claim_id = (base ->> 'claim_id')::uuid
    ), impacted_claims AS (
        SELECT s.claim_id FROM checks d JOIN lean_checks k ON k.id = d.id
        JOIN lean_statements s ON s.id = k.statement_id
        UNION SELECT e.claim_id FROM receipts d JOIN evidence e ON e.id = d.id
        UNION SELECT f.claim_id FROM (
            SELECT packet_id FROM packet_basis_items WHERE version_id = base_id
            UNION SELECT packet_id FROM packet_library_items WHERE version_id = base_id
            UNION SELECT packet_id FROM lean_core_imports WHERE version_id = base_id
        ) b JOIN formalization_status f ON f.id = b.packet_id
        WHERE NOT f.superseded AND base ->> 'mode' = 'basis'
    ), revision_state AS (
        SELECT 'check' AS kind, id FROM checks UNION ALL SELECT 'evidence', id FROM receipts
        UNION ALL SELECT 'claim', claim_id FROM impacted_claims
    )
    SELECT jsonb_build_object(
        'checks', (SELECT count(*) FROM checks), 'receipts', (SELECT count(*) FROM receipts),
        'claims', (SELECT count(*) FROM impacted_claims),
        'sha256', encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(revision_state)
                      ORDER BY revision_state.kind, revision_state.id)::text, '[]'), 'UTF8')), 'hex')
    ) INTO affected FROM revision_state;
    preview := jsonb_build_object('base', base, 'after', jsonb_build_object('natural', wording, 'lean', proposition),
                                  'affected', affected);
    RETURN preview || jsonb_build_object('token', encode(sha256(convert_to(preview::text, 'UTF8')), 'hex'));
END $$;

CREATE FUNCTION rm.confirm_library_revision(base_id uuid, expected_sha text, wording text, proposition text,
                                             token text, actor_ref text, reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE item formal_item_versions;
DECLARE replacement formal_item_versions;
DECLARE prior_goal research_goals;
DECLARE candidate research_conjectures;
DECLARE goal research_goals;
DECLARE chosen research_goal_selections;
DECLARE mode text;
DECLARE preview jsonb;
DECLARE body jsonb;
DECLARE target jsonb;
DECLARE target_id uuid;
DECLARE prior_id uuid;
DECLARE version integer;
DECLARE key text;
BEGIN
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL OR length(reason) > 2000 THEN
        RAISE EXCEPTION 'revision needs human confirmation and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT item FROM formal_item_versions i WHERE i.id = base_id;
    -- The key lock also serializes edits against proposals from the CLI. The
    -- foundation lock serializes confirmation against proof publication.
    PERFORM advance_foundation();
    PERFORM FROM formal_item_keys k WHERE k.logical_key = item.logical_key FOR UPDATE;
    prior_id := (library_revision_item(base_id) ->> 'goal_id')::uuid;
    PERFORM FROM research_goals g WHERE g.id = prior_id FOR UPDATE;
    preview := library_revision_preview(base_id, expected_sha, wording, proposition);
    IF preview ->> 'token' IS DISTINCT FROM token THEN
        RAISE EXCEPTION 'review the current statement revision and dependencies' USING ERRCODE = '23514';
    END IF;
    mode := preview #>> '{base,mode}';
    body := item.body || jsonb_build_object('natural', wording, 'revision_of', item.id);
    IF proposition IS DISTINCT FROM preview #>> '{base,lean}' THEN
        -- Display-specific alternate forms refer to the old proposition.
        body := body #- '{formal,latex}' #- '{formal,formula}';
    END IF;
    IF jsonb_typeof(body #> '{formal,lean}') = 'object' THEN
        body := jsonb_set(body, '{formal,lean,proposition}', to_jsonb(proposition));
    ELSE
        body := jsonb_set(body, '{formal,lean}', to_jsonb(proposition));
    END IF;

    IF mode = 'goal' THEN
        SELECT * INTO STRICT prior_goal FROM research_goals g WHERE g.id = prior_id;
        SELECT c.* INTO STRICT candidate FROM research_goal_selections s
        JOIN research_conjectures c ON c.id = s.conjecture_id WHERE s.goal_id = prior_goal.id;
        body := candidate.body || jsonb_build_object('statement', wording, 'formal', body -> 'formal',
            'provenance', jsonb_build_object('kind', 'human', 'revised_from', item.id));
        goal := create_research_goal(prior_goal.body || jsonb_build_object('objective', wording), prior_goal.id);
        IF (research_target_for_goal(prior_goal.id)).id IS NOT NULL THEN
            target := preview_research_target(
                jsonb_build_object('title', prior_goal.body -> 'title', 'domain', prior_goal.body -> 'domain',
                                   'sensitivity', prior_goal.body -> 'sensitivity') || (body - 'provenance')
            );
            SELECT t.id INTO target_id FROM research_targets t WHERE t.target_sha256 = target ->> 'target_sha256';
            IF NOT FOUND THEN
                target_id := freeze_research_target(target -> 'body', target ->> 'target_sha256', actor_ref, reason);
            END IF;
        END IF;
        version := (preview #>> '{base,version}')::integer + 1;
        INSERT INTO research_statement_revisions VALUES (goal.id, target_id, item.id, version);
        PERFORM confirm_research_goal(goal.id, goal.frame_sha256, actor_ref, reason);
        candidate := propose_research_conjecture(goal.id, body);
        chosen := select_research_conjecture(candidate.id, candidate.conjecture_sha256, actor_ref, reason);
        SELECT v.* INTO STRICT replacement FROM packet_basis_items b
        JOIN formal_item_versions v ON v.id = b.version_id WHERE b.packet_id = chosen.packet_id AND b.item_label = 'goal';
    ELSE
        key := item.logical_key;
        IF mode = 'successor' THEN
            key := 'library-successor:' || item.id;
            -- Preserve the source's original conclusion and evidence. A reviewed
            -- successor is a human entry with no inherited proof or authority.
            body := jsonb_strip_nulls(jsonb_build_object(
                'kind', item.body -> 'kind', 'title', item.body -> 'title', 'natural', wording,
                'formal', (body -> 'formal') #- '{lean,declaration}',
                'source', jsonb_build_object('kind', 'human_revision', 'version_id', item.id)
            ));
        END IF;
        replacement := propose_formal_item(key, body);
        IF replacement.version <= item.version AND mode = 'basis' THEN
            RAISE EXCEPTION 'an identical prior version cannot be reused as a new revision' USING ERRCODE = '23514';
        END IF;
        PERFORM approve_formal_item(replacement.id, replacement.version_sha256, actor_ref, reason);
        IF mode = 'basis' THEN
            PERFORM retire_formal_item(item.id, 'Superseded by approved version ' || replacement.version);
        END IF;
        version := replacement.version;
    END IF;
    RETURN jsonb_build_object('id', replacement.id, 'version', version, 'claim_id', chosen.claim_id);
END $$;

REVOKE ALL ON FUNCTION rm.library_statement_revisions(uuid[]), rm.library_revision_item(uuid), rm.library_revision_preview(uuid,text,text,text),
    rm.confirm_library_revision(uuid,text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.library_statement_revisions(uuid[]), rm.library_revision_item(uuid), rm.library_revision_preview(uuid,text,text,text),
    rm.confirm_library_revision(uuid,text,text,text,text,text,text) TO rm_human;
