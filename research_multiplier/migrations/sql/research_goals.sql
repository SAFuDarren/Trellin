CREATE TABLE rm.research_goals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    revision_of uuid REFERENCES rm.research_goals(id),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    frame_sha256 rm.sha256_hex NOT NULL,
    proposed_by name NOT NULL DEFAULT session_user,
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX research_goals_revision ON rm.research_goals(revision_of);

CREATE TABLE rm.research_goal_approvals (
    goal_id uuid PRIMARY KEY REFERENCES rm.research_goals(id),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.research_conjectures (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id uuid NOT NULL REFERENCES rm.research_goals(id),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    conjecture_sha256 rm.sha256_hex NOT NULL,
    proposed_by name NOT NULL DEFAULT session_user,
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT conjecture_goal_digest UNIQUE (goal_id, conjecture_sha256)
);

CREATE TABLE rm.research_goal_selections (
    goal_id uuid PRIMARY KEY REFERENCES rm.research_goals(id),
    conjecture_id uuid NOT NULL UNIQUE REFERENCES rm.research_conjectures(id),
    packet_id uuid NOT NULL UNIQUE REFERENCES rm.formalization_packets(id),
    claim_id uuid NOT NULL UNIQUE REFERENCES rm.claims(id),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    selected_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.research_goal_basis_bindings (
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    item_label text NOT NULL CHECK (btrim(item_label) <> ''),
    version_id uuid NOT NULL REFERENCES rm.formal_item_versions(id),
    PRIMARY KEY (packet_id, item_label),
    UNIQUE (packet_id, version_id)
);
CREATE INDEX research_goal_basis_version ON rm.research_goal_basis_bindings(version_id);

CREATE TABLE rm.research_condition_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id uuid NOT NULL REFERENCES rm.research_goals(id),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    request_sha256 rm.sha256_hex NOT NULL,
    clarification_id uuid UNIQUE REFERENCES rm.clarification_requests(id),
    requested_by name NOT NULL DEFAULT session_user,
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT research_condition_goal_digest UNIQUE (goal_id, request_sha256)
);

CREATE TABLE rm.research_condition_decisions (
    request_id uuid PRIMARY KEY REFERENCES rm.research_condition_requests(id),
    action text NOT NULL CHECK (action IN ('reaffirm', 'reject', 'revise', 'derived')),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    version_id uuid REFERENCES rm.formal_item_versions(id),
    evidence_id uuid REFERENCES rm.evidence(id),
    revised_goal_id uuid REFERENCES rm.research_goals(id),
    authenticated_role name NOT NULL DEFAULT session_user,
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((action = 'revise') = (revised_goal_id IS NOT NULL)),
    CHECK ((action IN ('reaffirm', 'revise')) = (version_id IS NOT NULL)),
    CHECK ((action = 'derived') = (evidence_id IS NOT NULL))
);

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['research_goals', 'research_goal_approvals', 'research_conjectures',
            'research_goal_selections', 'research_goal_basis_bindings', 'research_condition_requests',
            'research_condition_decisions'] LOOP
        EXECUTE format('CREATE TRIGGER research_goal_append_only BEFORE UPDATE OR DELETE ON rm.%I '
                       'FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation()', table_name);
    END LOOP;
END $$;

CREATE FUNCTION rm.require_research_goal_basis(versions jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF jsonb_typeof(versions) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'research basis must be an ordered list of approved item version IDs'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(versions) v WHERE jsonb_typeof(v) <> 'string')
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(versions) v GROUP BY v HAVING count(*) > 1)
        OR EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(versions) v(version_id)
            WHERE NOT EXISTS (
                SELECT 1 FROM formal_item_status s JOIN formal_item_approvals a ON a.version_id = s.id
                WHERE s.id::text = v.version_id AND NOT s.needs_recheck
                  AND s.body ->> 'kind' IN ('definition', 'hypothesis', 'theorem')
            )
        ) THEN
        RAISE EXCEPTION 'the research basis contains a duplicate, unapproved, retired, or inapplicable version'
            USING ERRCODE = '23514';
    END IF;
END $$;

