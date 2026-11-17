-- Native submissions use the reviewed Core statement and ordinary exact-proof
-- admission. The source audit independently binds the raw artifact, method
-- and complete declaration/version map before the verifier can lease work.
ALTER FUNCTION rm.require_dynamic_lean_job(rm.jobs) RENAME TO require_prior_source_lean_job;
CREATE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE source jsonb := job.payload -> 'lean_source';
DECLARE mappings jsonb;
DECLARE versions jsonb;
BEGIN
    anchor := require_prior_source_lean_job(job);
    IF NOT (job.payload ? 'lean_source') THEN
        RETURN anchor;
    END IF;
    IF anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR job.payload ->> 'target' IS DISTINCT FROM 'goal'
        OR job.budget ->> 'max_model_calls' IS DISTINCT FROM '0'
        OR job.payload ?| ARRAY['local_lemma_ids','local_checker_revision','refutation_source_sha256']
        OR jsonb_typeof(source) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'a native Lean source job needs a reviewed Core goal and a deterministic verifier'
            USING ERRCODE = '23514';
    END IF;
    IF NOT source ?& ARRAY['mode','module','declaration','verifier_revision','candidate_sha256','approved_declarations']
        OR source - ARRAY['mode','module','declaration','verifier_revision','candidate_sha256','approved_declarations']
            <> '{}'::jsonb
        OR jsonb_typeof(source -> 'mode') IS DISTINCT FROM 'string'
        OR (source ->> 'mode') NOT IN ('file', 'module')
        OR jsonb_typeof(source -> 'module') IS DISTINCT FROM 'string'
        OR jsonb_typeof(source -> 'declaration') IS DISTINCT FROM 'string'
        OR nullif(btrim(source ->> 'declaration'), '') IS NULL
        OR (source ->> 'mode' = 'file' AND source ->> 'module' IS DISTINCT FROM 'RMSubmitted')
        OR (source ->> 'mode' = 'module' AND
            NOT coalesce(anchor.body #> '{environment,external,modules}' ? (source ->> 'module'), false))
        OR jsonb_typeof(source -> 'verifier_revision') IS DISTINCT FROM 'string'
        OR jsonb_typeof(source -> 'candidate_sha256') IS DISTINCT FROM 'string'
        OR NOT coalesce(source ->> 'verifier_revision' ~ '^[0-9a-f]{64}$', false)
        OR NOT coalesce(source ->> 'candidate_sha256' ~ '^[0-9a-f]{64}$', false)
        OR NOT lean_artifact_ref_valid(job.payload -> 'submission')
        OR source ->> 'candidate_sha256' IS DISTINCT FROM job.payload #>> '{submission,sha256}'
        OR jsonb_typeof(source -> 'approved_declarations') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'native Lean verification must bind its exact submission, declaration and verifier revision'
            USING ERRCODE = '23514';
    END IF;

    mappings := source -> 'approved_declarations';
    IF EXISTS (
        SELECT FROM jsonb_array_elements(mappings) AS mapping(entry)
        WHERE CASE WHEN jsonb_typeof(entry) = 'object' THEN
            NOT entry ?& ARRAY['version_id','declaration']
            OR entry - ARRAY['version_id','declaration'] <> '{}'::jsonb
            OR NOT coalesce(entry ->> 'version_id' ~
                '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', false)
            OR jsonb_typeof(entry -> 'declaration') IS DISTINCT FROM 'string'
            OR nullif(btrim(entry ->> 'declaration'), '') IS NULL
        ELSE true END
    ) OR (SELECT count(DISTINCT entry ->> 'version_id') FROM jsonb_array_elements(mappings) AS mapping(entry))
            <> jsonb_array_length(mappings)
      OR (SELECT count(DISTINCT (entry ->> 'declaration') COLLATE "C")
          FROM jsonb_array_elements(mappings) AS mapping(entry)) <> jsonb_array_length(mappings) THEN
        RAISE EXCEPTION 'native Lean verification needs unique exact declaration and version mappings' USING ERRCODE = '23514';
    END IF;
    SELECT coalesce(jsonb_agg(entry -> 'version_id' ORDER BY entry -> 'version_id'), '[]'::jsonb)
    INTO versions FROM jsonb_array_elements(mappings) AS mapping(entry);
    IF job.payload -> 'basis_version_ids' IS DISTINCT FROM versions OR EXISTS (
        SELECT FROM jsonb_array_elements(mappings) AS mapping(entry)
        LEFT JOIN get_packet_basis(anchor.packet_id) item ON item.item_version_id::text = entry ->> 'version_id'
        WHERE item.body ->> 'kind' IS DISTINCT FROM 'theorem'
           OR (item.body #>> '{formal,lean,declaration}') COLLATE "C" IS DISTINCT FROM
                (entry ->> 'declaration') COLLATE "C"
    ) THEN
        RAISE EXCEPTION 'native Lean declaration mappings must use the current reviewed basis' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (SELECT FROM verification_input_retirements retired
        WHERE (retired.input_kind, retired.sha256::text) IN (
            ('verifier', source ->> 'verifier_revision'), ('source', source ->> 'candidate_sha256')
        )) THEN
        RAISE EXCEPTION 'native Lean verification input requires recheck' USING ERRCODE = '23514';
    END IF;
    RETURN anchor;
END $$;
REVOKE ALL ON FUNCTION rm.require_dynamic_lean_job(rm.jobs) FROM PUBLIC;

CREATE FUNCTION rm.guard_unanchored_lean_source_job() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    RAISE EXCEPTION 'native Lean source metadata belongs to an anchored verification job' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER guard_unanchored_lean_source_job BEFORE INSERT ON rm.jobs
    FOR EACH ROW WHEN (NEW.payload ? 'lean_source' AND (NEW.kind <> 'verify' OR NOT NEW.payload ? 'lean_statement_id'))
    EXECUTE FUNCTION rm.guard_unanchored_lean_source_job();
REVOKE ALL ON FUNCTION rm.guard_unanchored_lean_source_job() FROM PUBLIC;

-- Compare actual constant names reported by Lean, then let ordinary
-- registration record every mapped version in the checked proof and its type.
CREATE FUNCTION rm.guard_lean_source_receipt() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE source jsonb;
DECLARE direct_constants jsonb := NEW.body #> '{dependencies,direct_constants}';
DECLARE constants jsonb := NEW.body #> '{dependencies,constants}';
DECLARE direct_versions jsonb;
DECLARE actual_versions jsonb;
BEGIN
    IF NEW.body ? 'verification_attempt_id' THEN
        SELECT j.payload -> 'lean_source' INTO source FROM attempts a
        JOIN jobs j ON j.id = a.job_id WHERE a.id = (NEW.body ->> 'verification_attempt_id')::uuid;
    END IF;
    IF source IS NULL AND NOT (NEW.body ?| ARRAY[
        'lean_source','source_declaration','source_module','direct_basis_version_ids'
    ]) THEN
        RETURN NEW;
    END IF;
    IF source IS NULL OR NEW.body -> 'lean_source' IS DISTINCT FROM source
        OR NEW.body ->> 'target' IS DISTINCT FROM 'goal'
        OR NEW.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR NEW.body ->> 'candidate_theorem' IS DISTINCT FROM 'RMTheory.Proof.checked'
        OR NEW.body ->> 'source_declaration' IS DISTINCT FROM source ->> 'declaration'
        OR NEW.body ->> 'source_module' IS DISTINCT FROM source ->> 'module'
        OR NEW.body ?| ARRAY['local_lemma_ids','expanded_local_lemma_ids','local_checker_revision','refutation_source_sha256']
        OR jsonb_typeof(constants) IS DISTINCT FROM 'array'
        OR jsonb_typeof(direct_constants) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'native Lean receipt metadata must match the successful native verification job'
            USING ERRCODE = '23514';
    END IF;
    IF NOT constants @> direct_constants OR EXISTS (
        SELECT FROM jsonb_array_elements(constants) AS constant(entry)
        WHERE jsonb_typeof(entry) IS DISTINCT FROM 'string' OR nullif(btrim(entry #>> '{}'), '') IS NULL
    ) THEN
        RAISE EXCEPTION 'native Lean receipt needs the exact direct and transitive constant names' USING ERRCODE = '23514';
    END IF;
    SELECT coalesce(jsonb_agg(entry -> 'version_id' ORDER BY entry -> 'version_id')
                        FILTER (WHERE direct_constants ? (entry ->> 'declaration')), '[]'::jsonb),
           coalesce(jsonb_agg(entry -> 'version_id' ORDER BY entry -> 'version_id')
                        FILTER (WHERE constants ? (entry ->> 'declaration')), '[]'::jsonb)
    INTO direct_versions, actual_versions
    FROM jsonb_array_elements(source -> 'approved_declarations') AS mapping(entry);
    IF NEW.body -> 'direct_basis_version_ids' IS DISTINCT FROM direct_versions
        OR NEW.body -> 'basis_version_ids' IS DISTINCT FROM actual_versions
        OR NEW.body -> 'expanded_basis_version_ids' IS DISTINCT FROM actual_versions THEN
        RAISE EXCEPTION 'native Lean receipt basis must match the audited declarations'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_lean_source_receipt BEFORE INSERT ON rm.lean_checks
    FOR EACH ROW EXECUTE FUNCTION rm.guard_lean_source_receipt();
REVOKE ALL ON FUNCTION rm.guard_lean_source_receipt() FROM PUBLIC;

-- Both the reviewed base verifier and the native source checker remain
-- retirement/hold inputs. Do not change approved statement hashes.
CREATE OR REPLACE VIEW rm.retired_lean_checks WITH (security_barrier = true) AS
    SELECT DISTINCT c.id
    FROM rm.lean_checks c
    JOIN rm.lean_statements s ON s.id = c.statement_id
    JOIN rm.verification_input_retirements r ON
        (r.input_kind = 'verifier' AND r.sha256 IN (coalesce(c.body ->> 'checker_revision',
            s.body #>> '{environment,inputs,RMFlow/Audit.lean}'), c.body ->> 'local_checker_revision',
            c.body #>> '{lean_source,verifier_revision}')) OR
        (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                      c.body ->> 'verification_environment_sha256')) OR
        (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
            c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
            c.body ->> 'refutation_source_sha256', c.body #>> '{report,sha256}'));

CREATE OR REPLACE FUNCTION rm.local_final_checker_revision(payload jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT CASE WHEN $1 ? 'lean_statement_id'
        AND $1 -> 'checker' = '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
    THEN CASE
        WHEN $1 ->> 'target' = 'goal' AND $1 #>> '{lean_source,verifier_revision}' ~ '^[0-9a-f]{64}$'
        THEN $1 #>> '{lean_source,verifier_revision}'
        WHEN $1 ->> 'target' IN ('goal', 'refutation')
            AND $1 ->> 'local_checker_revision' ~ '^[0-9a-f]{64}$'
            AND $1 -> 'local_lemma_ids' <> '[]'::jsonb AND valid_final_lemma_ids($1 -> 'local_lemma_ids')
        THEN $1 ->> 'local_checker_revision'
    END END
$$;

-- Native source receipts may prove their own claim. The current catalog
-- replays structured submissions, so it cannot export a native source receipt.
CREATE FUNCTION rm.lean_receipt_has_native_source(evidence_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM evidence e JOIN lean_checks c ON c.id = e.lean_check_id
        WHERE e.id = $1 AND c.body ? 'lean_source'
    )
$$;

CREATE OR REPLACE FUNCTION rm.capture_promoted_lean_core_theorem() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT lean_receipt_has_local_inputs(NEW.decisive_evidence_id)
        AND NOT lean_receipt_has_native_source(NEW.decisive_evidence_id) THEN
        PERFORM publish_lean_core_theorem(NEW.decisive_evidence_id);
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION rm.guard_portable_lean_export() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF lean_receipt_has_native_source(NEW.evidence_id) THEN
        RAISE EXCEPTION 'a native Lean source receipt needs a portable structured proof before cross-claim export'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_portable_lean_export BEFORE INSERT ON rm.lean_core_exports
    FOR EACH ROW EXECUTE FUNCTION rm.guard_portable_lean_export();
REVOKE ALL ON FUNCTION rm.lean_receipt_has_native_source(uuid), rm.guard_portable_lean_export() FROM PUBLIC;
