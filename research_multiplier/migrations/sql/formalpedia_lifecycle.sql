-- Source observations remain historically distinct. One frozen statement's
-- exact missing proof inputs require only one decision.
CREATE TABLE rm.formalpedia_application_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id uuid NOT NULL REFERENCES rm.lean_statements(id),
    obligations_sha256 rm.sha256_hex NOT NULL,
    obligations jsonb NOT NULL,
    candidate jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (statement_id, obligations_sha256)
);

INSERT INTO rm.formalpedia_application_reviews (id, statement_id, obligations_sha256, obligations, candidate, recorded_at)
SELECT DISTINCT ON (statement_id, digest) id, statement_id, digest, obligations, candidate, recorded_at
FROM (
    SELECT old_review.*, encode(sha256(convert_to(obligations::text, 'UTF8')), 'hex') AS digest
    FROM rm.formalpedia_basis_reviews old_review
    WHERE obligations <> '[]'::jsonb
) prior
ORDER BY statement_id, digest, recorded_at, id;

CREATE TABLE rm.formalpedia_goal_provenance (
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    candidate jsonb NOT NULL,
    provenance jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (evidence_id, candidate)
);
CREATE TRIGGER formalpedia_goal_provenance_append_only BEFORE UPDATE OR DELETE ON rm.formalpedia_goal_provenance
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.formalpedia_bound_candidate(candidate jsonb) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT formalpedia_candidate_identity($1 - ARRAY['statement_sha256','index_entry_sha256','solution_file_sha256'])
       AND coalesce(
            jsonb_typeof($1 -> 'statement_sha256') = 'string'
        AND jsonb_typeof($1 -> 'index_entry_sha256') = 'string'
        AND jsonb_typeof($1 -> 'solution_file_sha256') = 'string'
        AND $1 ->> 'statement_sha256' ~ '^[0-9a-f]{64}$'
        AND $1 ->> 'index_entry_sha256' ~ '^[0-9a-f]{64}$'
        AND $1 ->> 'solution_file_sha256' ~ '^[0-9a-f]{64}$', false)
$$;

CREATE FUNCTION rm.formalpedia_bound_source(candidate jsonb, provenance jsonb) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT formalpedia_bound_candidate($1) AND coalesce(
        jsonb_typeof($2) = 'object' AND octet_length($2::text) <= 64000
        AND lean_artifact_ref_valid($2 -> 'exported_statement')
        AND lean_artifact_ref_valid($2 -> 'exported_proof_body')
        AND $1 ->> 'statement_sha256' = $2 #>> '{exported_statement,sha256}'
        AND $1 ->> 'solution_sha256' = $2 #>> '{exported_proof_body,sha256}'
        AND $1 ->> 'index_entry_sha256' = $2 ->> 'index_entry_sha256'
        AND $1 ->> 'solution_file_sha256' = $2 ->> 'exported_solution_file_sha256', false)
$$;

CREATE FUNCTION rm.formalpedia_goal_is_verified(anchor_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM lean_checks checked JOIN evidence_foundation_status evidence ON evidence.lean_check_id = checked.id
        WHERE checked.statement_id = $1 AND evidence.kind = 'lean'
          AND evidence.verdict = 'verified' AND evidence.diff_status = 'exact'
          AND evidence.issuance_sha256 IS NOT NULL AND evidence.review_status <> 'returned' AND NOT evidence.needs_recheck
    )
$$;

