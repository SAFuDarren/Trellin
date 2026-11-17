CREATE TABLE rm.target_clarifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_sha256 rm.sha256_hex NOT NULL,
    parent_id uuid REFERENCES rm.target_clarifications(id),
    goal_id uuid REFERENCES rm.research_goals(id),
    frame_sha256 rm.sha256_hex,
    title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 300),
    sensitivity text NOT NULL CHECK (sensitivity IN ('public', 'restricted')),
    questions jsonb NOT NULL CHECK (jsonb_typeof(questions) = 'array'),
    proposal_sha256 rm.sha256_hex NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    answers jsonb,
    actor_ref text,
    reason text,
    decided_at timestamptz,
    result_sha256 rm.sha256_hex,
    CHECK ((goal_id IS NULL) = (frame_sha256 IS NULL)),
    UNIQUE NULLS NOT DISTINCT (subject_sha256, parent_id)
);

CREATE VIEW rm.target_clarification_status WITH (security_barrier = true) AS
SELECT q.*, child.id AS next_clarification_id,
    CASE WHEN q.result_sha256 IS NOT NULL THEN 'continued'
         WHEN child.id IS NOT NULL THEN 'continued'
         WHEN q.decided_at IS NOT NULL AND q.answers IS NULL THEN 'dismissed'
         WHEN q.goal_id IS NOT NULL AND NOT (g.approved_at IS NOT NULL AND g.basis_current
             AND NOT g.interpretation_hold AND g.conjecture_id IS NULL AND g.frame_sha256 = q.frame_sha256)
             THEN 'stale'
         WHEN q.answers IS NOT NULL THEN 'answered'
         ELSE 'waiting' END AS status
FROM rm.target_clarifications q
LEFT JOIN rm.research_goal_status g ON g.id = q.goal_id
LEFT JOIN rm.target_clarifications child ON child.parent_id = q.id AND child.subject_sha256 = q.subject_sha256;

CREATE FUNCTION rm.record_target_clarification(subject_sha256 text, parent_id uuid, goal_id uuid,
    frame_sha256 text, title text, sensitivity text, questions jsonb, proposal_sha256 text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE request_id uuid;
BEGIN
    IF jsonb_typeof($7) IS DISTINCT FROM 'array' OR jsonb_array_length($7) NOT BETWEEN 1 AND 8
       OR EXISTS (SELECT FROM jsonb_array_elements($7) item
                  WHERE jsonb_typeof(item) <> 'string' OR char_length(item #>> '{}') > 1200
                  OR nullif(btrim(item #>> '{}'), '') IS NULL) THEN
        RAISE EXCEPTION 'record specific questions about the intended formalization' USING ERRCODE = '22023';
    END IF;
    IF $3 IS NOT NULL AND NOT EXISTS (SELECT FROM research_goal_status frame
        WHERE frame.id = $3 AND frame.frame_sha256 = $4 AND frame.approved_at IS NOT NULL
        AND frame.basis_current AND NOT frame.interpretation_hold AND frame.conjecture_id IS NULL
        AND frame.body ->> 'sensitivity' = $6) THEN
        RAISE EXCEPTION 'the exact reviewed conjecture frame is no longer current' USING ERRCODE = '23514';
    END IF;
    IF $2 IS NOT NULL AND NOT EXISTS (SELECT FROM target_clarification_status prior
        WHERE prior.id = $2 AND prior.subject_sha256 = $1 AND prior.status = 'answered') THEN
        RAISE EXCEPTION 'continue only an answered question from the same draft' USING ERRCODE = '23514';
    END IF;
    INSERT INTO target_clarifications AS q
        (subject_sha256, parent_id, goal_id, frame_sha256, title, sensitivity, questions, proposal_sha256)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT ON CONSTRAINT target_clarifications_subject_sha256_parent_id_key DO NOTHING RETURNING q.id INTO request_id;
    IF NOT FOUND THEN
        SELECT q.id INTO STRICT request_id FROM target_clarifications q
        WHERE q.subject_sha256 = $1 AND q.parent_id IS NOT DISTINCT FROM $2;
    END IF;
    RETURN request_id;
END $$;

CREATE FUNCTION rm.answer_target_clarification(request_id uuid, answers jsonb, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE request target_clarifications;
BEGIN
    SELECT * INTO STRICT request FROM target_clarifications q WHERE q.id = $1 FOR UPDATE;
    IF coalesce(char_length(btrim($3)), 0) NOT BETWEEN 1 AND 300
       OR coalesce(char_length(btrim($4)), 0) NOT BETWEEN 1 AND 2000 THEN
        RAISE EXCEPTION 'a clarification decision needs a researcher and a reason' USING ERRCODE = '22023';
    END IF;
    IF request.decided_at IS NOT NULL AND request.answers IS NOT DISTINCT FROM $2
        AND request.actor_ref = $3 AND request.reason = $4 THEN
        RETURN request.id;
    END IF;
    IF NOT EXISTS (SELECT FROM target_clarification_status q WHERE q.id = $1 AND q.status = 'waiting') THEN
        RAISE EXCEPTION 'the requested clarification is no longer waiting for a decision' USING ERRCODE = '23514';
    END IF;
    IF $2 IS NOT NULL AND (jsonb_typeof($2) IS DISTINCT FROM 'array'
       OR jsonb_array_length($2) <> jsonb_array_length(request.questions)
       OR EXISTS (SELECT FROM jsonb_array_elements($2) item
                  WHERE jsonb_typeof(item) <> 'string' OR char_length(item #>> '{}') > 1200
                  OR nullif(btrim(item #>> '{}'), '') IS NULL)) THEN
        RAISE EXCEPTION 'supply one concise answer for each question, in the recorded order' USING ERRCODE = '22023';
    END IF;
    UPDATE target_clarifications SET answers = $2, actor_ref = $3, reason = $4, decided_at = clock_timestamp()
    WHERE id = $1;
    RETURN request.id;
END $$;

CREATE FUNCTION rm.complete_target_clarification(request_id uuid, result_sha256 text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    UPDATE target_clarifications SET result_sha256 = $2 WHERE id = $1
    AND EXISTS (SELECT FROM target_clarification_status q WHERE q.id = $1 AND q.status = 'answered');
    IF NOT FOUND THEN
        RAISE EXCEPTION 'the answered clarification changed before formalization completed' USING ERRCODE = '23514';
    END IF;
END $$;

CREATE INDEX target_clarifications_open ON rm.target_clarifications(requested_at, id) WHERE decided_at IS NULL;

ALTER VIEW rm.research_actionable_review_inbox RENAME TO research_actionable_review_before_clarifications;
CREATE VIEW rm.research_actionable_review_inbox WITH (security_barrier = true) AS
    SELECT * FROM rm.research_actionable_review_before_clarifications
    UNION ALL SELECT 'clarify_formalization', q.id, NULL::uuid, q.requested_at,
        'Clarify the intended meaning before formalization can continue.',
        jsonb_build_object('title',q.title,'questions',q.questions,'goal_id',q.goal_id)
    FROM rm.target_clarification_status q WHERE q.status = 'waiting';

REVOKE ALL ON FUNCTION rm.record_target_clarification(text,uuid,uuid,text,text,text,jsonb,text),
    rm.answer_target_clarification(uuid,jsonb,text,text), rm.complete_target_clarification(uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_target_clarification(text,uuid,uuid,text,text,text,jsonb,text),
    rm.answer_target_clarification(uuid,jsonb,text,text), rm.complete_target_clarification(uuid,text) TO rm_human;
GRANT SELECT ON rm.target_clarification_status, rm.research_actionable_review_inbox TO rm_human;
