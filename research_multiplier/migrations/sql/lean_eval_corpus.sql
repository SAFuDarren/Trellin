CREATE TABLE rm.lean_eval_corpora (
    sha256 rm.sha256_hex PRIMARY KEY,
    body jsonb NOT NULL,
    actor_ref text NOT NULL CHECK (length(btrim(actor_ref)) BETWEEN 1 AND 200),
    reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 4000),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.lean_eval_case_splits (
    goal_sha256 rm.sha256_hex PRIMARY KEY,
    split text NOT NULL CHECK (split IN ('development', 'holdout')),
    first_corpus rm.sha256_hex NOT NULL REFERENCES rm.lean_eval_corpora(sha256)
);

REVOKE ALL ON rm.lean_eval_corpora, rm.lean_eval_case_splits FROM PUBLIC;
GRANT SELECT ON rm.lean_eval_corpora TO rm_human, rm_verifier;

CREATE FUNCTION rm.seal_lean_eval_corpus(checksum rm.sha256_hex, document jsonb, actor text, explanation text)
RETURNS SETOF rm.lean_eval_corpora LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE prior lean_eval_corpora;
DECLARE split_row record;
BEGIN
    IF jsonb_typeof(document) IS DISTINCT FROM 'object'
       OR document -> 'format_version' IS DISTINCT FROM '1'::jsonb
       OR document - ARRAY['format_version','development','holdout'] <> '{}'::jsonb
       OR jsonb_typeof(document -> 'development') IS DISTINCT FROM 'array'
       OR jsonb_typeof(document -> 'holdout') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'invalid Lean evaluation corpus' USING ERRCODE = '23514';
    END IF;
    IF jsonb_array_length(document -> 'development') NOT BETWEEN 1 AND 128
       OR jsonb_array_length(document -> 'holdout') NOT BETWEEN 1 AND 128 THEN
        RAISE EXCEPTION 'unbounded or empty Lean evaluation split' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements((document -> 'development') || (document -> 'holdout')) AS item
        WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
           OR NOT (item ?& ARRAY['statement_id','expected','scope_sha256','goal_sha256'])
           OR item - ARRAY['statement_id','expected','scope_sha256','goal_sha256'] <> '{}'::jsonb
           OR item ->> 'goal_sha256' !~ '^[0-9a-f]{64}$' OR item ->> 'scope_sha256' !~ '^[0-9a-f]{64}$'
           OR item ->> 'statement_id' !~ '^[0-9a-f-]{36}$'
           OR item ->> 'expected' NOT IN ('verified','refuted','open')
           OR item ->> 'statement_id' IS NULL OR item ->> 'expected' IS NULL
           OR item ->> 'goal_sha256' IS NULL OR item ->> 'scope_sha256' IS NULL
    ) OR EXISTS (
        SELECT FROM jsonb_array_elements((document -> 'development') || (document -> 'holdout')) AS item
        GROUP BY item ->> 'goal_sha256' HAVING count(*) > 1
    ) OR EXISTS (
        SELECT FROM jsonb_array_elements((document -> 'development') || (document -> 'holdout')) AS item
        GROUP BY item ->> 'statement_id' HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'invalid or duplicate Lean evaluation statement' USING ERRCODE = '23514';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.lean_eval_corpora', 17));
    SELECT * INTO prior FROM lean_eval_corpora WHERE sha256 = checksum;
    IF FOUND AND prior.body IS DISTINCT FROM document THEN
        RAISE EXCEPTION 'Lean evaluation corpus digest already binds different inputs' USING ERRCODE = '23514';
    END IF;
    IF FOUND THEN
        RETURN NEXT prior;
        RETURN;
    END IF;
    FOR split_row IN
        SELECT side.key AS split, item ->> 'goal_sha256' AS goal
        FROM jsonb_each(document - 'format_version') side CROSS JOIN LATERAL jsonb_array_elements(side.value) item
    LOOP
        IF EXISTS (SELECT FROM lean_eval_case_splits WHERE goal_sha256 = split_row.goal AND split <> split_row.split) THEN
            RAISE EXCEPTION 'a previously enrolled Lean proposition cannot change evaluation splits' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    INSERT INTO lean_eval_corpora(sha256, body, actor_ref, reason) VALUES (checksum, document, actor, explanation)
    RETURNING * INTO prior;
    INSERT INTO lean_eval_case_splits(goal_sha256, split, first_corpus)
        SELECT item ->> 'goal_sha256', side.key, checksum
        FROM jsonb_each(document - 'format_version') side CROSS JOIN LATERAL jsonb_array_elements(side.value) item
        ON CONFLICT (goal_sha256) DO NOTHING;
    RETURN NEXT prior;
END $$;

REVOKE ALL ON FUNCTION rm.seal_lean_eval_corpus(rm.sha256_hex,jsonb,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.seal_lean_eval_corpus(rm.sha256_hex,jsonb,text,text) TO rm_human;