CREATE FUNCTION rm.create_research_goal(body jsonb, revision_of uuid DEFAULT NULL) RETURNS rm.research_goals
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved research_goals;
DECLARE checksum text;
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR jsonb_typeof(body -> 'title') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'title'), '') IS NULL
        OR jsonb_typeof(body -> 'domain') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'domain'), '') IS NULL
        OR jsonb_typeof(body -> 'objective') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'objective'), '') IS NULL
        OR coalesce(body ->> 'sensitivity', '') NOT IN ('public', 'restricted')
        OR (body ? 'progress' AND jsonb_typeof(body -> 'progress') IS DISTINCT FROM 'object') THEN
        RAISE EXCEPTION 'a goal frame needs a title, domain, objective, sensitivity and approved basis versions'
            USING ERRCODE = '22023';
    END IF;
    IF revision_of IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM research_goals old JOIN research_goal_approvals a ON a.goal_id = old.id
        WHERE old.id = create_research_goal.revision_of
    ) THEN
        RAISE EXCEPTION 'a revised goal must identify an existing confirmed frame' USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_research_goal_basis(body -> 'basis_version_ids');
    checksum := encode(sha256(convert_to(jsonb_build_object('body',body,'revision_of',revision_of)::text,
                                                   'UTF8')), 'hex');
    INSERT INTO research_goals (revision_of, body, frame_sha256)
    VALUES (revision_of, body, checksum) RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE FUNCTION rm.confirm_research_goal(goal_id uuid, expected_sha text, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_goals;
DECLARE existing research_goal_approvals;
BEGIN
    SELECT * INTO STRICT target FROM research_goals g WHERE g.id = goal_id FOR UPDATE;
    IF target.frame_sha256 IS DISTINCT FROM expected_sha THEN
        RAISE EXCEPTION 'review the exact goal frame hash before confirming' USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_research_goal_basis(target.body -> 'basis_version_ids');
    SELECT * INTO existing FROM research_goal_approvals a WHERE a.goal_id = target.id;
    IF FOUND THEN
        IF ROW(existing.actor_ref, existing.reason, existing.authenticated_role)
           IS DISTINCT FROM ROW(actor_ref, reason, session_user) THEN
            RAISE EXCEPTION 'the frame already has a different confirmation' USING ERRCODE = '23514';
        END IF;
        RETURN target.id;
    END IF;
    INSERT INTO research_goal_approvals (goal_id, actor_ref, reason) VALUES (target.id, actor_ref, reason);
    RETURN target.id;
END $$;

CREATE FUNCTION rm.propose_research_conjecture(goal_id uuid, body jsonb) RETURNS rm.research_conjectures
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_goals;
DECLARE saved research_conjectures;
DECLARE checksum text;
BEGIN
    SELECT * INTO STRICT target FROM research_goals g WHERE g.id = goal_id FOR UPDATE;
    IF NOT pg_has_role(session_user, 'rm_human', 'USAGE')
        AND target.body ->> 'sensitivity' IS DISTINCT FROM 'public' THEN
        RAISE EXCEPTION 'restricted goal proposals require the human role' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM research_goal_approvals a WHERE a.goal_id = target.id)
        OR EXISTS (SELECT 1 FROM research_goal_selections s WHERE s.goal_id = target.id)
        OR EXISTS (
            SELECT 1 FROM research_condition_requests q
            LEFT JOIN research_condition_decisions d ON d.request_id = q.id
            WHERE q.goal_id = target.id AND q.body ->> 'kind' = 'interpretation'
                AND (d.action IS NULL OR d.action = 'revise')
        ) THEN
        RAISE EXCEPTION 'conjectures require a confirmed frame that has no selected goal' USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['statement','formal','quantifiers','parameters','constant_dependencies','provenance'] <> '{}'::jsonb
        OR jsonb_typeof(body -> 'statement') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'statement'), '') IS NULL
        OR jsonb_typeof(body -> 'formal') IS DISTINCT FROM 'object'
        OR jsonb_typeof(body -> 'provenance') IS DISTINCT FROM 'object'
        OR coalesce(body #>> '{provenance,kind}', '') NOT IN ('human', 'ai', 'program')
        OR (NOT pg_has_role(session_user, 'rm_human', 'USAGE') AND body #>> '{provenance,kind}' = 'human')
        OR (body ? 'quantifiers' AND jsonb_typeof(body -> 'quantifiers') IS DISTINCT FROM 'array')
        OR (body ? 'parameters' AND jsonb_typeof(body -> 'parameters') IS DISTINCT FROM 'array')
        OR (body ? 'constant_dependencies' AND jsonb_typeof(body -> 'constant_dependencies') IS DISTINCT FROM 'object') THEN
        RAISE EXCEPTION 'a conjecture requires structured meaning, provenance and the unchanged frame basis'
            USING ERRCODE = '22023';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_research_goal_basis(target.body -> 'basis_version_ids');
    checksum := encode(sha256(convert_to(jsonb_build_object('goal',target.id,'frame',target.frame_sha256,
                                                          'body',body)::text,'UTF8')), 'hex');
    INSERT INTO research_conjectures (goal_id, body, conjecture_sha256)
    VALUES (target.id, body, checksum) ON CONFLICT ON CONSTRAINT conjecture_goal_digest DO NOTHING
    RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_conjectures c
            WHERE c.goal_id = target.id AND c.conjecture_sha256 = checksum;
    END IF;
    RETURN saved;
END $$;

-- Carry the exact previously approved IDs forward for a selected research goal.
-- The legacy exploration mechanism still reuses the IDs from its parent packet.
CREATE OR REPLACE FUNCTION rm.capture_packet_basis(packet_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE packet formalization_packets;
DECLARE parent uuid;
DECLARE entry jsonb;
DECLARE payload jsonb;
DECLARE version_id uuid;
DECLARE key text;
BEGIN
    SELECT p.* INTO STRICT packet FROM formalization_packets p JOIN formalization_approvals a
        ON a.packet_id = p.id WHERE p.id = capture_packet_basis.packet_id;
    SELECT basis_packet_id INTO parent FROM exploration_goal_links WHERE goal_review_id = packet.review_id;
    FOR entry IN SELECT value FROM jsonb_array_elements(packet.packet -> 'items') LOOP
        payload := entry - ARRAY['id','status','question'];
        key := 'packet:' || packet.review_id || ':' ||
            encode(sha256(convert_to(entry ->> 'id', 'UTF8')), 'hex');
        SELECT b.version_id INTO version_id FROM research_goal_basis_bindings b
        WHERE b.packet_id = packet.id AND b.item_label = entry ->> 'id';
        IF NOT FOUND THEN
            SELECT b.version_id INTO version_id FROM packet_basis_items b JOIN formal_item_versions v
                ON v.id = b.version_id
            WHERE b.packet_id = parent AND b.item_label = entry ->> 'id' AND v.body = payload;
        END IF;
        IF NOT FOUND THEN
            version_id := (insert_formal_item(key, 'packet', payload)).id;
        END IF;
        INSERT INTO packet_basis_items (packet_id, item_label, version_id)
            VALUES (packet.id, entry ->> 'id', version_id) ON CONFLICT DO NOTHING;
    END LOOP;
END $$;

CREATE FUNCTION rm.select_research_conjecture(conjecture_id uuid, expected_sha text, actor_ref text, reason text)
RETURNS rm.research_goal_selections
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE candidate research_conjectures;
DECLARE target research_goals;
DECLARE prepared formalization_packets;
DECLARE recorded research_goal_selections;
DECLARE prior_claim uuid;
DECLARE subject uuid := gen_random_uuid();
DECLARE contract jsonb;
DECLARE packet jsonb;
DECLARE hypotheses jsonb := '[]'::jsonb;
DECLARE items jsonb;
DECLARE mappings jsonb := '[]'::jsonb;
DECLARE entry record;
DECLARE label text;
DECLARE hypothesis_count integer := 0;
BEGIN
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'selecting a conjecture requires a researcher and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT candidate FROM research_conjectures c WHERE c.id = conjecture_id;
    SELECT * INTO STRICT target FROM research_goals g WHERE g.id = candidate.goal_id FOR UPDATE;
    IF NOT EXISTS (SELECT 1 FROM research_goal_approvals a WHERE a.goal_id = target.id)
        OR candidate.conjecture_sha256 IS DISTINCT FROM expected_sha THEN
        RAISE EXCEPTION 'selection requires the confirmed frame and reviewed conjecture hash' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO recorded FROM research_goal_selections s WHERE s.goal_id = target.id;
    IF FOUND THEN
        IF ROW(recorded.conjecture_id, recorded.actor_ref, recorded.reason, recorded.authenticated_role)
            IS DISTINCT FROM ROW(candidate.id, actor_ref, reason, session_user) THEN
            RAISE EXCEPTION 'the frame already selected a conjecture; use a revised frame for a new choice'
                USING ERRCODE = '23514';
        END IF;
        RETURN recorded;
    END IF;
    IF EXISTS (SELECT 1 FROM research_condition_requests q
        JOIN research_condition_decisions d ON d.request_id = q.id AND d.action = 'revise'
        WHERE q.goal_id = target.id) THEN
        RAISE EXCEPTION 'select from the revised frame after adopting a change in scope' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT 1 FROM research_condition_requests q WHERE q.goal_id = target.id
        AND q.body ->> 'kind' = 'interpretation'
        AND NOT EXISTS (SELECT 1 FROM research_condition_decisions d WHERE d.request_id = q.id)) THEN
        RAISE EXCEPTION 'resolve pending meaning questions before freezing the conjecture' USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_research_goal_basis(target.body -> 'basis_version_ids');
    items := jsonb_build_array(jsonb_build_object('id','goal', 'kind','goal',
        'natural', candidate.body -> 'statement', 'formal', candidate.body -> 'formal',
        'status','needs_confirmation'));
    FOR entry IN
        SELECT v.id, v.body, requested.position
        FROM jsonb_array_elements_text(target.body -> 'basis_version_ids') WITH ORDINALITY AS requested(id, position)
        JOIN formal_item_versions v ON v.id::text = requested.id ORDER BY requested.position
    LOOP
        IF entry.body ->> 'kind' = 'hypothesis' THEN
            hypothesis_count := hypothesis_count + 1;
            label := 'H' || hypothesis_count;
            hypotheses := hypotheses || jsonb_build_object('label', label, 'statement', entry.body -> 'natural',
                                                            'formal', entry.body -> 'formal');
        ELSE
            label := 'basis-' || entry.position;
        END IF;
        items := items || jsonb_build_object('id',label,'status','stated',
            'kind',entry.body -> 'kind','natural',entry.body -> 'natural','formal',entry.body -> 'formal');
        mappings := mappings || jsonb_build_object('label', label, 'version_id', entry.id);
    END LOOP;
    -- Match the order used by the immutable claim snapshot.
    SELECT coalesce(jsonb_agg(item ORDER BY item ->> 'label' COLLATE "C"), '[]'::jsonb)
    INTO hypotheses FROM jsonb_array_elements(hypotheses) item;
    contract := jsonb_build_object(
        'format_version', 1, 'claim_id', subject, 'kind', 'researcher_claim', 'origin', 'human',
        'domain', target.body -> 'domain', 'title', target.body -> 'title',
        'statement', candidate.body -> 'statement', 'sensitivity', target.body -> 'sensitivity',
        'formal_skeleton', jsonb_build_object(
            'format','rm-goal-v1','goal_id',target.id,'goal_sha256',target.frame_sha256,
            'candidate_id',candidate.id,'candidate_sha256',candidate.conjecture_sha256,
            'basis_version_ids',target.body -> 'basis_version_ids','target',candidate.body -> 'formal'),
        'small_case_spec', '{}'::jsonb, 'definition_ids', '[]'::jsonb, 'hypotheses', hypotheses,
        'conclusion', jsonb_build_object('statement',candidate.body -> 'statement',
            'quantifiers',coalesce(candidate.body -> 'quantifiers','[]'::jsonb),
            'parameters',coalesce(candidate.body -> 'parameters','[]'::jsonb),
            'constant_dependencies',coalesce(candidate.body -> 'constant_dependencies','{}'::jsonb)));
    packet := jsonb_build_object('format_version',1,'claim_id',subject,'items',items);
    prepared := propose_formalization(packet, contract);
    INSERT INTO research_goal_basis_bindings (packet_id, item_label, version_id)
    SELECT prepared.id, pair ->> 'label', (pair ->> 'version_id')::uuid
    FROM jsonb_array_elements(mappings) pair;
    PERFORM confirm_formalization(prepared.id, prepared.version, prepared.packet_sha256,
                                  ARRAY['goal'], actor_ref, reason);
    INSERT INTO research_goal_selections (goal_id, conjecture_id, packet_id, claim_id, actor_ref, reason)
    VALUES (target.id, candidate.id, prepared.id, subject, actor_ref, reason) RETURNING * INTO recorded;
    SELECT s.claim_id INTO prior_claim FROM research_goal_selections s WHERE s.goal_id = target.revision_of;
    IF FOUND THEN
        INSERT INTO claim_edges (source_id, target_id, kind, justification)
        VALUES (subject, prior_claim, 'supersedes', reason);
    END IF;
    RETURN recorded;
END $$;

CREATE FUNCTION rm.request_research_condition(goal_id uuid, body jsonb) RETURNS rm.research_condition_requests
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target research_goals;
DECLARE saved research_condition_requests;
DECLARE checksum text;
DECLARE selected_packet uuid;
DECLARE label text;
DECLARE clarification uuid;
BEGIN
    SELECT * INTO STRICT target FROM research_goals g WHERE g.id = goal_id FOR UPDATE;
    IF NOT pg_has_role(session_user, 'rm_human', 'USAGE')
        AND target.body ->> 'sensitivity' IS DISTINCT FROM 'public' THEN
        RAISE EXCEPTION 'restricted goal requests require the human role' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM research_goal_approvals a WHERE a.goal_id = target.id)
        OR jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR coalesce(body ->> 'kind', '') NOT IN ('interpretation','route_condition')
        OR coalesce(jsonb_typeof(body -> 'statement'), '') <> 'string'
        OR nullif(btrim(body ->> 'statement'), '') IS NULL
        OR coalesce(jsonb_typeof(body -> 'question'), '') <> 'string'
        OR nullif(btrim(body ->> 'question'), '') IS NULL
        OR coalesce(jsonb_typeof(body -> 'impact'), '') <> 'string'
        OR nullif(btrim(body ->> 'impact'), '') IS NULL
        OR jsonb_typeof(body -> 'formal') IS DISTINCT FROM 'object'
        OR (body ? 'replaces_version_id' AND NOT coalesce(
            (target.body -> 'basis_version_ids') ? (body ->> 'replaces_version_id'), false)) THEN
        RAISE EXCEPTION 'a condition question needs a confirmed frame, meaning, question and concrete impact'
            USING ERRCODE = '22023';
    END IF;
    checksum := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');
    SELECT * INTO saved FROM research_condition_requests q
    WHERE q.goal_id = target.id AND q.request_sha256 = checksum;
    IF FOUND THEN RETURN saved; END IF;
    IF body ->> 'kind' = 'interpretation' THEN
        SELECT s.packet_id INTO selected_packet FROM research_goal_selections s WHERE s.goal_id = target.id;
        IF FOUND THEN
            SELECT b.item_label INTO label FROM research_goal_basis_bindings b
            WHERE b.packet_id = selected_packet AND b.version_id::text = body ->> 'replaces_version_id';
            clarification := request_clarification(selected_packet, coalesce(label,'goal'),
                                                     body ->> 'question', body ->> 'impact');
        END IF;
    END IF;
    INSERT INTO research_condition_requests (goal_id, body, request_sha256, clarification_id)
    VALUES (target.id, body, checksum, clarification) RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE FUNCTION rm.decide_research_condition(request_id uuid, action text, actor_ref text, reason text,
    version_id uuid DEFAULT NULL, evidence_id uuid DEFAULT NULL) RETURNS rm.research_condition_decisions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE question research_condition_requests;
DECLARE target research_goals;
DECLARE saved research_condition_decisions;
DECLARE new_goal uuid;
DECLARE old_version text;
DECLARE new_basis jsonb;
BEGIN
    IF coalesce(action, '') NOT IN ('reaffirm','reject','revise','derived')
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL
        OR ((action IN ('reaffirm','revise')) <> (version_id IS NOT NULL))
        OR ((action = 'derived') <> (evidence_id IS NOT NULL)) THEN
        RAISE EXCEPTION 'provide the decision reason and its required version or verification'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT question FROM research_condition_requests q WHERE q.id = request_id;
    SELECT * INTO STRICT target FROM research_goals g WHERE g.id = question.goal_id FOR UPDATE;
    SELECT * INTO saved FROM research_condition_decisions d WHERE d.request_id = question.id;
    IF FOUND THEN
        IF ROW(saved.action,saved.actor_ref,saved.reason,saved.version_id,saved.evidence_id,saved.authenticated_role)
            IS DISTINCT FROM ROW(action,actor_ref,reason,version_id,evidence_id,session_user) THEN
            RAISE EXCEPTION 'the condition already has a different decision' USING ERRCODE = '23514';
        END IF;
        RETURN saved;
    END IF;
    PERFORM lock_live_foundation();
    IF action IN ('reaffirm','revise') THEN
        PERFORM require_research_goal_basis(jsonb_build_array(version_id));
        IF NOT EXISTS (SELECT 1 FROM formal_item_versions v WHERE v.id = version_id
            AND v.body ->> 'kind' = 'hypothesis' AND v.body -> 'formal' = question.body -> 'formal') THEN
            RAISE EXCEPTION 'the condition does not have the same formal meaning as the named hypothesis'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF action = 'reaffirm' AND NOT ((target.body -> 'basis_version_ids') ? version_id::text) THEN
        RAISE EXCEPTION 'reaffirmation can only use an existing hypothesis in this frame' USING ERRCODE = '23514';
    ELSIF action = 'revise' THEN
        IF (target.body -> 'basis_version_ids') ? version_id::text THEN
            RAISE EXCEPTION 'an existing hypothesis requires reaffirmation' USING ERRCODE = '23514';
        END IF;
        IF question.body ->> 'kind' <> 'interpretation' THEN
            RAISE EXCEPTION 'a proof route condition cannot revise the research frame' USING ERRCODE = '23514';
        END IF;
        old_version := question.body ->> 'replaces_version_id';
        IF old_version IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM formal_item_versions v WHERE v.id::text = old_version AND v.body ->> 'kind' = 'hypothesis'
        ) THEN
            RAISE EXCEPTION 'revising a hypothesis can replace only a hypothesis' USING ERRCODE = '23514';
        END IF;
        new_basis := CASE WHEN old_version IS NULL THEN target.body -> 'basis_version_ids'
            ELSE (target.body -> 'basis_version_ids') - old_version END;
        new_goal := (create_research_goal(target.body || jsonb_build_object(
            'basis_version_ids', new_basis || to_jsonb(version_id)), target.id)).id;
    ELSIF action = 'derived' AND NOT EXISTS (
        SELECT 1 FROM evidence_foundation_status e
        JOIN claims c ON c.id = e.claim_id AND c.contract_sha256 = e.contract_sha256
        JOIN research_goal_selections chosen ON chosen.claim_id = c.id
        JOIN research_goals witness ON witness.id = chosen.goal_id
        WHERE e.id = evidence_id AND e.issuance_sha256 IS NOT NULL AND e.verdict = 'verified'
            AND e.diff_status = 'exact' AND e.kind IN ('lean','exact_certificate')
            AND NOT e.needs_recheck AND e.review_status <> 'returned'
            AND c.domain = target.body ->> 'domain' AND c.sensitivity = target.body ->> 'sensitivity'
            AND c.formal_skeleton ->> 'format' = 'rm-goal-v1'
            AND c.formal_skeleton -> 'target' = question.body -> 'formal'
            AND (target.body -> 'basis_version_ids') @> (witness.body -> 'basis_version_ids')
    ) THEN
        RAISE EXCEPTION 'derived conditions need current exact evidence for the same formal statement using only this basis'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO research_condition_decisions (request_id, action, actor_ref, reason, version_id,
                                              evidence_id, revised_goal_id)
    VALUES (question.id, action, actor_ref, reason, version_id, evidence_id, new_goal) RETURNING * INTO saved;
    IF question.clarification_id IS NOT NULL AND action <> 'revise' THEN
        PERFORM resolve_clarification(question.clarification_id, actor_ref, reason);
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.record_research_derivation(request_id uuid, evidence_id uuid)
RETURNS rm.research_condition_decisions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM research_condition_requests q
        WHERE q.id = record_research_derivation.request_id AND q.body ->> 'kind' = 'route_condition') THEN
        RAISE EXCEPTION 'a verifier can discharge a route condition; meaning questions require a researcher'
            USING ERRCODE = '23514';
    END IF;
    RETURN decide_research_condition(request_id, 'derived', 'exact verifier',
        'The independent receipt proves the same condition from the approved basis.', NULL, evidence_id);
