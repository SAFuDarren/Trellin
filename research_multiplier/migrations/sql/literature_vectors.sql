CREATE TABLE rm.literature_vector_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model text NOT NULL CHECK (btrim(model) <> '' AND length(model) <= 200),
    model_revision text NOT NULL CHECK (btrim(model_revision) <> '' AND length(model_revision) <= 200),
    dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 2000),
    UNIQUE (model, model_revision, dimensions),
    UNIQUE (id, dimensions)
);

CREATE TABLE rm.literature_vectors (
    profile_id uuid NOT NULL,
    dimensions integer NOT NULL,
    kind text NOT NULL CHECK (kind IN ('source','claim','basis','evidence','failure')),
    document_id uuid NOT NULL,
    content_sha256 rm.sha256_hex NOT NULL,
    input_vector_sha256 rm.sha256_hex NOT NULL,
    embedding public.vector NOT NULL,
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (profile_id, kind, document_id, content_sha256),
    FOREIGN KEY (profile_id, dimensions) REFERENCES rm.literature_vector_profiles(id, dimensions),
    CHECK (public.vector_dims(embedding) = dimensions AND public.vector_norm(embedding) > 0)
);

-- The reference is bound to the exact canonical text exported for indexing and
-- the source version. Recompute eligibility when reading rather than allowing
-- historical embeddings into answers after their proof or source is retired.
CREATE VIEW rm.literature_embedding_documents WITH (security_barrier = true) AS
    WITH documents(kind, document_id, claim_id, version_sha256, title, input) AS (
        SELECT 'source'::text, s.id, s.claim_id, s.source_sha256::text, s.body ->> 'title',
            jsonb_build_object('identifier', s.body -> 'identifier', 'title', s.body -> 'title',
                'locator', s.body -> 'locator', 'excerpt', s.body -> 'excerpt',
                'conventions', s.body -> 'convention_map')::text
        FROM rm.literature_source_status s JOIN rm.claim_foundation_status status ON status.id = s.claim_id
        WHERE s.confirmed_at IS NOT NULL AND NOT s.retired AND status.status <> 'needs_recheck'
        UNION ALL
        SELECT 'claim', c.id, c.id, c.contract_sha256::text, c.title, c.contract_bytes
        FROM rm.claims c JOIN rm.claim_foundation_status s ON s.id = c.id
        WHERE c.frozen_at IS NOT NULL AND s.status <> 'needs_recheck'
        UNION ALL
        SELECT 'basis', i.id, NULL::uuid, i.version_sha256::text, i.body ->> 'natural', i.body::text
        FROM rm.formal_item_status i
        WHERE i.admitted AND NOT i.needs_recheck AND i.body ->> 'kind' IN ('definition','hypothesis','theorem')
        UNION ALL
        SELECT 'evidence', e.id, e.claim_id, e.issuance_sha256::text, c.title,
            jsonb_build_object('claim', c.statement, 'kind', e.kind, 'result', e.result)::text
        FROM rm.evidence_foundation_status e
        JOIN rm.claims c ON c.id = e.claim_id
        WHERE e.issuance_sha256 IS NOT NULL AND e.verdict = 'verified' AND e.diff_status = 'exact'
            AND e.review_status = 'approved' AND NOT e.needs_recheck
        UNION ALL
        SELECT 'failure', attempt.id, c.id, c.contract_sha256::text, c.title,
            jsonb_build_object('claim', c.statement, 'role', attempt.role,
                'finding', attempt.finding, 'reason', attempt.failure_reason)::text
        FROM rm.attempts attempt
        JOIN rm.claims c ON (c.id, c.contract_sha256) = (attempt.claim_id, attempt.contract_sha256)
        JOIN rm.claim_foundation_status s ON s.id = c.id
        WHERE attempt.finding = 'no_result' AND attempt.failure_reason IS NOT NULL
          AND s.status <> 'needs_recheck'
    )
    SELECT documents.*,
        encode(sha256(convert_to(jsonb_build_object('kind', kind, 'id', document_id,
            'version_sha256', version_sha256, 'input', input)::text, 'UTF8')), 'hex') AS content_sha256
    FROM documents;

