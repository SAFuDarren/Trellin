CREATE TABLE rm.lean_reductions (
    statement_id uuid NOT NULL REFERENCES rm.lean_statements(id),
    implication_id uuid PRIMARY KEY REFERENCES rm.local_lean_lemmas(id),
    obligations text[] NOT NULL CHECK (cardinality(obligations) BETWEEN 1 AND 8),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX lean_reductions_statement ON rm.lean_reductions(statement_id, recorded_at, implication_id);
CREATE TRIGGER lean_reductions_append_only BEFORE UPDATE OR DELETE ON rm.lean_reductions
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
GRANT SELECT ON rm.lean_reductions TO rm_human, rm_verifier;

CREATE FUNCTION rm.record_lean_reduction(anchor_id uuid, lemma_id uuid, obligations text[]) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE required text;
BEGIN
    IF coalesce(cardinality($3), 0) NOT BETWEEN 1 AND 8 OR EXISTS (
        SELECT 1 FROM unnest($3) p WHERE p IS NULL OR btrim(p) = '' OR length(p) > 16000
    ) OR (SELECT count(DISTINCT p) FROM unnest($3) p) <> cardinality($3) THEN
        RAISE EXCEPTION 'a reduction needs 1 to 8 distinct nonempty propositions' USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    anchor := require_lean_review($1, true);
    SELECT string_agg('(' || chr(10) || p || chr(10) || ')', ' → ' ORDER BY ordinal)
        INTO required
    FROM unnest($3 || ARRAY[anchor.body ->> 'theorem_type']) WITH ORDINALITY AS chain(p, ordinal);
    IF NOT EXISTS (
        SELECT 1 FROM local_lean_lemma_status(anchor.id)
        WHERE id = $2 AND status = 'verified' AND proposition = required
    ) THEN
        RAISE EXCEPTION 'the reduction requires a current, exact implication to the frozen goal'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_reductions(statement_id, implication_id, obligations) VALUES ($1, $2, $3)
    ON CONFLICT (implication_id) DO NOTHING;
    RETURN $2;
END $$;
REVOKE ALL ON FUNCTION rm.record_lean_reduction(uuid,uuid,text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_lean_reduction(uuid,uuid,text[]) TO rm_verifier;

CREATE TABLE rm.lean_reduction_results (
    implication_id uuid NOT NULL REFERENCES rm.lean_reductions(implication_id),
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    PRIMARY KEY (implication_id, evidence_id)
);
CREATE TRIGGER lean_reduction_results_append_only BEFORE UPDATE OR DELETE ON rm.lean_reduction_results
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
GRANT SELECT ON rm.lean_reduction_results TO rm_human, rm_verifier;

CREATE FUNCTION rm.record_lean_reduction_result(implication_id uuid, evidence_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM lean_reductions route
        JOIN evidence e ON e.id = $2 AND e.verdict = 'verified' AND e.diff_status = 'exact'
        JOIN lean_checks checked ON checked.id = e.lean_check_id AND checked.statement_id = route.statement_id
        WHERE route.implication_id = $1 AND checked.body ->> 'target' = 'goal'
          AND checked.body -> 'local_lemma_ids' ? $1::text
    ) THEN
        RAISE EXCEPTION 'a reduction result must use its checked implication in a proof of the frozen goal'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_reduction_results VALUES ($1, $2) ON CONFLICT DO NOTHING;
END $$;
REVOKE ALL ON FUNCTION rm.record_lean_reduction_result(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_lean_reduction_result(uuid,uuid) TO rm_verifier;

CREATE FUNCTION rm.lean_reduction_status(anchor_id uuid)
RETURNS TABLE (implication_id uuid, obligations jsonb, recorded_at timestamptz,
               evidence_id uuid, review_status text, stale_evidence_id uuid, status text)
LANGUAGE sql STABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH routes AS MATERIALIZED (
        SELECT route.* FROM lean_reductions route WHERE route.statement_id = $1
    ),
    lemmas AS MATERIALIZED (
        SELECT lemma.id, lemma.proposition, lemma.recorded_at, lemma.status
        FROM local_lean_lemma_status($1) lemma WHERE EXISTS (SELECT 1 FROM routes)
    ),
    checked AS (
        SELECT DISTINCT ON (lemma.proposition COLLATE "C") lemma.proposition, lemma.id
        FROM lemmas lemma WHERE lemma.status = 'verified'
        ORDER BY lemma.proposition COLLATE "C", lemma.recorded_at, lemma.id
    )
    SELECT route.implication_id, requirement.items, route.recorded_at,
           CASE WHEN NOT receipt.needs_recheck THEN receipt.id END,
           CASE WHEN NOT receipt.needs_recheck THEN receipt.review_status END,
           CASE WHEN receipt.needs_recheck THEN receipt.id END,
           CASE WHEN implication.status IS DISTINCT FROM 'verified' THEN 'needs_recheck'
                WHEN NOT receipt.needs_recheck THEN 'goal_checked'
                WHEN requirement.all_verified THEN 'ready'
                WHEN receipt.needs_recheck THEN 'needs_recheck'
                ELSE 'awaiting_obligations' END
    FROM routes route
    LEFT JOIN lemmas implication ON implication.id = route.implication_id
    CROSS JOIN LATERAL (
        SELECT jsonb_agg(jsonb_build_object('proposition', obligation.proposition, 'lemma_id', lemma.id)
                         ORDER BY obligation.ordinal) AS items,
               bool_and(lemma.id IS NOT NULL) AS all_verified
        FROM unnest(route.obligations) WITH ORDINALITY AS obligation(proposition, ordinal)
        LEFT JOIN checked lemma ON lemma.proposition COLLATE "C" = obligation.proposition COLLATE "C"
    ) requirement
    LEFT JOIN LATERAL (
        SELECT e.id, e.review_status, e.needs_recheck FROM lean_reduction_results result
        JOIN evidence_foundation_status e ON e.id = result.evidence_id
        WHERE result.implication_id = route.implication_id
        ORDER BY e.needs_recheck, (e.review_status = 'approved') DESC, e.id LIMIT 1
    ) receipt ON true
$$;

REVOKE ALL ON FUNCTION rm.lean_reduction_status(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_reduction_status(uuid) TO rm_human, rm_verifier;