END $$;

CREATE VIEW rm.research_goal_basis WITH (security_barrier = true) AS
    SELECT g.id AS goal_id, basis.position, s.id AS version_id, s.logical_key, s.version,
           s.version_sha256, s.body, s.needs_recheck
    FROM rm.research_goals g
    CROSS JOIN LATERAL jsonb_array_elements_text(g.body -> 'basis_version_ids')
        WITH ORDINALITY AS basis(version_id, position)
    JOIN rm.formal_item_status s ON s.id::text = basis.version_id;

CREATE VIEW rm.research_goal_status WITH (security_barrier = true) AS
    SELECT g.*, a.approved_at, a.actor_ref AS approved_by, s.conjecture_id, s.packet_id, s.claim_id,
           claim.status AS claim_status, packet.proof_ready,
           NOT EXISTS (SELECT 1 FROM rm.research_goal_basis b
                       WHERE b.goal_id = g.id AND b.needs_recheck) AS basis_current,
           EXISTS (SELECT 1 FROM rm.research_condition_requests q
                LEFT JOIN rm.research_condition_decisions d ON d.request_id = q.id
                WHERE q.goal_id = g.id AND q.body ->> 'kind' = 'interpretation'
                    AND (d.action IS NULL OR d.action = 'revise')) AS interpretation_hold
    FROM rm.research_goals g
    LEFT JOIN rm.research_goal_approvals a ON a.goal_id = g.id
    LEFT JOIN rm.research_goal_selections s ON s.goal_id = g.id
    LEFT JOIN rm.formalization_status packet ON packet.id = s.packet_id
    LEFT JOIN rm.claim_foundation_status claim ON claim.id = s.claim_id;

