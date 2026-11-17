-- A candidate remains optional. Its missing basis does not reopen or block
-- independent proofs of the original frozen claim.
CREATE TABLE rm.formalpedia_basis_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id uuid NOT NULL REFERENCES rm.lean_statements(id),
    candidate jsonb NOT NULL,
    obligations jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolution_evidence_id uuid REFERENCES rm.evidence(id),
    resolved_at timestamptz,
    UNIQUE (statement_id, candidate)
);

CREATE TABLE rm.formalpedia_lemma_provenance (
    lemma_id uuid NOT NULL REFERENCES rm.local_lean_lemmas(id),
    candidate jsonb NOT NULL,
    provenance jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (lemma_id, candidate)
);
CREATE TRIGGER formalpedia_lemma_provenance_append_only BEFORE UPDATE OR DELETE ON rm.formalpedia_lemma_provenance
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.formalpedia_candidate_identity(candidate jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT coalesce(jsonb_typeof($1) = 'object' AND $1 - ARRAY[
            'snapshot', 'environment', 'theorem', 'solution_sha256'] = '{}'::jsonb
        AND jsonb_typeof($1 -> 'snapshot') = 'string' AND $1 ->> 'snapshot' ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'
        AND jsonb_typeof($1 -> 'environment') = 'string' AND $1 ->> 'environment' ~ '^[0-9a-f]{7,64}$'
        AND jsonb_typeof($1 -> 'theorem') = 'string' AND length($1 ->> 'theorem') BETWEEN 1 AND 512
        AND $1 ->> 'theorem' = btrim($1 ->> 'theorem')
        AND jsonb_typeof($1 -> 'solution_sha256') = 'string' AND $1 ->> 'solution_sha256' ~ '^[0-9a-f]{64}$', false)
$$;

CREATE FUNCTION rm.formalpedia_goal_resolution_live(anchor_id uuid, receipt_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM evidence_foundation_status evidence
        JOIN lean_checks checked ON checked.id = evidence.lean_check_id
        WHERE evidence.id = $2 AND checked.statement_id = $1 AND evidence.kind = 'lean'
          AND evidence.verdict = 'verified' AND evidence.diff_status = 'exact'
          AND evidence.issuance_sha256 IS NOT NULL AND evidence.review_status <> 'returned' AND NOT evidence.needs_recheck
    )
$$;

CREATE FUNCTION rm.record_formalpedia_basis_review(anchor_id uuid, proof_identity jsonb, missing jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE review_id uuid;
DECLARE normalized jsonb;
BEGIN
    PERFORM require_lean_review($1, true);
    IF NOT formalpedia_candidate_identity($2) OR jsonb_typeof($3) IS DISTINCT FROM 'array'
        OR octet_length($3::text) > 64000 OR jsonb_array_length($3) NOT BETWEEN 1 AND 64
        OR EXISTS (
            SELECT FROM jsonb_array_elements($3) item(value)
            WHERE NOT coalesce(CASE value ->> 'kind'
                WHEN 'premise' THEN value - ARRAY['kind','proposition'] = '{}'::jsonb
                    AND jsonb_typeof(value -> 'proposition') = 'string'
                    AND length(btrim(value ->> 'proposition')) BETWEEN 1 AND 16000
                WHEN 'declaration' THEN value - ARRAY['kind','declaration','proposition'] = '{}'::jsonb
                    AND jsonb_typeof(value -> 'declaration') = 'string'
                    AND length(btrim(value ->> 'declaration')) BETWEEN 1 AND 512
                    AND (NOT value ? 'proposition' OR (jsonb_typeof(value -> 'proposition') = 'string'
                        AND length(btrim(value ->> 'proposition')) BETWEEN 1 AND 16000))
                ELSE false END, false)
        ) THEN
        RAISE EXCEPTION 'Formalpedia review requires a pinned candidate and exact missing proof inputs'
            USING ERRCODE = '22023';
    END IF;
    SELECT jsonb_agg(item ORDER BY item) INTO normalized
    FROM (SELECT DISTINCT item FROM jsonb_array_elements($3) entry(item)) distinct_items;
    INSERT INTO formalpedia_basis_reviews AS review (statement_id, candidate, obligations)
    VALUES ($1, $2, normalized)
    ON CONFLICT (statement_id, candidate) DO UPDATE SET obligations = EXCLUDED.obligations,
        resolution_evidence_id = NULL, resolved_at = NULL
    WHERE (review.resolution_evidence_id IS NULL AND review.obligations IS DISTINCT FROM EXCLUDED.obligations)
       OR (review.resolution_evidence_id IS NOT NULL
           AND NOT formalpedia_goal_resolution_live($1, review.resolution_evidence_id))
    RETURNING id INTO review_id;
    IF NOT FOUND THEN
        SELECT review.id INTO review_id FROM formalpedia_basis_reviews review
        WHERE review.statement_id = $1 AND review.candidate = $2 AND review.resolution_evidence_id IS NULL;
    END IF;
    RETURN review_id;
END $$;

-- Successful checks win over stale duplicate failures for the same scope and
-- exact source. Preserve existing observations as history.
CREATE FUNCTION rm.settle_formalpedia_basis_review(anchor_id uuid, proof_identity jsonb, receipt_id uuid) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, rm, pg_temp AS $$
    INSERT INTO formalpedia_basis_reviews AS review (statement_id, candidate, obligations, resolution_evidence_id, resolved_at)
    VALUES ($1, $2, '[]'::jsonb, $3, clock_timestamp())
    ON CONFLICT (statement_id, candidate) DO UPDATE SET resolution_evidence_id = EXCLUDED.resolution_evidence_id,
        resolved_at = EXCLUDED.resolved_at
    WHERE review.resolution_evidence_id IS DISTINCT FROM EXCLUDED.resolution_evidence_id;
$$;

CREATE FUNCTION rm.record_formalpedia_lemma(anchor_id uuid, lemma_id uuid, proof_identity jsonb, provenance jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_lean_review($1, true);
    IF NOT formalpedia_candidate_identity($3) OR jsonb_typeof($4) IS DISTINCT FROM 'object'
        OR $4 = '{}'::jsonb OR octet_length($4::text) > 64000
        OR NOT EXISTS (SELECT FROM local_lean_lemmas lemma
                       WHERE lemma.id = $2 AND lemma.statement_id = $1 AND local_lean_lemma_source_live(lemma.id)) THEN
        RAISE EXCEPTION 'Formalpedia provenance requires a current verified lemma and pinned source in the same scope'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO formalpedia_lemma_provenance (lemma_id, candidate, provenance) VALUES ($2, $3, $4)
    ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT FROM formalpedia_lemma_provenance entry
                   WHERE entry.lemma_id = $2 AND entry.candidate = $3 AND entry.provenance = $4) THEN
        RAISE EXCEPTION 'The pinned source provenance differs from the previously recorded import'
            USING ERRCODE = '23514';
    END IF;
END $$;

CREATE FUNCTION rm.resolve_formalpedia_basis_review(anchor_id uuid, proof_identity jsonb, evidence_id uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_lean_review($1, true);
    IF NOT formalpedia_candidate_identity($2) OR NOT formalpedia_goal_resolution_live($1, $3) THEN
        RAISE EXCEPTION 'The Formalpedia candidate needs an exact current proof of the frozen statement'
            USING ERRCODE = '23514';
    END IF;
    PERFORM settle_formalpedia_basis_review($1, $2, $3);
END $$;

CREATE FUNCTION rm.formalpedia_provenance(anchor_id uuid)
RETURNS TABLE (lemma_id uuid, proposition text, status text, candidate jsonb, provenance jsonb, recorded_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT lemma.id, lemma.proposition, lemma.status, entry.candidate, entry.provenance, entry.recorded_at
    FROM local_lean_lemma_status($1) lemma
    JOIN formalpedia_lemma_provenance entry ON entry.lemma_id = lemma.id
    ORDER BY entry.recorded_at, lemma.id, entry.candidate;
$$;

ALTER VIEW rm.research_actionable_review_inbox RENAME TO research_actionable_review_before_formalpedia;
CREATE VIEW rm.research_actionable_review_inbox WITH (security_barrier = true) AS
    SELECT * FROM rm.research_actionable_review_before_formalpedia
    UNION ALL
    SELECT 'review_formalpedia_basis', review.id, statement.claim_id, review.recorded_at,
           'A Formalpedia proof has inputs still undischarged on this route. Review the exact inputs before using it.',
           jsonb_build_object('statement_id', statement.id, 'candidate', review.candidate, 'obligations', review.obligations)
    FROM rm.formalpedia_basis_reviews review
    JOIN rm.lean_statement_status statement ON statement.id = review.statement_id AND statement.check_ready
    JOIN rm.claims claim ON claim.id = statement.claim_id AND claim.status = 'open'
    WHERE review.resolution_evidence_id IS NULL;

REVOKE ALL ON rm.formalpedia_basis_reviews, rm.formalpedia_lemma_provenance,
    rm.research_actionable_review_inbox FROM PUBLIC;
REVOKE ALL ON FUNCTION rm.formalpedia_candidate_identity(jsonb), rm.settle_formalpedia_basis_review(uuid,jsonb,uuid),
    rm.formalpedia_goal_resolution_live(uuid,uuid),
    rm.record_formalpedia_basis_review(uuid,jsonb,jsonb), rm.record_formalpedia_lemma(uuid,uuid,jsonb,jsonb),
    rm.resolve_formalpedia_basis_review(uuid,jsonb,uuid), rm.formalpedia_provenance(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_formalpedia_basis_review(uuid,jsonb,jsonb),
    rm.record_formalpedia_lemma(uuid,uuid,jsonb,jsonb), rm.resolve_formalpedia_basis_review(uuid,jsonb,uuid) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.formalpedia_provenance(uuid) TO rm_human;
GRANT SELECT ON rm.research_actionable_review_inbox TO rm_human;
