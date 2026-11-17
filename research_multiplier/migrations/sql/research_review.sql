CREATE TABLE rm.literature_sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL REFERENCES rm.claims(id),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    source_sha256 rm.sha256_hex NOT NULL,
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (claim_id, source_sha256)
);
CREATE INDEX literature_source_identifier ON rm.literature_sources ((body ->> 'identifier'));

CREATE TABLE rm.literature_source_confirmations (
    source_id uuid PRIMARY KEY REFERENCES rm.literature_sources(id),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.novelty_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL REFERENCES rm.claims(id),
    evidence_id uuid REFERENCES rm.evidence(id),
    assessment text NOT NULL CHECK (assessment IN ('not_found', 'known', 'distinct')),
    source_ids uuid[] NOT NULL,
    report jsonb NOT NULL CHECK (jsonb_typeof(report) = 'object'),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((assessment = 'distinct') = (evidence_id IS NOT NULL))
);
CREATE INDEX novelty_reviews_recent ON rm.novelty_reviews(claim_id, sequence_no DESC);

CREATE TABLE rm.research_understanding (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    explanation text NOT NULL CHECK (btrim(explanation) <> ''),
    lesson text NOT NULL CHECK (btrim(lesson) <> ''),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX research_understanding_evidence ON rm.research_understanding(evidence_id, recorded_at);

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['literature_sources', 'literature_source_confirmations',
                                      'novelty_reviews', 'research_understanding']
    LOOP
        EXECUTE format('CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.%I
                        FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation()', table_name);
    END LOOP;
END $$;

CREATE VIEW rm.literature_source_status WITH (security_barrier = true) AS
    SELECT s.*, c.confirmed_at, c.actor_ref AS confirmed_by, c.reason AS confirmation_reason,
        EXISTS (SELECT 1 FROM rm.verification_input_retirements retired
                WHERE retired.input_kind = 'source'
                  AND retired.sha256 IN (s.source_sha256, s.body #>> '{archive,sha256}')) AS retired
    FROM rm.literature_sources s LEFT JOIN rm.literature_source_confirmations c ON c.source_id = s.id;

CREATE FUNCTION rm.register_literature_source(claim_id uuid, body jsonb) RETURNS rm.literature_sources
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE canonical text;
DECLARE registered literature_sources;
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['identifier','title','locator','excerpt','convention_map','archive','origin'] <> '{}'::jsonb
        OR jsonb_typeof(body -> 'identifier') IS DISTINCT FROM 'string'
        OR NOT coalesce(body ->> 'identifier' ~ '^(arXiv:([0-9]{4}[.][0-9]{4,5}|[a-zA-Z.-]+/[0-9]{7})v[1-9][0-9]*|doi:10[.][0-9]{4,9}/[^[:space:][:cntrl:]]+)$', false)
        OR coalesce(body ->> 'origin', '') NOT IN ('arxiv_download', 'operator_import')
        OR (body ->> 'origin' = 'arxiv_download' AND body ->> 'identifier' !~ '^arXiv:')
        OR NOT lean_artifact_ref_valid(body -> 'archive')
        OR jsonb_typeof(body -> 'title') IS DISTINCT FROM 'string'
        OR jsonb_typeof(body -> 'locator') IS DISTINCT FROM 'string'
        OR jsonb_typeof(body -> 'excerpt') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'title'), '') IS NULL OR length(body ->> 'title') > 1000
        OR nullif(btrim(body ->> 'locator'), '') IS NULL OR length(body ->> 'locator') > 1000
        OR nullif(btrim(body ->> 'excerpt'), '') IS NULL OR length(body ->> 'excerpt') > 24000
        OR jsonb_typeof(body -> 'convention_map') IS DISTINCT FROM 'object'
        OR pg_column_size(body -> 'convention_map') > 24000 THEN
        RAISE EXCEPTION 'literature requires a versioned source identifier, archived original, theorem locator and conventions'
            USING ERRCODE = '22023';
    END IF;
    canonical := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');
    INSERT INTO literature_sources (claim_id, body, source_sha256)
        VALUES (claim_id, body, canonical)
        ON CONFLICT ON CONSTRAINT literature_sources_claim_id_source_sha256_key DO NOTHING
        RETURNING * INTO registered;
    IF NOT FOUND THEN
        SELECT * INTO STRICT registered FROM literature_sources s
        WHERE s.claim_id = register_literature_source.claim_id AND s.source_sha256 = canonical;
    END IF;
    RETURN registered;
END $$;

CREATE FUNCTION rm.confirm_literature_source(source_id uuid, expected_sha text, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE admitted literature_source_confirmations;
BEGIN
    PERFORM lock_live_foundation();
    IF NOT EXISTS (
        SELECT 1 FROM literature_source_status s WHERE s.id = source_id
          AND s.source_sha256 = expected_sha AND NOT s.retired
    ) OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'confirm the exact available source version with actor and reason' USING ERRCODE = '23514';
    END IF;
    INSERT INTO literature_source_confirmations (source_id, actor_ref, reason)
        VALUES (source_id, actor_ref, reason)
        ON CONFLICT ON CONSTRAINT literature_source_confirmations_pkey DO NOTHING
        RETURNING * INTO admitted;
    IF NOT FOUND THEN
        SELECT * INTO STRICT admitted FROM literature_source_confirmations c
        WHERE c.source_id = confirm_literature_source.source_id;
        IF ROW(admitted.authenticated_role, admitted.actor_ref, admitted.reason)
            IS DISTINCT FROM ROW(session_user::name, actor_ref, reason) THEN
            RAISE EXCEPTION 'this source version already has another confirmation' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN source_id;
END $$;

CREATE FUNCTION rm.record_novelty_review(claim_id uuid, evidence_id uuid, assessment text, source_ids uuid[],
                                        report jsonb, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE result_id uuid;
BEGIN
    PERFORM 1 FROM claims c WHERE c.id = claim_id AND c.frozen_at IS NOT NULL FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'novelty review needs a frozen claim' USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    IF assessment IS NULL OR assessment NOT IN ('not_found', 'known', 'distinct')
        OR source_ids IS NULL OR array_position(source_ids, NULL) IS NOT NULL
        OR cardinality(source_ids) <> (SELECT count(DISTINCT source_id) FROM unnest(source_ids) source_id)
        OR jsonb_typeof(report) IS DISTINCT FROM 'object'
        OR nullif(btrim(report ->> 'search_scope'), '') IS NULL
        OR jsonb_typeof(report -> 'search_scope') IS DISTINCT FROM 'string'
        OR pg_column_size(report) > 65536
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'novelty review needs a search scope, an assessment, actor and reason' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (
        SELECT 1 FROM unnest(source_ids) source_id
        WHERE NOT EXISTS (
            SELECT 1 FROM literature_source_status s WHERE s.id = source_id
              AND s.claim_id = record_novelty_review.claim_id AND s.confirmed_at IS NOT NULL AND NOT s.retired
        )
    ) THEN
        RAISE EXCEPTION 'novelty review may cite only current confirmed original sources for this claim' USING ERRCODE = '23514';
    END IF;
    IF assessment IN ('known', 'distinct') AND (
        cardinality(source_ids) = 0 OR nullif(btrim(report ->> 'comparison'), '') IS NULL
        OR jsonb_typeof(report -> 'comparison') IS DISTINCT FROM 'string'
    ) THEN
        RAISE EXCEPTION 'a literature judgment requires a comparison against confirmed original sources'
            USING ERRCODE = '23514';
    END IF;
    IF (assessment = 'distinct' AND NOT EXISTS (
        SELECT 1 FROM evidence_foundation_status e WHERE e.id = evidence_id AND e.claim_id = record_novelty_review.claim_id
          AND e.kind IN ('exact_certificate', 'lean', 'audited_proof') AND e.verdict = 'verified'
          AND e.diff_status = 'exact' AND e.review_status = 'approved' AND NOT e.needs_recheck
    )) OR (assessment <> 'distinct' AND evidence_id IS NOT NULL) THEN
        RAISE EXCEPTION 'a distinct result requires current exact proof evidence approved by a researcher'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO novelty_reviews (claim_id, evidence_id, assessment, source_ids, report, actor_ref, reason)
        VALUES (claim_id, evidence_id, assessment, source_ids, report, actor_ref, reason)
        RETURNING id INTO result_id;
    RETURN result_id;
END $$;

CREATE VIEW rm.novelty_review_status WITH (security_barrier = true) AS
    SELECT c.id AS claim_id, review.id AS review_id, review.evidence_id, review.assessment,
        review.source_ids, review.report, review.actor_ref, review.reason, review.recorded_at,
        CASE
            WHEN review.id IS NULL OR review.assessment = 'not_found' THEN 'possibly_known'
            WHEN EXISTS (
                SELECT 1 FROM rm.literature_source_status source WHERE source.id = ANY(review.source_ids) AND source.retired
            ) THEN 'needs_review'
            WHEN review.assessment = 'known' THEN 'known'
            WHEN evidence.needs_recheck OR evidence.review_status <> 'approved' THEN 'needs_review'
            ELSE 'human_cleared'
        END AS status
    FROM rm.claims c
    LEFT JOIN LATERAL (
        SELECT * FROM rm.novelty_reviews n WHERE n.claim_id = c.id ORDER BY n.sequence_no DESC LIMIT 1
    ) review ON true
    LEFT JOIN rm.evidence_foundation_status evidence ON evidence.id = review.evidence_id
    WHERE c.frozen_at IS NOT NULL;

CREATE FUNCTION rm.record_research_understanding(evidence_id uuid, actor_ref text, explanation text, lesson text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE result_id uuid;
BEGIN
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(explanation), '') IS NULL
       OR nullif(btrim(lesson), '') IS NULL THEN
        RAISE EXCEPTION 'record the researcher explanation and takeaway' USING ERRCODE = '22023';
    END IF;
    PERFORM lock_live_foundation();
    IF NOT EXISTS (
        SELECT 1 FROM evidence_foundation_status e WHERE e.id = evidence_id
          AND e.verdict = 'verified' AND e.diff_status = 'exact' AND NOT e.needs_recheck
    ) THEN
        RAISE EXCEPTION 'understanding record requires current exact verified evidence' USING ERRCODE = '23514';
    END IF;
    INSERT INTO research_understanding (evidence_id, actor_ref, explanation, lesson)
        VALUES (evidence_id, actor_ref, explanation, lesson) RETURNING id INTO result_id;
    RETURN result_id;
END $$;

CREATE VIEW rm.research_review_inbox WITH (security_barrier = true) AS
    SELECT 'confirm_formalization'::text AS kind, p.id AS item_id, p.claim_id, p.proposed_at AS waiting_since,
        'Does this version match the intended statement and assumptions?'::text AS question,
        jsonb_build_object('version', p.version, 'sha256', p.packet_sha256) AS detail
    FROM rm.formalization_status p WHERE NOT p.approved AND NOT p.superseded
    UNION ALL
    SELECT 'resolve_clarification', q.id, p.claim_id, q.requested_at, q.question,
        jsonb_build_object('packet_id', p.id, 'item_id', q.item_id, 'impact', q.impact)
    FROM rm.formalization_clarification_status q
    JOIN rm.formalization_status p ON p.id = q.packet_id WHERE NOT q.resolved AND NOT p.superseded
        AND NOT EXISTS (SELECT 1 FROM rm.research_condition_status linked WHERE linked.clarification_id = q.id)
    UNION ALL
    SELECT 'confirm_goal', g.id, g.claim_id, g.proposed_at,
        'Are the objective and exact starting basis suitable for exploration?',
        jsonb_build_object('title', g.body ->> 'title', 'sha256', g.frame_sha256)
    FROM rm.research_goal_status g WHERE g.approved_at IS NULL AND g.basis_current
    UNION ALL
    SELECT 'choose_conjecture', g.id, g.claim_id, proposed.first_at,
        'Which conjecture matches the intended research direction?',
        jsonb_build_object('title', g.body ->> 'title', 'candidates', proposed.count)
    FROM rm.research_goal_status g
    CROSS JOIN LATERAL (
        SELECT count(*) AS count, min(c.proposed_at) AS first_at FROM rm.research_conjectures c WHERE c.goal_id = g.id
    ) proposed
    WHERE g.approved_at IS NOT NULL AND g.basis_current AND g.conjecture_id IS NULL AND proposed.count > 0
    UNION ALL
    SELECT 'decide_condition', q.id, g.claim_id, q.requested_at, q.body ->> 'question',
        jsonb_build_object('goal_id', g.id, 'impact', q.body ->> 'impact',
                           'statement', q.body ->> 'statement', 'kind', q.body ->> 'kind')
    FROM rm.research_condition_status q JOIN rm.research_goal_status g ON g.id = q.goal_id
    WHERE q.action IS NULL
    UNION ALL
    SELECT 'confirm_lean_statement', s.id, s.claim_id, s.proposed_at,
        'Does this Lean proposition express the same claim?',
        jsonb_build_object('version', s.version, 'sha256', s.statement_sha256, 'packet_id', s.packet_id)
    FROM rm.lean_statement_status s JOIN rm.formalization_status p ON p.id = s.packet_id
    WHERE NOT s.approved AND s."current" AND p.proof_ready
    UNION ALL
    SELECT 'review_evidence', e.id, e.claim_id, e.issued_at,
        'Approve or return this verified result with a reason.',
        jsonb_build_object('kind', e.kind, 'sha256', e.artifact_sha256)
    FROM rm.evidence_foundation_status e JOIN rm.claim_foundation_status c ON c.id = e.claim_id
    WHERE c.status = 'open' AND e.review_status = 'pending' AND NOT e.needs_recheck
        AND e.verdict = 'verified' AND e.diff_status = 'exact' AND e.issuance_sha256 IS NOT NULL
    UNION ALL
    SELECT 'confirm_literature_source', s.id, s.claim_id, s.recorded_at,
        'Does the archived original support this theorem record and convention mapping?',
        jsonb_build_object('identifier', s.body ->> 'identifier', 'sha256', s.source_sha256)
    FROM rm.literature_source_status s WHERE s.confirmed_at IS NULL AND NOT s.retired;

REVOKE ALL ON FUNCTION rm.register_literature_source(uuid, jsonb),
    rm.confirm_literature_source(uuid, text, text, text),
    rm.record_novelty_review(uuid, uuid, text, uuid[], jsonb, text, text),
    rm.record_research_understanding(uuid, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.register_literature_source(uuid, jsonb),
    rm.confirm_literature_source(uuid, text, text, text),
    rm.record_novelty_review(uuid, uuid, text, uuid[], jsonb, text, text),
    rm.record_research_understanding(uuid, text, text, text) TO rm_human;
GRANT SELECT ON rm.literature_source_status, rm.novelty_review_status, rm.novelty_reviews,
    rm.research_understanding, rm.research_review_inbox TO rm_human;