CREATE VIEW rm.research_condition_status WITH (security_barrier = true) AS
    SELECT q.*, decision.action, decision.actor_ref, decision.reason, decision.version_id,
           decision.evidence_id, decision.revised_goal_id, decision.decided_at,
           CASE WHEN decision.evidence_id IS NOT NULL THEN
               e.verdict = 'verified' AND e.diff_status = 'exact' AND NOT e.needs_recheck AND e.review_status <> 'returned'
           END AS derivation_current
    FROM rm.research_condition_requests q
    LEFT JOIN rm.research_condition_decisions decision ON decision.request_id = q.id
    LEFT JOIN rm.evidence_foundation_status e ON e.id = decision.evidence_id;

REVOKE EXECUTE ON FUNCTION rm.require_research_goal_basis(jsonb), rm.create_research_goal(jsonb, uuid),
    rm.confirm_research_goal(uuid, text, text, text), rm.propose_research_conjecture(uuid, jsonb),
    rm.select_research_conjecture(uuid, text, text, text), rm.request_research_condition(uuid, jsonb),
    rm.decide_research_condition(uuid, text, text, text, uuid, uuid),
    rm.record_research_derivation(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.create_research_goal(jsonb, uuid), rm.confirm_research_goal(uuid, text, text, text),
    rm.select_research_conjecture(uuid, text, text, text),
    rm.decide_research_condition(uuid, text, text, text, uuid, uuid) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.propose_research_conjecture(uuid, jsonb),
    rm.request_research_condition(uuid, jsonb) TO rm_human, rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.record_research_derivation(uuid, uuid) TO rm_verifier;
GRANT SELECT ON rm.research_goal_status, rm.research_goal_basis, rm.research_conjectures,
    rm.research_condition_status TO rm_human;
