-- A checked Lake comparison is a request to inspect meaning, never to supply
-- a new premise. Keep observations append-only and show the latest current one.
CREATE TABLE rm.lean_scope_observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id uuid NOT NULL REFERENCES rm.lean_statements(id),
    observed_environment_sha256 rm.sha256_hex NOT NULL,
    changes jsonb NOT NULL CHECK (jsonb_typeof(changes) = 'array'),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE
);
CREATE INDEX lean_scope_observation_latest ON rm.lean_scope_observations(statement_id, sequence_no DESC);
CREATE TRIGGER lean_scope_observation_append_only BEFORE UPDATE OR DELETE ON rm.lean_scope_observations
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.record_lean_scope_observation(
    statement_id uuid, observed_environment_sha256 text, changes jsonb
) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE previous lean_scope_observations;
DECLARE saved uuid;
BEGIN
    statement := require_lean_review($1, true);
    IF statement.body #> '{environment,external}' IS NULL
        OR NOT coalesce($2 ~ '^[0-9a-f]{64}$', false)
        OR jsonb_typeof($3) IS DISTINCT FROM 'array'
        OR jsonb_array_length($3) > 130
        OR ($3 <> '[]'::jsonb AND $2 = statement.body #>> '{environment,sha256}') THEN
        RAISE EXCEPTION 'Lean scope review requires a measured Lake build for the current statement'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements($3) delta(entry)
        WHERE CASE WHEN jsonb_typeof(entry) <> 'object' THEN true
          WHEN entry ->> 'kind' IN ('lean_foundation','frozen_goal_or_premises')
             THEN entry - 'kind' <> '{}'::jsonb
          WHEN entry ->> 'kind' = 'approved_theorem' THEN
             entry - ARRAY['kind','version_id','declaration'] <> '{}'::jsonb OR NOT EXISTS (
                 SELECT FROM jsonb_array_elements(current_lean_lake_basis(statement.packet_id)) named(value)
                 WHERE value = entry - 'kind'
             )
          ELSE true END
    ) OR jsonb_array_length($3) <> (SELECT count(DISTINCT entry) FROM jsonb_array_elements($3) delta(entry))
    THEN
        RAISE EXCEPTION 'only observed changes to the frozen Lean scope can be sent for scope review'
            USING ERRCODE = '23514';
    END IF;

    -- The usual unchanged check creates no row and takes no per-statement lock.
    SELECT * INTO previous FROM lean_scope_observations entry WHERE entry.statement_id = $1
    ORDER BY entry.sequence_no DESC LIMIT 1;
    IF $3 = '[]'::jsonb AND (NOT FOUND OR previous.changes = '[]'::jsonb) THEN RETURN NULL; END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.lean_scope_observation:' || $1::text, 0));
    SELECT * INTO previous FROM lean_scope_observations entry WHERE entry.statement_id = $1
    ORDER BY entry.sequence_no DESC LIMIT 1;
    IF $3 = '[]'::jsonb AND (NOT FOUND OR previous.changes = '[]'::jsonb) THEN RETURN NULL; END IF;
    IF FOUND AND previous.observed_environment_sha256 = $2 AND previous.changes = $3 THEN
        RETURN previous.id;
    END IF;
    INSERT INTO lean_scope_observations (statement_id, observed_environment_sha256, changes)
    VALUES ($1,$2,$3) RETURNING id INTO saved;
    RETURN saved;