CREATE FUNCTION rm.import_literature_vectors(model text, model_revision text, dimensions integer, entries jsonb)
RETURNS TABLE (profile_id uuid, document_count integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE profile uuid;
DECLARE entry jsonb;
DECLARE embedding_value public.vector;
DECLARE vector_hash text;
DECLARE doc_id uuid;
DECLARE doc_kind text;
DECLARE doc_hash text;
BEGIN
    IF model IS NULL OR nullif(btrim(model), '') IS NULL OR length(model) > 200
        OR model_revision IS NULL OR nullif(btrim(model_revision), '') IS NULL OR length(model_revision) > 200
        OR dimensions IS NULL OR dimensions NOT BETWEEN 1 AND 2000
        OR jsonb_typeof(entries) IS DISTINCT FROM 'array' OR pg_column_size(entries) > 4194304 THEN
        RAISE EXCEPTION 'vector import needs a model, revision, dimensions and bounded entries' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(entries) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'import between 1 and 100 document vectors per batch' USING ERRCODE = '22023';
    END IF;
    PERFORM lock_live_foundation();
    INSERT INTO literature_vector_profiles (model, model_revision, dimensions) VALUES ($1, $2, $3)
        ON CONFLICT ON CONSTRAINT literature_vector_profiles_model_model_revision_dimensions_key DO NOTHING
        RETURNING id INTO profile;
    IF NOT FOUND THEN
        SELECT p.id INTO STRICT profile FROM literature_vector_profiles p
        WHERE (p.model, p.model_revision, p.dimensions) = ($1, $2, $3);
    END IF;
    FOR entry IN SELECT value FROM jsonb_array_elements(entries) LOOP
        IF jsonb_typeof(entry) IS DISTINCT FROM 'object'
            OR entry - ARRAY['kind','id','content_sha256','vector'] <> '{}'::jsonb
            OR coalesce(entry ->> 'kind', '') NOT IN ('source','claim','basis','evidence','failure')
            OR NOT coalesce(entry ->> 'id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', false)
            OR NOT coalesce(entry ->> 'content_sha256' ~ '^[0-9a-f]{64}$', false)
            OR jsonb_typeof(entry -> 'vector') IS DISTINCT FROM 'array' THEN
            RAISE EXCEPTION 'vector entries must identify an exact eligible document' USING ERRCODE = '22023';
        END IF;
        doc_id := (entry ->> 'id')::uuid;
        doc_kind := entry ->> 'kind';
        doc_hash := entry ->> 'content_sha256';
        IF NOT EXISTS (
            SELECT FROM literature_embedding_documents d
            WHERE (d.kind, d.document_id, d.content_sha256) = (doc_kind, doc_id, doc_hash)
        ) THEN
            RAISE EXCEPTION 'document % is stale, ineligible or does not match the exported content hash', doc_id
                USING ERRCODE = '23514';
        END IF;
        embedding_value := (entry -> 'vector')::text::public.vector;
        IF public.vector_dims(embedding_value) <> $3 OR public.vector_norm(embedding_value) <= 0 THEN
            RAISE EXCEPTION 'embedding dimensions must match the profile and its vector must be nonzero'
                USING ERRCODE = '22023';
        END IF;
        vector_hash := encode(sha256(convert_to(embedding_value::text, 'UTF8')), 'hex');
        INSERT INTO literature_vectors (
            profile_id, dimensions, kind, document_id, content_sha256, input_vector_sha256, embedding
        ) VALUES (profile, $3, doc_kind, doc_id, doc_hash, vector_hash, public.l2_normalize(embedding_value))
        ON CONFLICT ON CONSTRAINT literature_vectors_pkey DO NOTHING;
        IF NOT FOUND AND NOT EXISTS (
            SELECT FROM literature_vectors v WHERE (v.profile_id, v.kind, v.document_id, v.content_sha256)
                = (profile, doc_kind, doc_id, doc_hash) AND v.input_vector_sha256 = vector_hash
        ) THEN
            RAISE EXCEPTION 'this model revision already has a different vector for the document'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN QUERY SELECT profile, jsonb_array_length(entries);
END $$;

CREATE FUNCTION rm.search_literature_vectors(model text, model_revision text, dimensions integer,
    query_vector jsonb, result_limit integer DEFAULT 20, document_kind text DEFAULT NULL)
RETURNS TABLE (kind text, document_id uuid, claim_id uuid, version_sha256 text,
    title text, content_sha256 text, similarity double precision)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE profile uuid;
DECLARE probe public.vector;
BEGIN
    IF jsonb_typeof(query_vector) IS DISTINCT FROM 'array' OR result_limit IS NULL
        OR result_limit NOT BETWEEN 1 AND 100
        OR (document_kind IS NOT NULL AND document_kind NOT IN ('source','claim','basis','evidence','failure')) THEN
        RAISE EXCEPTION 'supply a vector, valid document kind and a result limit from 1 to 100' USING ERRCODE = '22023';
    END IF;
    SELECT p.id INTO profile FROM literature_vector_profiles p
        WHERE (p.model, p.model_revision, p.dimensions) = ($1, $2, $3);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no embedding profile matches this model, revision and dimensions' USING ERRCODE = '23514';
    END IF;
    IF jsonb_array_length(query_vector) <> $3 OR pg_column_size(query_vector) > 100000 THEN
        RAISE EXCEPTION 'query vector does not match the profile dimensions' USING ERRCODE = '22023';
    END IF;
    probe := query_vector::text::public.vector;
    IF public.vector_norm(probe) <= 0 THEN
        RAISE EXCEPTION 'a query vector must be nonzero' USING ERRCODE = '22023';
    END IF;
    probe := public.l2_normalize(probe);
    -- The primary key restricts the scan to one model profile. Exact pgvector
    -- ranking avoids approximate retrieval losses when current evidence is filtered.
    RETURN QUERY
        SELECT d.kind, d.document_id, d.claim_id, d.version_sha256, d.title, d.content_sha256,
            1.0 - public.cosine_distance(v.embedding, probe)
        FROM literature_vectors v JOIN literature_embedding_documents d
            ON (d.kind, d.document_id, d.content_sha256) = (v.kind, v.document_id, v.content_sha256)
        WHERE v.profile_id = profile AND (document_kind IS NULL OR v.kind = document_kind)
        ORDER BY public.cosine_distance(v.embedding, probe), d.kind, d.document_id
        LIMIT result_limit;
END $$;

CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.literature_vector_profiles
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON rm.literature_vectors
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
REVOKE EXECUTE ON FUNCTION rm.import_literature_vectors(text, text, integer, jsonb),
    rm.search_literature_vectors(text, text, integer, jsonb, integer, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.import_literature_vectors(text, text, integer, jsonb),
    rm.search_literature_vectors(text, text, integer, jsonb, integer, text) TO rm_human;
GRANT SELECT ON rm.literature_embedding_documents, rm.literature_vector_profiles TO rm_human;
