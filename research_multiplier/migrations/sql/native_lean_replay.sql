-- The capsule is stored by content hash in the selected workspace. Its binding
-- is append-only and can only be registered by the independent verifier.
CREATE TABLE rm.native_lean_replay_archives (
    evidence_id uuid PRIMARY KEY REFERENCES rm.evidence(id),
    binding jsonb NOT NULL CHECK (jsonb_typeof(binding) = 'object'),
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER native_lean_replay_archives_immutable BEFORE UPDATE OR DELETE ON rm.native_lean_replay_archives
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
REVOKE ALL ON rm.native_lean_replay_archives FROM PUBLIC;

-- Archive construction is distinct from the original proof check. A retired
-- archive builder invalidates its exports and consumers through the item graph.
CREATE FUNCTION rm.retire_native_lean_archive_exports() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    INSERT INTO formal_item_retirements (version_id, reason)
    SELECT exported.version_id, NEW.reason FROM lean_core_exports exported
    JOIN native_lean_replay_archives archive ON archive.evidence_id = exported.evidence_id
    WHERE archive.binding ->> 'verifier_revision' = NEW.sha256
    ON CONFLICT DO NOTHING;
    RETURN NEW;
END $$;

-- A frozen consequent cannot be attached as an assumption. A current native
-- export from another claim is a proved implication with checked premises.
-- Only the verifier import path may consider it for independent replay.
CREATE OR REPLACE FUNCTION rm.guard_target_library_item() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE selected_goal uuid;
BEGIN
    IF TG_TABLE_NAME = 'lean_core_imports' AND EXISTS (
        SELECT FROM current_lean_core_theorems exported
        JOIN evidence source ON source.id = exported.evidence_id
        JOIN formalization_packets destination ON destination.id = NEW.packet_id
        WHERE exported.id = NEW.version_id AND exported.body ->> 'format' = 'lean-native-proof'
            AND source.claim_id <> destination.claim_id
    ) THEN
        RETURN NEW;
    END IF;
    SELECT selection.goal_id INTO selected_goal FROM research_goal_selections selection WHERE selection.packet_id = NEW.packet_id;
    IF EXISTS (
        SELECT FROM research_target_for_goal(selected_goal) target
        JOIN formal_item_versions version ON version.id = NEW.version_id
        WHERE target_as_basis(target.body, version.body)
    ) THEN
        RAISE EXCEPTION 'the frozen target cannot be attached as a proof input' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_reviewed_target_basis() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE proof_packet uuid;
DECLARE replay_version text;
DECLARE allowed jsonb;
BEGIN
    IF NEW.lean_check_id IS NULL OR NOT EXISTS (
        SELECT FROM research_goal_selections selected WHERE selected.claim_id = NEW.claim_id
            AND is_target_first_goal(selected.goal_id)
    ) THEN
        RETURN NEW;
    END IF;
    SELECT statement.packet_id, checked.body #>> '{native_replay,version_id}' INTO STRICT proof_packet, replay_version
    FROM lean_checks checked JOIN lean_statements statement ON statement.id = checked.statement_id
    WHERE checked.id = NEW.lean_check_id AND statement.claim_id = NEW.claim_id;
    SELECT coalesce(jsonb_agg(permitted.version_id), '[]'::jsonb) INTO allowed
    FROM (
        SELECT version_id FROM packet_basis_items WHERE packet_id = proof_packet
        UNION
        SELECT version_id FROM packet_library_items WHERE packet_id = proof_packet
    ) permitted;
    IF replay_version IS NOT NULL THEN
        -- The imported proof establishes H -> G. The destination verifier
        -- already discharged H and checked its other actual references.
        -- Keep the source edge for liveness, without treating the source's
        -- historical proof branches as additional destination assumptions.
        IF NOT EXISTS (
            SELECT FROM lean_core_imports imported
            JOIN current_lean_core_theorems exported ON exported.id = imported.version_id
            WHERE imported.packet_id = proof_packet AND imported.version_id::text = replay_version
                AND exported.body ->> 'format' = 'lean-native-proof'
        ) OR EXISTS (
            SELECT FROM verification_basis_use used WHERE used.check_id = NEW.lean_check_id
                AND used.dependency_kind = 'actual' AND used.version_id::text <> replay_version
                AND NOT coalesce(proof_dependencies_authorized('item', used.version_id, allowed), false)
        ) THEN
            RAISE EXCEPTION 'the verified replay uses facts outside the destination reviewed basis'
                USING ERRCODE = '23514';
        END IF;
    ELSIF NOT coalesce(lean_check_basis_closed(NEW.lean_check_id, allowed), false) THEN
        RAISE EXCEPTION 'the verified proof or refutation uses facts outside the reviewed basis'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER retire_native_lean_archive_exports AFTER INSERT ON rm.verification_input_retirements
    FOR EACH ROW WHEN (NEW.input_kind = 'verifier')
    EXECUTE FUNCTION rm.retire_native_lean_archive_exports();
REVOKE ALL ON FUNCTION rm.retire_native_lean_archive_exports() FROM PUBLIC;

CREATE FUNCTION rm.publish_native_lean_theorem(evidence_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE original record;
DECLARE saved formal_item_versions;
BEGIN
    PERFORM lock_live_foundation();
    SELECT e.id, e.claim_id, e.contract_sha256, e.code_revision, e.environment_sha256,
           c.statement AS natural, checked.id AS check_id, checked.body AS audit,
           s.id AS statement_id, s.statement_sha256, s.packet_id, s.body AS approved,
           p.packet_sha256, proof.id AS attempt_id, archived.binding
    INTO original
    FROM evidence_foundation_status e
    JOIN claims c ON c.id = e.claim_id AND c.status = 'proved' AND c.decisive_evidence_id = e.id
    JOIN lean_checks checked ON checked.id = e.lean_check_id
    JOIN lean_statement_status s ON s.id = checked.statement_id AND s.check_ready
    JOIN formalization_status p ON p.id = s.packet_id
    JOIN lean_execution_links execution ON execution.check_id = checked.id AND execution.target = 'goal'
        AND execution.verification_attempt_id = e.verification_attempt_id
    JOIN attempts proof ON proof.id = execution.verification_attempt_id AND proof.role = 'verify'
        AND proof.finding = 'exact_proof'
    JOIN native_lean_replay_archives archived ON archived.evidence_id = e.id
    WHERE e.id = $1 AND c.domain = 'lean-core' AND e.kind = 'lean'
        AND e.review_status = 'approved' AND e.verdict = 'verified' AND e.diff_status = 'exact'
        AND NOT e.needs_recheck AND s.body ->> 'theorem' = 'RMTheory.expected'
        AND NOT EXISTS (SELECT FROM verification_input_retirements retired
            WHERE (retired.input_kind, retired.sha256::text) IN (
                ('source', archived.binding #>> '{artifact,sha256}'),
                ('verifier', archived.binding ->> 'verifier_revision')));
    IF NOT FOUND THEN
        RAISE EXCEPTION 'a native Lean receipt needs a current promoted proof and a portable structured proof or checked replay archive'
            USING ERRCODE = '23514';
    END IF;

    saved := insert_formal_item('lean:export:' || original.id, 'lean',
        jsonb_build_object('kind', 'theorem', 'format', 'lean-native-proof', 'natural', original.natural,
            'formal', jsonb_build_object('lean', jsonb_build_object(
                'proposition', original.approved -> 'theorem_type',
                'antecedents', original.approved #> '{scope,lean_hypotheses}')),
            'source', jsonb_build_object(
                'kind', 'lean', 'evidence_id', original.id, 'claim_id', original.claim_id,
                'contract_sha256', original.contract_sha256, 'check_id', original.check_id,
                'verification_attempt_id', original.attempt_id, 'statement_id', original.statement_id,
                'statement_sha256', original.statement_sha256, 'packet_id', original.packet_id,
                'packet_sha256', original.packet_sha256, 'trusted_source', original.approved -> 'trusted_source',
                'checker', original.approved -> 'checker', 'checker_revision', original.code_revision,
                'environment', original.approved -> 'environment', 'environment_sha256', original.environment_sha256,
                'candidate', original.audit -> 'candidate', 'submission', original.audit -> 'submission',
                'basis_version_ids', original.binding -> 'basis_version_ids',
                'replay', original.binding -> 'artifact', 'sha256', original.binding #>> '{artifact,sha256}',
                'replay_builder_revision', original.binding -> 'verifier_revision')));
    IF EXISTS (SELECT FROM foundation_stale WHERE kind = 'item' AND id = saved.id) THEN
        RAISE EXCEPTION 'this Lean theorem export has been retired' USING ERRCODE = '23514';
    END IF;
    INSERT INTO formal_lean_derivations (version_id, packet_id, check_id)
        VALUES (saved.id, original.packet_id, original.check_id) ON CONFLICT (version_id) DO NOTHING;
    INSERT INTO lean_core_exports (version_id, evidence_id)
        VALUES (saved.id, original.id) ON CONFLICT (version_id) DO NOTHING;
    RETURN saved;
END $$;
REVOKE ALL ON FUNCTION rm.publish_native_lean_theorem(uuid) FROM PUBLIC;

CREATE FUNCTION rm.register_native_lean_replay(evidence_id uuid, binding jsonb)
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
        AND checked.body ?| ARRAY['lean_source','native_replay'];
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

-- The structured job path continues to bind its original environment. Both
-- native methods also bind a current measured Lake build when imports exist.
CREATE OR REPLACE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE validation jsonb := job.payload -> 'lake_validation';
DECLARE replay jsonb := job.payload -> 'native_replay';
DECLARE mappings jsonb;
DECLARE external_modules boolean;
BEGIN
    anchor := require_prior_lake_lean_job(job);
    external_modules := coalesce(jsonb_typeof(anchor.body #> '{environment,external,modules}') = 'array'
        AND anchor.body #> '{environment,external,modules}' <> '[]'::jsonb, false);

    IF job.payload ? 'native_replay' THEN
        IF job.payload ?| ARRAY['lean_source','local_lemma_ids','local_checker_revision','refutation_source_sha256']
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
    IF NOT job.payload ?| ARRAY['lean_source','native_replay'] OR NOT external_modules
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
    IF NEW.payload ?| ARRAY['lake_validation','native_replay'] AND (
        NEW.kind <> 'verify' OR NOT NEW.payload ? 'lean_statement_id'
            OR NOT NEW.payload ?| ARRAY['lean_source','native_replay']
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
    FOR EACH ROW WHEN (NEW.payload ?| ARRAY['lean_source','native_replay','lake_validation'])
    EXECUTE FUNCTION rm.guard_new_lean_lake_job();

CREATE FUNCTION rm.guard_native_lean_replay_receipt() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE replay jsonb;
DECLARE observed_constants jsonb := NEW.body #> '{dependencies,constants}';
DECLARE actual_versions jsonb;
BEGIN
    IF NEW.body ? 'verification_attempt_id' THEN
        SELECT j.payload -> 'native_replay' INTO replay FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = (NEW.body ->> 'verification_attempt_id')::uuid;
    END IF;
    IF NEW.body -> 'native_replay' IS DISTINCT FROM replay
        OR (NEW.body ? 'native_capsule' AND (
            NOT NEW.body ?| ARRAY['native_replay','lean_source'] OR NOT lean_artifact_ref_valid(NEW.body -> 'native_capsule')
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
CREATE TRIGGER guard_native_lean_replay_receipt BEFORE INSERT ON rm.lean_checks
    FOR EACH ROW EXECUTE FUNCTION rm.guard_native_lean_replay_receipt();
REVOKE ALL ON FUNCTION rm.guard_native_lean_replay_receipt() FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.local_final_checker_revision(payload jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT CASE WHEN $1 ? 'lean_statement_id'
        AND $1 -> 'checker' = '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
    THEN CASE
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
            c.body #>> '{native_replay,verifier_revision}')) OR
        (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
            c.body ->> 'verification_environment_sha256', c.body #>> '{lake_validation,runtime_sha256}')) OR
        (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
            c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
            c.body ->> 'refutation_source_sha256', c.body #>> '{report,sha256}',
            c.body #>> '{native_replay,artifact,sha256}'));
REVOKE ALL ON FUNCTION rm.register_native_lean_replay(uuid,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.register_native_lean_replay(uuid,jsonb) TO rm_verifier;

CREATE OR REPLACE FUNCTION rm.lean_receipt_has_native_source(evidence_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM evidence e JOIN lean_checks c ON c.id = e.lean_check_id
        WHERE e.id = $1 AND c.body ?| ARRAY['lean_source','native_replay']
    )
$$;

CREATE OR REPLACE FUNCTION rm.guard_portable_lean_export() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF lean_receipt_has_native_source(NEW.evidence_id) AND NOT EXISTS (
        SELECT FROM native_lean_replay_archives archive JOIN formal_item_versions item ON item.id = NEW.version_id
        WHERE archive.evidence_id = NEW.evidence_id AND item.body ->> 'format' = 'lean-native-proof'
            AND item.body #> '{source,replay}' = archive.binding -> 'artifact'
    ) THEN
        RAISE EXCEPTION 'a native Lean source receipt needs a portable structured proof or checked replay archive before cross-claim export'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

ALTER FUNCTION rm.publish_lean_core_theorem(uuid) RENAME TO publish_prior_lean_core_theorem;
REVOKE ALL ON FUNCTION rm.publish_prior_lean_core_theorem(uuid) FROM rm_verifier;
CREATE FUNCTION rm.publish_lean_core_theorem(evidence_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF lean_receipt_has_native_source($1) THEN
        RETURN publish_native_lean_theorem($1);
    END IF;
    RETURN publish_prior_lean_core_theorem($1);
END $$;
REVOKE ALL ON FUNCTION rm.publish_lean_core_theorem(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.publish_lean_core_theorem(uuid) TO rm_verifier;

-- A source receipt can be promoted before an archive has been regenerated.
-- Its original claim stays eligible for adoption in that situation.
CREATE OR REPLACE FUNCTION rm.capture_promoted_lean_core_theorem() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF lean_receipt_has_native_source(NEW.decisive_evidence_id) THEN
        IF EXISTS (SELECT FROM native_lean_replay_archives archive WHERE archive.evidence_id = NEW.decisive_evidence_id
            AND NOT EXISTS (SELECT FROM verification_input_retirements retired
                WHERE (retired.input_kind, retired.sha256::text) IN (
                    ('source', archive.binding #>> '{artifact,sha256}'),
                    ('verifier', archive.binding ->> 'verifier_revision'))))
        THEN
            PERFORM publish_native_lean_theorem(NEW.decisive_evidence_id);
        END IF;
    ELSIF NEW.sensitivity = 'public' AND NOT lean_receipt_has_local_inputs(NEW.decisive_evidence_id) THEN
        PERFORM publish_prior_lean_core_theorem(NEW.decisive_evidence_id);
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER capture_promoted_lean_core_theorem ON rm.claims;
CREATE TRIGGER capture_promoted_lean_core_theorem AFTER UPDATE OF status ON rm.claims
    FOR EACH ROW WHEN (
        OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'proved' AND NEW.domain = 'lean-core'
    ) EXECUTE FUNCTION rm.capture_promoted_lean_core_theorem();

CREATE OR REPLACE FUNCTION rm.admit_lean_core_theorem(packet_id uuid, version_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved formal_item_versions;
DECLARE target record;
DECLARE runtime jsonb;
BEGIN
    SELECT f.claim_id, f.packet_sha256, c.sensitivity INTO target FROM formalization_status f
    JOIN claims c ON c.id = f.claim_id
    WHERE f.id = $1 AND f.proof_ready AND c.domain = 'lean-core';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'a promoted Lean Core theorem needs an approved Lean Core destination'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(target.claim_id, jsonb_build_object(
        'formalization_id', packet_id, 'formalization_sha256', target.packet_sha256));
    PERFORM lock_live_foundation();
    SELECT s.body #> '{environment,external}' INTO runtime FROM lean_statement_status s
    WHERE s.packet_id = $1 AND s.check_ready;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'the destination needs a current approved Lean statement' USING ERRCODE = '23514';
    END IF;
    SELECT v.* INTO saved FROM formal_item_versions v
    JOIN current_lean_core_theorems available ON available.id = v.id
    JOIN evidence original ON original.id = available.evidence_id
    JOIN claims source_claim ON source_claim.id = original.claim_id
    WHERE v.id = $2 AND source_claim.id <> target.claim_id
        AND (source_claim.sensitivity = 'public' OR target.sensitivity = 'restricted')
        AND (v.body ->> 'format' = 'lean-native-proof'
             OR (v.body #> '{source,environment,external}') IS NOT DISTINCT FROM runtime);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'the cited theorem needs a current promoted proof from another claim with a compatible reviewed Lean scope'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_core_imports (packet_id, version_id)
        VALUES ($1, saved.id) ON CONFLICT ON CONSTRAINT lean_core_imports_pkey DO NOTHING;
    RETURN saved;
END $$;