END $$;
REVOKE ALL ON TABLE rm.lean_scope_observations FROM PUBLIC;
REVOKE ALL ON FUNCTION rm.record_lean_scope_observation(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_lean_scope_observation(uuid,text,jsonb) TO rm_verifier;

ALTER VIEW rm.research_actionable_review_inbox RENAME TO research_actionable_review_inbox_before_lean_scope;
CREATE VIEW rm.research_actionable_review_inbox WITH (security_barrier = true) AS
    SELECT * FROM rm.research_actionable_review_inbox_before_lean_scope
    UNION ALL
    SELECT 'review_lean_scope_change', seen.id, statement.claim_id, seen.observed_at,
           'A measured Lake build changed the confirmed meaning or trusted Lean foundation. Inspect the change before approving a replacement scope.',
           jsonb_build_object('statement_id', statement.id, 'changes', seen.changes,
                   'reviewed_environment_sha256', statement.body #>> '{environment,sha256}',
                   'observed_environment_sha256', seen.observed_environment_sha256)
    FROM rm.lean_statement_status statement
    CROSS JOIN LATERAL (
        SELECT observation.* FROM rm.lean_scope_observations observation
        WHERE observation.statement_id = statement.id ORDER BY observation.sequence_no DESC LIMIT 1
    ) seen
    WHERE statement.approved AND statement."current" AND statement.check_ready
      AND seen.changes <> '[]'::jsonb;
REVOKE ALL ON rm.research_actionable_review_inbox FROM PUBLIC;
GRANT SELECT ON rm.research_actionable_review_inbox TO rm_human;

-- Discovery is read-only. The verifier registers an import only when a
-- candidate actually uses its exact version.
CREATE FUNCTION rm.lean_search_native_theorems(packet_id uuid, model_visible boolean)
RETURNS TABLE (id uuid, body jsonb) LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE destination record;
DECLARE candidate record;
DECLARE admitted_count integer := 0;
BEGIN
    SELECT claim.id, claim.sensitivity, statement.id AS statement_id,
           statement.body ->> 'theorem_type' AS proposition
    INTO destination FROM lean_statement_status statement
    JOIN claims claim ON claim.id = statement.claim_id
    WHERE statement.packet_id = $1 AND statement.check_ready AND claim.domain = 'lean-core';
    IF NOT FOUND OR ($2 AND destination.sensitivity <> 'public') THEN RETURN; END IF;
    FOR candidate IN
        WITH obligations AS MATERIALIZED (
            SELECT item ->> 'proposition' AS proposition
            FROM lean_reduction_status(destination.statement_id) route
            CROSS JOIN LATERAL jsonb_array_elements(route.obligations) open(item)
            WHERE route.status = 'awaiting_obligations' AND item -> 'lemma_id' = 'null'::jsonb
        )
        SELECT export.id, export.body
        FROM current_lean_core_theorems export
        JOIN evidence receipt ON receipt.id = export.evidence_id
        JOIN claims origin ON origin.id = receipt.claim_id
        LEFT JOIN lean_core_imports imported ON imported.version_id = export.id AND imported.packet_id = $1
        WHERE export.body ->> 'format' = 'lean-native-proof'
          AND origin.id <> destination.id AND (origin.sensitivity = 'public' OR destination.sensitivity = 'restricted')
        ORDER BY (EXISTS (SELECT FROM obligations
                   WHERE proposition = export.body #>> '{formal,lean,proposition}')) DESC,
                 (imported.version_id IS NOT NULL) DESC,
                 (export.body #>> '{formal,lean,proposition}' = destination.proposition) DESC,
                 (strpos(destination.proposition, export.body #>> '{formal,lean,proposition}') > 0) DESC,
                 receipt.issued_at DESC, export.id
        LIMIT 36
    LOOP
        IF $2 THEN
            BEGIN
                PERFORM require_public_model_basis(jsonb_build_array(candidate.id));
            EXCEPTION WHEN check_violation THEN
                CONTINUE;
            END;
        END IF;
        id := candidate.id;
        body := candidate.body;
        RETURN NEXT;
        admitted_count := admitted_count + 1;
        EXIT WHEN admitted_count = 12;
    END LOOP;
END $$;
REVOKE ALL ON FUNCTION rm.lean_search_native_theorems(uuid,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_search_native_theorems(uuid,boolean) TO rm_verifier;

CREATE FUNCTION rm.lean_composition_native_ids(constants jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT CASE WHEN jsonb_typeof($1) = 'array' THEN coalesce((
        SELECT jsonb_agg(DISTINCT found.id ORDER BY found.id) FROM (
            SELECT right(constant, 32)::uuid AS id
            FROM jsonb_array_elements_text($1) declaration(constant)
            WHERE constant ~ '^RMTheory[.]Proof[.]basis_native_[0-9a-f]{32}$'
        ) found
    ), '[]'::jsonb) END
$$;

CREATE FUNCTION rm.require_lean_composition_basis(job_payload jsonb, report jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE audited jsonb;
DECLARE used_native jsonb;
DECLARE imported jsonb;
BEGIN
    IF report -> 'lean_composition' IS DISTINCT FROM job_payload -> 'lean_composition'
      OR report -> 'lake_validation' IS DISTINCT FROM job_payload -> 'lake_validation' THEN
        RAISE EXCEPTION 'the composed Lean receipt must match the verification inputs'
            USING ERRCODE = '23514';
    END IF;
    IF NOT job_payload ? 'lean_composition' THEN RETURN; END IF;

    audited := lean_composition_native_ids(report #> '{dependencies,constants}');
    IF NOT valid_final_lemma_ids(audited)
        OR report #> '{dependencies,native_version_ids}' IS DISTINCT FROM audited
        OR jsonb_typeof(report -> 'basis_version_ids') IS DISTINCT FROM 'array'
        OR NOT coalesce(job_payload -> 'basis_version_ids' @> (report -> 'basis_version_ids'), false) THEN
        RAISE EXCEPTION 'composed Lean basis must follow the audited constants'
            USING ERRCODE = '23514';
    END IF;
    SELECT coalesce(jsonb_agg(item.id ORDER BY item.id), '[]'::jsonb) INTO used_native
    FROM jsonb_array_elements_text(report -> 'basis_version_ids') used(id)
    JOIN formal_item_versions item ON item.id = used.id::uuid
    WHERE item.body ->> 'format' = 'lean-native-proof';
    SELECT coalesce(jsonb_agg(entry -> 'version_id'), '[]'::jsonb) INTO imported
    FROM jsonb_array_elements(job_payload #> '{lean_composition,imports}') bound(entry);
    IF audited IS DISTINCT FROM used_native OR NOT imported @> audited THEN
        RAISE EXCEPTION 'composed Lean native versions do not match the exact used archives'
            USING ERRCODE = '23514';
    END IF;
END $$;
REVOKE ALL ON FUNCTION rm.lean_composition_native_ids(jsonb), rm.require_lean_composition_basis(jsonb,jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE validation jsonb := job.payload -> 'lake_validation';
DECLARE replay jsonb := job.payload -> 'native_replay';
DECLARE mappings jsonb;
DECLARE composition jsonb := job.payload -> 'lean_composition';
DECLARE composed_versions jsonb;
DECLARE job_native_versions jsonb;
DECLARE external_modules boolean;
BEGIN
    anchor := require_prior_lake_lean_job(job);
    external_modules := coalesce(jsonb_typeof(anchor.body #> '{environment,external,modules}') = 'array'
        AND anchor.body #> '{environment,external,modules}' <> '[]'::jsonb, false);

    IF job.payload ? 'native_replay' THEN
        IF job.payload ?| ARRAY['lean_source','lean_composition','local_lemma_ids','local_checker_revision','refutation_source_sha256']
            OR anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
            OR job.payload ->> 'target' IS DISTINCT FROM 'goal'
            OR job.budget ->> 'max_model_calls' IS DISTINCT FROM '0'
            OR jsonb_typeof(replay) IS DISTINCT FROM 'object'
            OR NOT replay ?& ARRAY['version_id','artifact','verifier_revision','approved_declarations']
            OR replay - ARRAY['version_id','artifact','verifier_revision','approved_declarations'] <> '{}'::jsonb
            OR NOT coalesce(replay ->> 'version_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$', false)
            OR jsonb_typeof(replay -> 'version_id') IS DISTINCT FROM 'string'
            OR NOT lean_artifact_ref_valid(replay -> 'artifact')
            OR jsonb_typeof(replay -> 'verifier_revision') IS DISTINCT FROM 'string'
            OR NOT coalesce(replay ->> 'verifier_revision' ~ '^[0-9a-f]{64}$', false)
            OR jsonb_typeof(replay -> 'approved_declarations') IS DISTINCT FROM 'array'
            OR NOT (job.payload -> 'basis_version_ids' ? (replay ->> 'version_id'))
            OR external_modules IS DISTINCT FROM (job.payload ? 'lake_validation') THEN
            RAISE EXCEPTION 'native Lean replay needs an exact exported theorem and observed verification inputs'
                USING ERRCODE = '23514';
        END IF;
        mappings := replay -> 'approved_declarations';
        IF order_lean_lake_basis(mappings) IS DISTINCT FROM current_lean_lake_basis(anchor.packet_id)
            OR NOT EXISTS (
                SELECT FROM lean_core_imports imported
                JOIN current_lean_core_theorems exported ON exported.id = imported.version_id
                JOIN native_lean_replay_archives archive ON archive.evidence_id = exported.evidence_id
                WHERE imported.packet_id = anchor.packet_id AND imported.version_id = (replay ->> 'version_id')::uuid
                    AND exported.body ->> 'format' = 'lean-native-proof'
                    AND archive.binding -> 'artifact' = replay -> 'artifact'
            ) THEN
            RAISE EXCEPTION 'native Lean replay requires the current imported theorem and its exact archive'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT FROM verification_input_retirements retired
            WHERE (retired.input_kind, retired.sha256::text) IN (
                ('source', replay #>> '{artifact,sha256}'), ('verifier', replay ->> 'verifier_revision'))
        ) THEN
            RAISE EXCEPTION 'native Lean replay input requires recheck' USING ERRCODE = '23514';
        END IF;
    ELSIF job.payload ? 'lean_composition' THEN
        IF job.payload ? 'lean_source'
            OR anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
            OR job.payload ->> 'target' NOT IN ('goal','refutation','local_lemma')
            OR job.budget ->> 'max_model_calls' IS DISTINCT FROM '0'
            OR jsonb_typeof(composition) IS DISTINCT FROM 'object'
            OR NOT composition ?& ARRAY['imports','verifier_revision','approved_declarations']
            OR composition - ARRAY['imports','verifier_revision','approved_declarations'] <> '{}'::jsonb
            OR jsonb_typeof(composition -> 'imports') IS DISTINCT FROM 'array'
            OR jsonb_typeof(composition -> 'verifier_revision') IS DISTINCT FROM 'string'
            OR NOT coalesce(composition ->> 'verifier_revision' ~ '^[0-9a-f]{64}$', false)
            OR jsonb_typeof(composition -> 'approved_declarations') IS DISTINCT FROM 'array'
            OR (job.payload ->> 'target' = 'local_lemma'
                AND composition ->> 'verifier_revision' IS DISTINCT FROM job.payload ->> 'checker_revision')
        THEN
            RAISE EXCEPTION 'Lean proof composition requires its exact scoped inputs' USING ERRCODE = '23514';
        END IF;
        mappings := composition -> 'approved_declarations';
        IF order_lean_lake_basis(mappings) IS DISTINCT FROM current_lean_lake_basis(anchor.packet_id)
            OR jsonb_array_length(composition -> 'imports') > 24
            OR EXISTS (
                SELECT FROM jsonb_array_elements(composition -> 'imports') bound(entry)
                WHERE jsonb_typeof(entry) IS DISTINCT FROM 'object'
                    OR NOT entry ?& ARRAY['version_id','artifact']
                    OR entry - ARRAY['version_id','artifact'] <> '{}'::jsonb
                    OR NOT coalesce(entry ->> 'version_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$', false)
                    OR NOT lean_artifact_ref_valid(entry -> 'artifact')
            )
        THEN
            RAISE EXCEPTION 'Lean proof composition requires a validated destination basis and exact archives'
                USING ERRCODE = '23514';
        END IF;
        SELECT coalesce(jsonb_agg(entry -> 'version_id' ORDER BY entry -> 'version_id'), '[]'::jsonb)
        INTO composed_versions FROM jsonb_array_elements(composition -> 'imports') bound(entry);
        SELECT coalesce(jsonb_agg(item.id ORDER BY item.id), '[]'::jsonb) INTO job_native_versions
        FROM jsonb_array_elements_text(job.payload -> 'basis_version_ids') requested(id)
        JOIN formal_item_versions item ON item.id = requested.id::uuid
        WHERE item.body ->> 'format' = 'lean-native-proof';
        IF NOT valid_final_lemma_ids(composed_versions) OR composed_versions IS DISTINCT FROM job_native_versions
            OR EXISTS (
                SELECT FROM jsonb_array_elements(composition -> 'imports') bound(entry)
                WHERE NOT EXISTS (
                    SELECT FROM lean_core_imports imported
                    JOIN current_lean_core_theorems exported ON exported.id = imported.version_id
                    JOIN native_lean_replay_archives archive ON archive.evidence_id = exported.evidence_id
                    WHERE imported.packet_id = anchor.packet_id
                        AND imported.version_id = (entry ->> 'version_id')::uuid
                        AND exported.body ->> 'format' = 'lean-native-proof'
                        AND exported.body #>> '{source,claim_id}' <> anchor.claim_id::text
                        AND archive.binding -> 'artifact' = entry -> 'artifact'
                )
            )
        THEN
            RAISE EXCEPTION 'Lean proof composition requires live, imported native theorem versions'
                USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT FROM verification_input_retirements retired
            WHERE (retired.input_kind = 'verifier' AND retired.sha256 = composition ->> 'verifier_revision')
                OR (retired.input_kind = 'source' AND EXISTS (
                    SELECT FROM jsonb_array_elements(composition -> 'imports') bound(entry)
                    WHERE retired.sha256 = entry #>> '{artifact,sha256}'
                ))
        ) THEN
            RAISE EXCEPTION 'Lean proof composition requires recheck under current verified inputs'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF EXISTS (
            SELECT FROM jsonb_array_elements_text(coalesce(job.payload -> 'basis_version_ids', '[]'::jsonb)) input(id)
            JOIN formal_item_versions item ON item.id = input.id::uuid
            WHERE item.body ->> 'format' = 'lean-native-proof'
        ) THEN
            RAISE EXCEPTION 'a native Lean library theorem requires independent native replay'
                USING ERRCODE = '23514';
        END IF;
        mappings := job.payload #> '{lean_source,approved_declarations}';
    END IF;

    IF NOT job.payload ? 'lake_validation' THEN
        RETURN anchor;
    END IF;
    IF NOT job.payload ?| ARRAY['lean_source','native_replay','lean_composition'] OR NOT external_modules
        OR jsonb_typeof(validation) IS DISTINCT FROM 'object'
        OR NOT validation ?& ARRAY['baseline_id','scope_revision','snapshot_sha256','runtime_sha256']
        OR validation - ARRAY['baseline_id','scope_revision','snapshot_sha256','runtime_sha256'] <> '{}'::jsonb
        OR jsonb_typeof(validation -> 'runtime_sha256') IS DISTINCT FROM 'string'
        OR NOT coalesce(validation ->> 'runtime_sha256' ~ '^[0-9a-f]{64}$', false)
        OR NOT EXISTS (
            SELECT FROM get_lean_lake_baseline(anchor.id, validation ->> 'scope_revision', mappings) saved
            WHERE to_jsonb(saved.id) = validation -> 'baseline_id'
                AND to_jsonb(saved.scope_revision) = validation -> 'scope_revision'
                AND to_jsonb(saved.snapshot_sha256) = validation -> 'snapshot_sha256'
                AND saved.statement_sha256 = anchor.statement_sha256
                AND saved.original_environment_sha256 = job.payload ->> 'verification_environment_sha256'
                AND saved.approved_declarations = current_lean_lake_basis(anchor.packet_id)
        ) THEN
        RAISE EXCEPTION 'a Lake verification job must bind the current runtime to its original semantic baseline'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT FROM verification_input_retirements retired
        WHERE (retired.input_kind, retired.sha256::text) IN (
            ('verifier', validation ->> 'scope_revision'), ('environment', validation ->> 'runtime_sha256'))
    ) THEN
        RAISE EXCEPTION 'the observed Lake verification environment requires recheck' USING ERRCODE = '23514';
    END IF;
    RETURN anchor;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_new_lean_lake_job() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.payload ?| ARRAY['lake_validation','native_replay','lean_composition'] AND (
        NEW.kind <> 'verify' OR NOT NEW.payload ? 'lean_statement_id'
            OR NOT NEW.payload ?| ARRAY['lean_source','native_replay','lean_composition']
    ) THEN
        RAISE EXCEPTION 'Lake and replay validation belongs to a native Lean verification job' USING ERRCODE = '23514';
    END IF;
    IF NOT NEW.payload ? 'lake_validation' AND EXISTS (
        SELECT FROM lean_statements s WHERE s.id::text = NEW.payload ->> 'lean_statement_id'
            AND jsonb_typeof(s.body #> '{environment,external,modules}') = 'array'
            AND s.body #> '{environment,external,modules}' <> '[]'::jsonb
    ) THEN
        RAISE EXCEPTION 'a new native Lake check requires a semantic baseline and observed runtime'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER guard_new_lean_lake_job ON rm.jobs;
CREATE TRIGGER guard_new_lean_lake_job BEFORE INSERT ON rm.jobs
    FOR EACH ROW WHEN (NEW.payload ?| ARRAY['lean_source','native_replay','lake_validation','lean_composition'])
    EXECUTE FUNCTION rm.guard_new_lean_lake_job();

CREATE OR REPLACE FUNCTION rm.guard_native_lean_replay_receipt() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE replay jsonb;
DECLARE task_payload jsonb;
DECLARE observed_constants jsonb := NEW.body #> '{dependencies,constants}';
DECLARE actual_versions jsonb;
BEGIN
    IF NEW.body ? 'verification_attempt_id' THEN
        SELECT j.payload INTO task_payload FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = (NEW.body ->> 'verification_attempt_id')::uuid;
    END IF;
    replay := task_payload -> 'native_replay';
    IF NEW.body ? 'lean_composition' OR task_payload ? 'lean_composition' THEN
        PERFORM require_lean_composition_basis(task_payload, NEW.body);
    END IF;
    IF NEW.body -> 'native_replay' IS DISTINCT FROM replay
        OR (NEW.body ? 'native_capsule' AND (
            NOT NEW.body ?| ARRAY['native_replay','lean_source','lean_composition'] OR NOT lean_artifact_ref_valid(NEW.body -> 'native_capsule')
            OR EXISTS (SELECT FROM verification_input_retirements retired
                WHERE retired.input_kind = 'source' AND retired.sha256 = NEW.body #>> '{native_capsule,sha256}'))
        ) THEN
        RAISE EXCEPTION 'native Lean replay receipt must match its exact imported theorem and successful check'
            USING ERRCODE = '23514';
    END IF;
    IF replay IS NOT NULL THEN
        IF jsonb_typeof(observed_constants) IS DISTINCT FROM 'array' OR EXISTS (
            SELECT FROM jsonb_array_elements(observed_constants) constant(value)
            WHERE jsonb_typeof(value) IS DISTINCT FROM 'string' OR nullif(btrim(value #>> '{}'), '') IS NULL
        ) THEN
            RAISE EXCEPTION 'a native replay receipt must report the actual checked declarations'
                USING ERRCODE = '23514';
        END IF;
        SELECT jsonb_agg(id ORDER BY id) INTO actual_versions FROM (
            SELECT replay -> 'version_id' AS id
            UNION SELECT entry -> 'version_id'
                FROM jsonb_array_elements(replay -> 'approved_declarations') mapping(entry)
                WHERE observed_constants ? (entry ->> 'declaration')
        ) AS bound;
        IF NEW.body -> 'basis_version_ids' IS DISTINCT FROM actual_versions
            OR (NEW.body ? 'expanded_basis_version_ids'
                AND NEW.body -> 'expanded_basis_version_ids' IS DISTINCT FROM actual_versions) THEN
            RAISE EXCEPTION 'native Lean replay receipt basis must match the imported theorem and actual checked declarations'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION rm.record_local_lean_lemma(attempt_id uuid, report jsonb) RETURNS rm.local_lean_lemmas
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
    PERFORM require_lean_composition_basis(job.payload, report);
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
        OR (NOT job.payload ? 'lean_composition'
            AND report -> 'basis_version_ids' IS DISTINCT FROM job.payload -> 'basis_version_ids')
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

CREATE OR REPLACE FUNCTION rm.proof_dependencies_authorized(root_kind text, root_id uuid, admitted jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE
    current_receipts AS NOT MATERIALIZED (
        SELECT e.id, e.lean_check_id, e.verification_attempt_id, e.attempt_id,
               e.verifier_name, e.verifier_version, e.claim_id, e.kind
        FROM evidence_foundation_status e
        WHERE e.kind IN ('lean', 'exact_certificate') AND e.verdict = 'verified'
          AND e.diff_status = 'exact' AND e.issuance_sha256 IS NOT NULL
          AND NOT e.needs_recheck AND e.review_status <> 'returned'
    ),
    exact_certificates AS NOT MATERIALIZED (
        SELECT e.id, packet.id AS packet_id
        FROM current_receipts e JOIN claims c ON c.id = e.claim_id
        JOIN attempts proof ON proof.id = e.attempt_id JOIN jobs author_task ON author_task.id = proof.job_id
        JOIN formalization_status packet ON packet.claim_id = c.id AND packet.proof_ready
        WHERE e.kind = 'exact_certificate' AND
          (c.domain, e.verifier_name, e.verifier_version) IN (
              ('fixed-directed-max-flow', 'exact-flow-cut', 'v0a-2'),
              ('graph-coloring', 'exact-graph-coloring', '1'),
              ('linear-program', 'exact-linear-program', '1'),
              ('semidefinite-program', 'exact-semidefinite-program', '1')
          )
          AND author_task.payload @> jsonb_build_object(
              'formalization_id', packet.id, 'formalization_sha256', packet.packet_sha256)
          AND NOT EXISTS (
              SELECT 1 FROM packet_basis_items basis JOIN formal_item_versions item ON item.id = basis.version_id
              WHERE basis.packet_id = packet.id AND item.body ->> 'kind' <> 'goal'
                AND NOT EXISTS (SELECT 1 FROM evidence_basis_use used
                    WHERE used.evidence_id = e.id AND used.version_id = basis.version_id)
          )
    ),
    supported_checks AS NOT MATERIALIZED (
        SELECT checked.id, execution.target, statement.packet_id, checked.body
        FROM lean_checks checked JOIN lean_execution_links execution ON execution.check_id = checked.id
        JOIN lean_statement_status statement ON statement.id = checked.statement_id AND statement.check_ready
        WHERE statement.body -> 'checker' IN (
            '{"name":"rm-lean-core-audit","version":"1"}'::jsonb,
            '{"name":"rm-lean-flow-audit","version":"1"}'::jsonb
        )
          AND checked.body -> 'accepted' = 'true'::jsonb
          AND jsonb_typeof(checked.body -> 'basis_version_ids') = 'array'
          AND checked.body -> 'basis_version_ids' = coalesce((
              SELECT jsonb_agg(used.version_id ORDER BY used.version_id)
              FROM verification_basis_use used WHERE used.check_id = checked.id AND used.dependency_kind = 'actual'
          ), '[]'::jsonb)
          AND NOT EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = checked.id)
    ),
    edges(dependent_kind, dependent_id, input_kind, input_id) AS NOT MATERIALIZED (
        SELECT 'receipt', e.id, 'check', e.lean_check_id
        FROM current_receipts e WHERE e.kind = 'lean'
        UNION ALL SELECT 'receipt', used.evidence_id, 'item', used.version_id
            FROM evidence_basis_use used JOIN formal_item_versions item ON item.id = used.version_id
            WHERE item.body ->> 'kind' <> 'goal'
        UNION ALL SELECT 'check', used.check_id, 'item', used.version_id
            FROM verification_basis_use used WHERE used.dependency_kind = 'actual'
        UNION ALL SELECT 'check', used.check_id, 'local-lemma', used.lemma_id FROM verification_local_lemma_use used
        UNION ALL SELECT 'local-lemma', d.lemma_id, 'local-lemma', d.prerequisite_id FROM local_lean_lemma_dependencies d
        UNION ALL SELECT 'local-lemma', used.lemma_id, 'item', used.version_id FROM local_lean_lemma_basis used
        UNION ALL SELECT 'local-check', used.check_id, 'item', used.version_id
            FROM verification_basis_use used JOIN formal_item_versions item ON item.id = used.version_id
            WHERE used.dependency_kind = 'actual'
                OR (used.dependency_kind = 'scope' AND item.body ->> 'kind' IN ('hypothesis','definition'))
        -- Native provenance remains on the staleness graph. The exact
        -- destination replay has already checked the premises of this
        -- independently established implication against the destination.
        UNION ALL SELECT 'item', exported.version_id, 'receipt', exported.evidence_id
            FROM lean_core_exports exported JOIN formal_item_versions item ON item.id = exported.version_id
            WHERE item.body ->> 'format' IS DISTINCT FROM 'lean-native-proof'
        UNION ALL SELECT 'item', derived.version_id, 'local-check', derived.check_id
            FROM formal_lean_derivations derived
            WHERE NOT EXISTS (SELECT 1 FROM lean_core_exports exported WHERE exported.version_id = derived.version_id)
        UNION ALL SELECT 'item', bridge.version_id, 'receipt', e.id
            FROM formal_certificate_derivations bridge JOIN flow_derivation_records fact ON fact.id = bridge.derivation_id
            JOIN current_receipts e ON e.verification_attempt_id = fact.verification_attempt_id AND e.kind = 'exact_certificate'
    ),
    ancestry(kind, id) AS (
        SELECT $1, $2
        UNION
        SELECT edge.input_kind, edge.input_id FROM ancestry node
        JOIN edges edge ON edge.dependent_kind = node.kind AND edge.dependent_id = node.id
    )
    SELECT NOT EXISTS (
        SELECT 1 FROM ancestry node
        WHERE NOT CASE node.kind
        WHEN 'receipt' THEN EXISTS (
            SELECT 1 FROM current_receipts e WHERE e.id = node.id AND (
                (e.kind = 'lean' AND EXISTS (
                    SELECT 1 FROM supported_checks checked WHERE checked.id = e.lean_check_id AND checked.target = 'goal'
                )) OR (e.kind = 'exact_certificate' AND EXISTS (
                    SELECT 1 FROM exact_certificates checked WHERE checked.id = e.id
                ))
            )
        )
        WHEN 'check' THEN EXISTS (
            SELECT 1 FROM supported_checks checked WHERE checked.id = node.id AND checked.target IN ('goal','refutation')
        )
        WHEN 'local-check' THEN EXISTS (
            SELECT 1 FROM supported_checks checked
            WHERE checked.id = node.id AND checked.target IN ('upper_bound', 'attainment')
        )
        WHEN 'local-lemma' THEN EXISTS (
            SELECT FROM local_lean_lemmas l WHERE l.id = node.id
                AND NOT EXISTS (SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = l.id)
        )
        WHEN 'item' THEN EXISTS (
            SELECT 1 FROM formal_item_status item WHERE item.id = node.id AND item.admitted AND NOT item.needs_recheck AND (
                EXISTS (SELECT 1 FROM current_lean_core_theorems export WHERE export.id = item.id)
                OR (NOT EXISTS (SELECT 1 FROM lean_core_exports export WHERE export.version_id = item.id) AND EXISTS (
                    SELECT 1 FROM formal_lean_derivations derived WHERE derived.version_id = item.id
                ))
                OR EXISTS (
                    SELECT 1 FROM formal_certificate_derivations bridge
                    JOIN verified_derivations fact ON fact.id = bridge.derivation_id
                    JOIN exact_certificates e ON e.packet_id = fact.packet_id
                    JOIN current_receipts receipt ON receipt.id = e.id
                        AND receipt.verification_attempt_id = fact.verification_attempt_id
                    WHERE bridge.version_id = item.id
                )
                OR (item.body ->> 'kind' IN ('definition','hypothesis','theorem') AND $3 ? (item.id::text) AND EXISTS (
                    SELECT 1 FROM formal_item_approvals approval WHERE approval.version_id = item.id
                ))
            )
        )
        ELSE false END
    )
$$;

CREATE OR REPLACE FUNCTION rm.local_lean_lemma_input_retired(report jsonb) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM verification_input_retirements r
        WHERE (r.input_kind = 'verifier' AND r.sha256 IN (report ->> 'checker_revision', report ->> 'reviewed_driver_sha256', report #>> '{lake_validation,scope_revision}'))
           OR (r.input_kind = 'environment' AND r.sha256 IN (report ->> 'verification_environment_sha256', report #>> '{lake_validation,runtime_sha256}'))
           OR (r.input_kind = 'source' AND r.sha256 IN (report ->> 'statement_sha256',
               report ->> 'trusted_source_sha256', report #>> '{candidate,sha256}',
               report #>> '{submission,sha256}', report #>> '{report,sha256}'))
    );
$$;

CREATE OR REPLACE FUNCTION rm.local_final_checker_revision(payload jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT CASE WHEN $1 ? 'lean_statement_id'
        AND $1 -> 'checker' = '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
    THEN CASE
        WHEN $1 ->> 'target' IN ('goal','refutation')
            AND $1 #>> '{lean_composition,verifier_revision}' ~ '^[0-9a-f]{64}$'
        THEN $1 #>> '{lean_composition,verifier_revision}'
        WHEN $1 ->> 'target' = 'goal' AND $1 #>> '{native_replay,verifier_revision}' ~ '^[0-9a-f]{64}$'
        THEN $1 #>> '{native_replay,verifier_revision}'
        WHEN $1 ->> 'target' = 'goal' AND $1 #>> '{lean_source,verifier_revision}' ~ '^[0-9a-f]{64}$'
        THEN $1 #>> '{lean_source,verifier_revision}'
        WHEN $1 ->> 'target' IN ('goal', 'refutation')
            AND $1 ->> 'local_checker_revision' ~ '^[0-9a-f]{64}$'
            AND $1 -> 'local_lemma_ids' <> '[]'::jsonb AND valid_final_lemma_ids($1 -> 'local_lemma_ids')
        THEN $1 ->> 'local_checker_revision'
    END END
$$;

CREATE OR REPLACE VIEW rm.retired_lean_checks WITH (security_barrier = true) AS
    SELECT DISTINCT c.id
    FROM rm.lean_checks c
    JOIN rm.lean_statements s ON s.id = c.statement_id
    JOIN rm.verification_input_retirements r ON
        (r.input_kind = 'verifier' AND r.sha256 IN (coalesce(c.body ->> 'checker_revision',
            s.body #>> '{environment,inputs,RMFlow/Audit.lean}'), c.body ->> 'local_checker_revision',
            c.body #>> '{lean_source,verifier_revision}', c.body #>> '{lake_validation,scope_revision}',
            c.body #>> '{native_replay,verifier_revision}', c.body #>> '{lean_composition,verifier_revision}')) OR
        (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
            c.body ->> 'verification_environment_sha256', c.body #>> '{lake_validation,runtime_sha256}')) OR
        (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
            c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
            c.body ->> 'refutation_source_sha256', c.body #>> '{report,sha256}',
            c.body #>> '{native_replay,artifact,sha256}'));

CREATE OR REPLACE FUNCTION rm.register_native_lean_replay(evidence_id uuid, binding jsonb)
RETURNS rm.native_lean_replay_archives
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE original record;
DECLARE saved native_lean_replay_archives;
BEGIN
    PERFORM lock_live_foundation();
    SELECT checked.body AS audit, e.review_status,
           (c.status = 'proved' AND c.decisive_evidence_id = e.id) AS promoted
    INTO original
    FROM evidence_foundation_status e
    JOIN claims c ON c.id = e.claim_id
    JOIN lean_checks checked ON checked.id = e.lean_check_id
    JOIN lean_statement_status s ON s.id = checked.statement_id AND s.check_ready
    JOIN lean_execution_links execution ON execution.check_id = checked.id AND execution.target = 'goal'
        AND execution.verification_attempt_id = e.verification_attempt_id
    JOIN attempts proof ON proof.id = execution.verification_attempt_id AND proof.role = 'verify'
        AND proof.finding = 'exact_proof'
    WHERE e.id = $1 AND e.kind = 'lean' AND e.verdict = 'verified' AND e.diff_status = 'exact'
        AND e.review_status <> 'returned' AND NOT e.needs_recheck AND c.domain = 'lean-core'
        AND s.body ->> 'theorem' = 'RMTheory.expected'
        AND checked.body ?| ARRAY['lean_source','native_replay','lean_composition'];
    IF NOT FOUND OR jsonb_typeof($2) IS DISTINCT FROM 'object'
        OR NOT $2 ?& ARRAY['artifact','candidate','submission','basis_version_ids','verifier_revision']
        OR $2 - ARRAY['artifact','candidate','submission','basis_version_ids','verifier_revision'] <> '{}'::jsonb
        OR NOT lean_artifact_ref_valid($2 -> 'artifact')
        OR (original.audit ? 'native_capsule'
            AND $2 -> 'artifact' IS DISTINCT FROM original.audit -> 'native_capsule')
        OR $2 -> 'candidate' IS DISTINCT FROM original.audit -> 'candidate'
        OR $2 -> 'submission' IS DISTINCT FROM original.audit -> 'submission'
        OR $2 -> 'basis_version_ids' IS DISTINCT FROM original.audit -> 'basis_version_ids'
        OR jsonb_typeof($2 -> 'verifier_revision') IS DISTINCT FROM 'string'
        OR NOT coalesce($2 ->> 'verifier_revision' ~ '^[0-9a-f]{64}$', false)
        OR EXISTS (SELECT FROM verification_input_retirements retired
            WHERE (retired.input_kind, retired.sha256::text) IN (
                ('source', $2 #>> '{artifact,sha256}'), ('verifier', $2 ->> 'verifier_revision'))) THEN
        RAISE EXCEPTION 'a replay archive must match the exact live native Lean receipt and its actual basis'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO native_lean_replay_archives (evidence_id, binding) VALUES ($1, $2)
    ON CONFLICT ON CONSTRAINT native_lean_replay_archives_pkey DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM native_lean_replay_archives archive WHERE archive.evidence_id = $1;
        IF saved.binding IS DISTINCT FROM $2 THEN
            RAISE EXCEPTION 'this receipt already has a different checked replay archive' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF original.promoted AND original.review_status = 'approved' THEN
        PERFORM publish_native_lean_theorem($1);
    END IF;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.lean_receipt_has_native_source(evidence_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM evidence e JOIN lean_checks c ON c.id = e.lean_check_id
        WHERE e.id = $1 AND c.body ?| ARRAY['lean_source','native_replay','lean_composition']
    )
$$;