CREATE FUNCTION rm.record_formalpedia_application_review(anchor_id uuid, proof_identity jsonb, missing jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE review_id uuid;
DECLARE normalized jsonb;
DECLARE digest rm.sha256_hex;
BEGIN
    PERFORM require_lean_review($1, true);
    IF NOT formalpedia_bound_candidate($2) OR jsonb_typeof($3) IS DISTINCT FROM 'array'
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
    digest := encode(sha256(convert_to(normalized::text, 'UTF8')), 'hex');
    INSERT INTO formalpedia_application_reviews (statement_id, obligations_sha256, obligations, candidate)
    VALUES ($1, digest, normalized, $2)
    ON CONFLICT (statement_id, obligations_sha256) DO NOTHING
    RETURNING id INTO review_id;
    IF NOT FOUND THEN
        SELECT review.id INTO STRICT review_id FROM formalpedia_application_reviews review
        WHERE review.statement_id = $1 AND review.obligations_sha256 = digest AND review.obligations = normalized;
    END IF;
    IF formalpedia_goal_is_verified($1) THEN RETURN NULL; END IF;
    RETURN review_id;
END $$;

-- Keep the selected source immutable. An optional equality audit may have been
-- omitted on an earlier equivalent plan and added when that plan was retried.
CREATE FUNCTION rm.record_formalpedia_checked_lemma(anchor_id uuid, lemma_id uuid, proof_identity jsonb, provenance jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_lean_review($1, true);
    IF NOT formalpedia_bound_source($3, $4)
        OR NOT EXISTS (SELECT FROM local_lean_lemmas lemma
                       WHERE lemma.id = $2 AND lemma.statement_id = $1 AND local_lean_lemma_source_live(lemma.id)) THEN
        RAISE EXCEPTION 'Formalpedia provenance requires a current verified lemma and exact source files in the same scope'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO formalpedia_lemma_provenance (lemma_id, candidate, provenance) VALUES ($2, $3, $4)
    ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT FROM formalpedia_lemma_provenance entry
                   WHERE entry.lemma_id = $2 AND entry.candidate = $3
                     AND entry.provenance - ARRAY['local_proposition','type_comparison_checker_sha256']
                         = $4 - ARRAY['local_proposition','type_comparison_checker_sha256']) THEN
        RAISE EXCEPTION 'The pinned source provenance differs from the previously recorded import'
            USING ERRCODE = '23514';
    END IF;
END $$;

CREATE FUNCTION rm.record_formalpedia_goal(anchor_id uuid, receipt_id uuid, proof_identity jsonb, provenance jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_lean_review($1, true);
    IF NOT formalpedia_bound_source($3, $4) OR NOT formalpedia_goal_resolution_live($1, $2) THEN
        RAISE EXCEPTION 'Formalpedia provenance requires a current exact frozen-goal receipt and exact source files'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO formalpedia_goal_provenance (evidence_id, candidate, provenance) VALUES ($2, $3, $4)
    ON CONFLICT DO NOTHING;
    IF NOT EXISTS (SELECT FROM formalpedia_goal_provenance entry
                   WHERE entry.evidence_id = $2 AND entry.candidate = $3
                     AND entry.provenance - ARRAY['local_proposition','type_comparison_checker_sha256']
                         = $4 - ARRAY['local_proposition','type_comparison_checker_sha256']) THEN
        RAISE EXCEPTION 'The pinned source provenance differs from the previously recorded import'
            USING ERRCODE = '23514';
    END IF;
END $$;

CREATE FUNCTION rm.formalpedia_checked_sources(anchor_id uuid)
RETURNS TABLE (lemma_id uuid, proposition text, status text, candidate jsonb, provenance jsonb,
    recorded_at timestamptz, evidence_id uuid, binding text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT lemma.lemma_id, lemma.proposition, lemma.status, lemma.candidate, lemma.provenance, lemma.recorded_at,
           NULL::uuid AS evidence_id,
           CASE WHEN formalpedia_bound_candidate(lemma.candidate)
                THEN 'source_files_bound' ELSE 'legacy_unbound' END AS binding
    FROM formalpedia_provenance($1) lemma
    UNION ALL
    SELECT NULL::uuid, statement.body ->> 'theorem_type',
           CASE WHEN formalpedia_goal_resolution_live($1, goal.evidence_id) THEN 'verified' ELSE 'needs_recheck' END,
           goal.candidate, goal.provenance, goal.recorded_at, goal.evidence_id, 'source_files_bound'
    FROM lean_statements statement
    JOIN lean_checks checked ON checked.statement_id = statement.id
    JOIN evidence receipt ON receipt.lean_check_id = checked.id
    JOIN formalpedia_goal_provenance goal ON goal.evidence_id = receipt.id
    WHERE statement.id = $1
    ORDER BY recorded_at, lemma_id, evidence_id, candidate;
$$;

CREATE OR REPLACE VIEW rm.research_actionable_review_inbox WITH (security_barrier = true) AS
    SELECT * FROM rm.research_actionable_review_before_formalpedia
    UNION ALL
    SELECT 'review_formalpedia_basis', review.id, statement.claim_id, review.recorded_at,
           'A Formalpedia proof has inputs still undischarged on this route. Review the exact inputs before using it.',
           jsonb_build_object('statement_id', statement.id, 'candidate', review.candidate, 'obligations', review.obligations)
    FROM rm.formalpedia_application_reviews review
    JOIN rm.lean_statement_status statement ON statement.id = review.statement_id AND statement.check_ready
    WHERE NOT rm.formalpedia_goal_is_verified(statement.id);

REVOKE ALL ON rm.formalpedia_application_reviews, rm.formalpedia_goal_provenance FROM PUBLIC;
REVOKE ALL ON FUNCTION rm.formalpedia_bound_candidate(jsonb), rm.formalpedia_bound_source(jsonb,jsonb),
    rm.formalpedia_goal_is_verified(uuid), rm.record_formalpedia_application_review(uuid,jsonb,jsonb),
    rm.record_formalpedia_checked_lemma(uuid,uuid,jsonb,jsonb), rm.record_formalpedia_goal(uuid,uuid,jsonb,jsonb),
    rm.formalpedia_checked_sources(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_formalpedia_application_review(uuid,jsonb,jsonb),
    rm.record_formalpedia_checked_lemma(uuid,uuid,jsonb,jsonb), rm.record_formalpedia_goal(uuid,uuid,jsonb,jsonb) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.formalpedia_goal_is_verified(uuid), rm.formalpedia_checked_sources(uuid) TO rm_human;
