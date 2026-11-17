ALTER TABLE rm.literature_search_hits DROP CONSTRAINT literature_search_hits_identifier_check;
ALTER TABLE rm.literature_search_hits ADD CONSTRAINT literature_search_hit_versioned_id CHECK (
    identifier ~ '^(arXiv:([0-9]{4}[.][0-9]{4,5}|[a-zA-Z.-]+/[0-9]{7})v[1-9][0-9]*|doi:10[.][0-9]{4,9}/[^[:space:][:cntrl:]]+)$'
);

CREATE FUNCTION rm.import_literature_search(claim_id uuid, expected_sha text, body jsonb, identifiers text[])
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved uuid;
BEGIN
    IF NOT EXISTS (SELECT FROM claims c WHERE c.id = $1 AND c.contract_sha256 = $2 AND c.frozen_at IS NOT NULL) THEN
        RAISE EXCEPTION 'import against the exact frozen claim' USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['query','searched_in','response'] <> '{}'::jsonb
        OR jsonb_typeof(body -> 'query') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'query'), '') IS NULL OR length(body ->> 'query') > 4000
        OR jsonb_typeof(body -> 'searched_in') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'searched_in'), '') IS NULL OR length(body ->> 'searched_in') > 1000
        OR NOT lean_artifact_ref_valid(body -> 'response')
        OR identifiers IS NULL OR cardinality(identifiers) > 50
        OR array_position(identifiers, NULL) IS NOT NULL
        OR cardinality(identifiers) <> (SELECT count(DISTINCT item) FROM unnest(identifiers) item)
        OR EXISTS (SELECT FROM unnest(identifiers) item WHERE length(item) > 1000
                   OR (item LIKE 'doi:%' AND item <> lower(item))) THEN
        RAISE EXCEPTION 'local literature search requires archived scope and unique versioned identifiers' USING ERRCODE = '22023';
    END IF;
    INSERT INTO literature_searches (claim_id, contract_sha256, body)
        VALUES ($1, $2, body || jsonb_build_object('provider','manual','status','complete')) RETURNING id INTO saved;
    INSERT INTO literature_search_hits (search_id, rank, identifier, body)
        SELECT saved, hit.rank::integer, hit.item, jsonb_build_object('identifier', hit.item)
        FROM unnest(identifiers) WITH ORDINALITY hit(item, rank);
    RETURN saved;
END $$;

CREATE TABLE rm.novelty_review_searches (
    review_id uuid NOT NULL REFERENCES rm.novelty_reviews(id),
    search_id uuid NOT NULL REFERENCES rm.literature_searches(id),
    PRIMARY KEY (review_id, search_id)
);

CREATE TABLE rm.novelty_review_originals (
    review_id uuid NOT NULL,
    search_id uuid NOT NULL,
    rank integer NOT NULL,
    source_id uuid NOT NULL,
    PRIMARY KEY (review_id, search_id, rank, source_id),
    FOREIGN KEY (review_id, search_id) REFERENCES rm.novelty_review_searches(review_id, search_id),
    FOREIGN KEY (search_id, rank, source_id) REFERENCES rm.literature_search_links(search_id, rank, source_id)
);

CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.novelty_review_searches
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.novelty_review_originals
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.require_novelty_searches() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE refs jsonb := coalesce(NEW.report -> 'search_ids', '[]'::jsonb);
DECLARE selected uuid[];
BEGIN
    IF jsonb_typeof(refs) IS DISTINCT FROM 'array' OR jsonb_array_length(refs) > 50 THEN
        RAISE EXCEPTION 'search_ids must be an array of up to 50 recorded search IDs' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT FROM jsonb_array_elements_text(refs) item WHERE item IS NULL
        OR item !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
        OR jsonb_array_length(refs) <> (SELECT count(DISTINCT item) FROM jsonb_array_elements_text(refs) item) THEN
        RAISE EXCEPTION 'search_ids must contain unique canonical UUIDs' USING ERRCODE = '22023';
    END IF;
    SELECT ARRAY(SELECT item::uuid FROM jsonb_array_elements_text(refs) item) INTO selected;
    IF NEW.assessment = 'distinct' AND cardinality(selected) = 0 THEN
        RAISE EXCEPTION 'a distinct result requires completed searches on the exact frozen claim' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT FROM unnest(selected) picked(search_id) WHERE NOT EXISTS (
            SELECT FROM literature_searches search
            JOIN claims claim ON (claim.id, claim.contract_sha256) = (search.claim_id, search.contract_sha256)
            WHERE search.id = picked.search_id AND claim.id = NEW.claim_id AND search.body ->> 'status' = 'complete'
              AND NOT EXISTS (SELECT FROM verification_input_retirements retirement
                  WHERE retirement.input_kind = 'source' AND retirement.sha256 = search.body #>> '{response,sha256}')
        )
    ) THEN
        RAISE EXCEPTION 'each selected search must be complete, current and scoped to this exact frozen claim'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO novelty_review_searches (review_id, search_id)
        SELECT NEW.id, picked.search_id FROM unnest(selected) picked(search_id);
    INSERT INTO novelty_review_originals (review_id, search_id, rank, source_id)
        SELECT NEW.id, link.search_id, link.rank, link.source_id FROM literature_search_links link
        WHERE link.search_id = ANY(selected) AND link.source_id = ANY(NEW.source_ids);
    IF NEW.assessment = 'distinct' AND EXISTS (
        SELECT FROM unnest(NEW.source_ids) cited(source_id) WHERE NOT EXISTS (
            SELECT FROM novelty_review_originals link WHERE link.review_id = NEW.id AND link.source_id = cited.source_id
        )
    ) THEN
        RAISE EXCEPTION 'link every compared original and its confirmed locator to one of the selected search hits'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER require_searches AFTER INSERT ON rm.novelty_reviews
    FOR EACH ROW EXECUTE FUNCTION rm.require_novelty_searches();

CREATE OR REPLACE VIEW rm.novelty_review_status WITH (security_barrier = true) AS
    SELECT c.id AS claim_id, review.id AS review_id, review.evidence_id, review.assessment,
        review.source_ids, review.report, review.actor_ref, review.reason, review.recorded_at,
        CASE
            WHEN review.id IS NULL OR review.assessment = 'not_found' THEN 'possibly_known'
            WHEN EXISTS (
                SELECT FROM rm.literature_source_status source WHERE source.id = ANY(review.source_ids) AND source.retired
            ) OR EXISTS (
                SELECT FROM rm.verification_input_retirements retirement
                JOIN rm.literature_searches search ON retirement.sha256 = search.body #>> '{response,sha256}'
                JOIN rm.novelty_review_searches link ON link.search_id = search.id
                WHERE retirement.input_kind = 'source' AND link.review_id = review.id
            ) THEN 'needs_review'
            WHEN review.assessment = 'known' THEN 'known'
            WHEN evidence.needs_recheck OR evidence.review_status IS DISTINCT FROM 'approved'
                OR NOT EXISTS (SELECT FROM rm.novelty_review_searches link WHERE link.review_id = review.id)
                THEN 'needs_review'
            ELSE 'human_cleared'
        END AS status
    FROM rm.claims c
    LEFT JOIN LATERAL (
        SELECT * FROM rm.novelty_reviews n WHERE n.claim_id = c.id ORDER BY n.sequence_no DESC LIMIT 1
    ) review ON true
    LEFT JOIN rm.evidence_foundation_status evidence ON evidence.id = review.evidence_id
    WHERE c.frozen_at IS NOT NULL;

REVOKE EXECUTE ON FUNCTION rm.import_literature_search(uuid,text,jsonb,text[]), rm.require_novelty_searches() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.import_literature_search(uuid,text,jsonb,text[]) TO rm_human;
GRANT SELECT ON rm.novelty_review_searches, rm.novelty_review_originals TO rm_human;

CREATE FUNCTION rm.exact_claim_scope(contract jsonb) RETURNS bytea
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
    SELECT convert_to((
        (contract - ARRAY['claim_id','title','origin','sensitivity']) ||
        jsonb_build_object('formal_skeleton',
            CASE WHEN contract #>> '{formal_skeleton,format}' = 'rm-goal-v1'
            THEN (contract -> 'formal_skeleton') - ARRAY['goal_id','goal_sha256','candidate_id','candidate_sha256']
            ELSE contract -> 'formal_skeleton' END)
    )::text, 'UTF8')
$$;

CREATE INDEX frozen_claim_exact_scope ON rm.claims (
    domain, sha256(rm.exact_claim_scope(contract_snapshot))
) WHERE frozen_at IS NOT NULL;
