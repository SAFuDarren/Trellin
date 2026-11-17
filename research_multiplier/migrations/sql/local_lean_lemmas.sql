CREATE TABLE rm.local_lean_lemmas (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id uuid NOT NULL REFERENCES rm.lean_statements(id),
    verification_attempt_id uuid NOT NULL UNIQUE REFERENCES rm.attempts(id),
    proposition text NOT NULL CHECK (btrim(proposition) <> '' AND length(proposition) <= 16000),
    body jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX local_lean_lemmas_statement ON rm.local_lean_lemmas(statement_id, recorded_at, id);

CREATE TABLE rm.local_lean_lemma_basis (
    lemma_id uuid NOT NULL REFERENCES rm.local_lean_lemmas(id),
    version_id uuid NOT NULL REFERENCES rm.formal_item_versions(id),
    PRIMARY KEY (lemma_id, version_id)
);
CREATE INDEX local_lean_lemma_by_basis ON rm.local_lean_lemma_basis(version_id);

CREATE TABLE rm.local_lean_lemma_dependencies (
    lemma_id uuid NOT NULL REFERENCES rm.local_lean_lemmas(id),
    prerequisite_id uuid NOT NULL REFERENCES rm.local_lean_lemmas(id),
    PRIMARY KEY (lemma_id, prerequisite_id),
    CHECK (lemma_id <> prerequisite_id)
);
CREATE INDEX local_lean_lemma_by_prerequisite ON rm.local_lean_lemma_dependencies(prerequisite_id);

CREATE TRIGGER local_lean_lemmas_append_only BEFORE UPDATE OR DELETE ON rm.local_lean_lemmas
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER local_lean_lemma_basis_append_only BEFORE UPDATE OR DELETE ON rm.local_lean_lemma_basis
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER local_lean_lemma_dependencies_append_only BEFORE UPDATE OR DELETE ON rm.local_lean_lemma_dependencies
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.local_lean_lemma_input_retired(report jsonb) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM verification_input_retirements r
        WHERE (r.input_kind = 'verifier' AND r.sha256 IN (report ->> 'checker_revision', report ->> 'reviewed_driver_sha256'))
           OR (r.input_kind = 'environment' AND r.sha256 = report ->> 'verification_environment_sha256')
           OR (r.input_kind = 'source' AND r.sha256 IN (report ->> 'statement_sha256',
               report ->> 'trusted_source_sha256', report #>> '{candidate,sha256}',
               report #>> '{submission,sha256}', report #>> '{report,sha256}'))
    );
$$;
REVOKE ALL ON FUNCTION rm.local_lean_lemma_input_retired(jsonb) FROM PUBLIC;

-- Walk only the selected statement's lemma graph, including inactive rows for audit.
CREATE FUNCTION rm.local_lean_lemma_status(anchor_id uuid)
RETURNS TABLE (id uuid, statement_id uuid, verification_attempt_id uuid, proposition text,
               body jsonb, recorded_at timestamptz, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE scoped AS MATERIALIZED (
        SELECT l.* FROM local_lean_lemmas l WHERE l.statement_id = anchor_id
    ), stale(lemma_id) AS (
        SELECT l.id FROM scoped l JOIN lean_statement_status anchor ON anchor.id = l.statement_id
        WHERE NOT anchor.check_ready OR local_lean_lemma_input_retired(l.body)
            OR EXISTS (
                SELECT 1 FROM packet_basis_items p JOIN formal_item_status b ON b.id = p.version_id
                WHERE p.packet_id = anchor.packet_id AND (NOT b.admitted OR b.needs_recheck)
            )
            OR EXISTS (
                SELECT 1 FROM local_lean_lemma_basis used
                LEFT JOIN formal_item_status b ON b.id = used.version_id AND b.admitted AND NOT b.needs_recheck
                WHERE used.lemma_id = l.id AND b.id IS NULL
            )
        UNION
        SELECT d.lemma_id FROM stale s JOIN local_lean_lemma_dependencies d ON d.prerequisite_id = s.lemma_id
    )
    SELECT l.id, l.statement_id, l.verification_attempt_id, l.proposition, l.body, l.recorded_at,
           CASE WHEN s.lemma_id IS NULL THEN 'verified' ELSE 'needs_recheck' END
    FROM scoped l LEFT JOIN stale s ON s.lemma_id = l.id;
$$;
REVOKE ALL ON FUNCTION rm.local_lean_lemma_status(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.local_lean_lemma_status(uuid) TO rm_human, rm_verifier;

CREATE FUNCTION rm.local_lean_lemma_source_sha(approved jsonb, proposition text) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT encode(sha256(convert_to(
        'import Lean' || chr(10) || coalesce((
            SELECT string_agg('import ' || m.value || chr(10), '' ORDER BY m.ordinal)
            FROM jsonb_array_elements_text(approved #> '{environment,external,modules}')
                 WITH ORDINALITY m(value, ordinal)
        ), '') || 'set_option autoImplicit false' || chr(10) || 'set_option maxHeartbeats 200000' || chr(10)
        || 'def RMTheory.expected : Prop := '
        || (SELECT string_agg('(' || chr(10) || a.value || chr(10) || ')', ' → ' ORDER BY a.ordinal)
            FROM jsonb_array_elements_text(approved #> '{scope,lean_hypotheses}' || jsonb_build_array(proposition))
                 WITH ORDINALITY a(value, ordinal)) || chr(10), 'UTF8')), 'hex');
$$;
REVOKE ALL ON FUNCTION rm.local_lean_lemma_source_sha(jsonb, text) FROM PUBLIC;

ALTER FUNCTION rm.require_dynamic_lean_job(rm.jobs) RENAME TO require_prior_dynamic_lean_job;
CREATE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
BEGIN
    IF job.payload ->> 'target' IS DISTINCT FROM 'local_lemma' THEN
        RETURN require_prior_dynamic_lean_job(job);
    END IF;

    PERFORM lock_live_foundation();
    anchor := require_lean_review((job.payload ->> 'lean_statement_id')::uuid, true);
    IF job.kind <> 'verify' OR job.budget ->> 'max_model_calls' IS DISTINCT FROM '0'
        OR anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR anchor.claim_id IS DISTINCT FROM job.claim_id
        OR anchor.contract_sha256 IS DISTINCT FROM job.contract_sha256
        OR anchor.statement_sha256 IS DISTINCT FROM job.payload ->> 'anchor_sha256'
        OR job.payload -> 'checker' IS DISTINCT FROM '{"name":"rm-lean-local-lemma","version":"1"}'::jsonb
        OR job.payload ->> 'trusted_source_sha256' IS DISTINCT FROM anchor.body #>> '{trusted_source,sha256}'
        OR job.payload ->> 'verification_environment_sha256' IS DISTINCT FROM anchor.body #>> '{environment,sha256}'
        OR job.payload ->> 'reviewed_driver_sha256' IS DISTINCT FROM anchor.body #>> '{environment,inputs,driver}'
        OR NOT coalesce(job.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$', false)
        OR NOT lean_artifact_ref_valid(job.payload -> 'submission')
        OR jsonb_typeof(job.payload -> 'proposition') IS DISTINCT FROM 'string'
        OR nullif(btrim(job.payload ->> 'proposition'), '') IS NULL
        OR length(job.payload ->> 'proposition') > 16000
        OR job.payload ->> 'statement_sha256' IS DISTINCT FROM
            local_lean_lemma_source_sha(anchor.body, job.payload ->> 'proposition')
        OR jsonb_typeof(job.payload -> 'local_lemma_ids') IS DISTINCT FROM 'array'
        OR local_lean_lemma_input_retired(job.payload) THEN
        RAISE EXCEPTION 'local Lean verification must match its current reviewed anchor and exact candidate'
            USING ERRCODE = '23514';
    END IF;

    PERFORM require_current_basis(anchor.packet_id, job.payload -> 'basis_version_ids');
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(job.payload -> 'local_lemma_ids') requested(id)
        WHERE NOT EXISTS (
            SELECT 1 FROM local_lean_lemma_status(anchor.id) lemma
            WHERE lemma.id::text = requested.id AND lemma.status = 'verified'
        )
    ) THEN
        RAISE EXCEPTION 'local theorem is missing, out of scope, or needs recheck' USING ERRCODE = '23514';
    END IF;
    RETURN anchor;
END $$;
REVOKE ALL ON FUNCTION rm.require_dynamic_lean_job(rm.jobs) FROM PUBLIC;

CREATE FUNCTION rm.record_local_lean_lemma(attempt_id uuid, report jsonb) RETURNS rm.local_lean_lemmas
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE attempt attempts;
DECLARE job jobs;
DECLARE anchor lean_statements;
DECLARE saved local_lean_lemmas;
DECLARE bound jsonb;
DECLARE allowed jsonb;
BEGIN
    SELECT * INTO STRICT attempt FROM attempts a WHERE a.id = attempt_id AND a.role = 'verify'
        AND a.finding = 'exact_proof' AND a.model_provider IS NULL;
    SELECT * INTO STRICT job FROM jobs j WHERE j.id = attempt.job_id AND j.status = 'succeeded'
        AND j.payload ->> 'target' = 'local_lemma';
    anchor := require_dynamic_lean_job(job);
    IF report -> 'compiled' IS DISTINCT FROM 'true'::jsonb
        OR report -> 'accepted' IS DISTINCT FROM 'true'::jsonb
        OR report -> 'kernel_rechecked' IS DISTINCT FROM 'true'::jsonb
        OR report -> 'scope_matches' IS DISTINCT FROM 'true'::jsonb
        OR NOT lean_analysis_valid(report)
        OR report ->> 'statement_sha256' IS DISTINCT FROM job.payload ->> 'statement_sha256'
        OR NOT lean_artifact_ref_valid(report -> 'candidate')
        OR report #>> '{report,uri}' IS DISTINCT FROM attempt.artifact_uri
        OR report #>> '{report,sha256}' IS DISTINCT FROM attempt.artifact_sha256
        OR jsonb_typeof(report -> 'basis_version_ids') IS DISTINCT FROM 'array'
        OR jsonb_typeof(report -> 'local_lemma_ids') IS DISTINCT FROM 'array'
        OR report -> 'basis_version_ids' IS DISTINCT FROM job.payload -> 'basis_version_ids'
        OR report -> 'local_lemma_ids' IS DISTINCT FROM job.payload -> 'local_lemma_ids' THEN
        RAISE EXCEPTION 'local theorem must have an exact, dependency-checked verifier receipt'
            USING ERRCODE = '23514';
    END IF;
    SELECT coalesce(jsonb_agg(permitted.version_id), '[]'::jsonb) INTO allowed
    FROM (
        SELECT version_id FROM packet_basis_items WHERE packet_id = anchor.packet_id
        UNION
        SELECT version_id FROM packet_library_items WHERE packet_id = anchor.packet_id
    ) permitted;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(report -> 'basis_version_ids') used(id)
        WHERE NOT proof_dependencies_authorized('item', used.id::uuid, allowed)
    ) THEN
        RAISE EXCEPTION 'local theorem uses facts outside the approved basis'
            USING ERRCODE = '23514';
    END IF;

    bound := report || jsonb_build_object(
        'checker', job.payload -> 'checker', 'checker_revision', job.payload -> 'checker_revision',
        'reviewed_driver_sha256', job.payload -> 'reviewed_driver_sha256',
        'submission', job.payload -> 'submission', 'anchor_sha256', job.payload -> 'anchor_sha256',
        'trusted_source_sha256', job.payload -> 'trusted_source_sha256',
        'verification_environment_sha256', job.payload -> 'verification_environment_sha256'
    );
    IF local_lean_lemma_input_retired(bound) THEN
        RAISE EXCEPTION 'local theorem uses a retired verification input' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO saved FROM local_lean_lemmas l WHERE l.verification_attempt_id = attempt.id;
    IF NOT FOUND THEN
        IF EXISTS (
            SELECT 1 FROM checker_hold_status h WHERE h.released_at IS NULL
                AND h.checker_revision IN (job.payload ->> 'checker_revision', job.payload ->> 'reviewed_driver_sha256')
        ) THEN
            RAISE EXCEPTION 'checker revision awaiting researcher review after a monitoring alert'
                USING ERRCODE = '23514';
        END IF;
        INSERT INTO local_lean_lemmas(statement_id, verification_attempt_id, proposition, body)
        VALUES (anchor.id, attempt.id, job.payload ->> 'proposition', bound)
        ON CONFLICT (verification_attempt_id) DO NOTHING RETURNING * INTO saved;
        IF FOUND THEN
            INSERT INTO local_lean_lemma_basis(lemma_id, version_id)
                SELECT saved.id, v.value::uuid FROM jsonb_array_elements_text(report -> 'basis_version_ids') v(value);
            INSERT INTO local_lean_lemma_dependencies(lemma_id, prerequisite_id)
                SELECT saved.id, v.value::uuid FROM jsonb_array_elements_text(report -> 'local_lemma_ids') v(value);
        ELSE
            SELECT * INTO STRICT saved FROM local_lean_lemmas l WHERE l.verification_attempt_id = attempt.id;
        END IF;
    END IF;
    IF saved.body IS DISTINCT FROM bound THEN
        RAISE EXCEPTION 'verification attempt already has a different local theorem record' USING ERRCODE = '23514';
    END IF;
    RETURN saved;
END $$;
REVOKE ALL ON FUNCTION rm.record_local_lean_lemma(uuid, jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_local_lean_lemma(uuid, jsonb) TO rm_verifier;
