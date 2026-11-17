CREATE OR REPLACE FUNCTION rm.lean_working_lemma_candidates(destination_id uuid, targets text[])
RETURNS TABLE (lemma_id uuid, proposition text, submission jsonb, contract jsonb, basis_version_ids jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT local.id, local.proposition, local.body -> 'submission', source.contract_snapshot,
           local.body -> 'basis_version_ids'
    FROM lean_statement_status destination
    JOIN claims target ON target.id = destination.claim_id AND target.status = 'open'
    JOIN local_lean_lemmas local ON local.statement_id <> destination.id
    JOIN lean_statement_status origin ON origin.id = local.statement_id AND origin.check_ready
    JOIN claims source ON source.id = origin.claim_id AND source.id <> target.id
        AND source.status IN ('open', 'proved')
        AND (source.sensitivity = 'public' OR target.sensitivity = 'restricted')
    WHERE destination.id = $1 AND destination.check_ready AND target.domain = 'lean-core' AND source.domain = target.domain
        AND cardinality($2) BETWEEN 1 AND 64
        AND (local.proposition = ANY($2) OR EXISTS (
            SELECT 1 FROM unnest($2) AS hint(proposition)
            WHERE lean_working_proposition_shape(local.proposition) = lean_working_proposition_shape(hint.proposition)
        ))
        AND origin.body #> '{environment,external}' IS NOT DISTINCT FROM destination.body #> '{environment,external}'
        AND lean_artifact_ref_valid(local.body -> 'submission')
        AND NOT EXISTS (
            SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = local.id
        )
    ORDER BY (local.proposition = ANY($2)) DESC, local.recorded_at DESC, local.id
    LIMIT 48;
$$;

-- Read one verified proof graph. The bound agrees with the local proof
-- compiler's limit and the scope predicate matches candidate discovery.
CREATE FUNCTION rm.lean_working_lemma_graph(destination_id uuid, root_id uuid)
RETURNS TABLE (lemma_id uuid, proposition text, submission jsonb, basis_version_ids jsonb, local_lemma_ids jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE scope AS MATERIALIZED (
        SELECT local.statement_id
        FROM lean_statement_status destination
        JOIN claims target ON target.id = destination.claim_id AND target.status = 'open'
        JOIN local_lean_lemmas local ON local.id = $2 AND local.statement_id <> destination.id
        JOIN lean_statement_status origin ON origin.id = local.statement_id AND origin.check_ready
        JOIN claims source ON source.id = origin.claim_id AND source.id <> target.id
            AND source.status IN ('open', 'proved')
            AND (source.sensitivity = 'public' OR target.sensitivity = 'restricted')
        WHERE destination.id = $1 AND destination.check_ready AND target.domain = 'lean-core' AND source.domain = target.domain
            AND origin.body #> '{environment,external}' IS NOT DISTINCT FROM destination.body #> '{environment,external}'
    ), reachable(id) AS (
        SELECT $2 WHERE EXISTS (SELECT 1 FROM scope)
        UNION
        SELECT ref.id::uuid
        FROM reachable
        JOIN local_lean_lemmas local ON local.id = reachable.id
        JOIN scope ON scope.statement_id = local.statement_id
        CROSS JOIN LATERAL jsonb_array_elements_text(local.body -> 'local_lemma_ids') ref(id)
    ), verified AS MATERIALIZED (
        SELECT local.id, local.proposition, local.body
        FROM reachable JOIN local_lean_lemmas local USING (id)
        JOIN scope ON scope.statement_id = local.statement_id
        WHERE lean_artifact_ref_valid(local.body -> 'submission')
            AND NOT EXISTS (
                SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = local.id
            )
    )
    SELECT v.id, v.proposition, v.body -> 'submission', v.body -> 'basis_version_ids', v.body -> 'local_lemma_ids'
    FROM verified v
    WHERE (SELECT count(*) FROM verified) = (SELECT count(*) FROM reachable)
        AND (SELECT count(*) FROM reachable) <= 24;
$$;
REVOKE ALL ON FUNCTION rm.lean_working_lemma_graph(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_working_lemma_graph(uuid,uuid) TO rm_verifier;

CREATE OR REPLACE FUNCTION rm.capture_working_lemma_replay() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE replay_binding jsonb;
DECLARE source boolean;
BEGIN
    SELECT job.payload -> 'working_lemma_replay' INTO replay_binding
    FROM attempts a JOIN jobs job ON job.id = a.job_id WHERE a.id = NEW.verification_attempt_id;
    IF replay_binding IS DISTINCT FROM NEW.body -> 'working_lemma_replay' THEN
        RAISE EXCEPTION 'the checked working replay must match the verification task' USING ERRCODE = '23514';
    END IF;
    IF replay_binding IS NULL THEN RETURN NEW; END IF;
    IF jsonb_typeof(replay_binding) IS DISTINCT FROM 'object'
        OR NOT replay_binding ?& ARRAY['lemma_id','submission']
        OR replay_binding - ARRAY['lemma_id','submission'] <> '{}'::jsonb
        OR NOT coalesce(replay_binding ->> 'lemma_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$', false)
        OR NOT lean_artifact_ref_valid(replay_binding -> 'submission') THEN
        RAISE EXCEPTION 'the working theorem replay needs its exact source reference' USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    SELECT true INTO source
    FROM local_lean_lemmas local
    JOIN lean_statement_status original ON original.id = local.statement_id AND original.check_ready
    JOIN claims origin_claim ON origin_claim.id = original.claim_id AND origin_claim.status IN ('open', 'proved')
    JOIN lean_statement_status checked ON checked.id = NEW.statement_id AND checked.check_ready
    JOIN claims target ON target.id = checked.claim_id AND target.status = 'open'
    WHERE local.id = (replay_binding ->> 'lemma_id')::uuid
        AND origin_claim.id <> target.id
        AND (origin_claim.sensitivity = 'public' OR target.sensitivity = 'restricted')
        AND local.body -> 'submission' = replay_binding -> 'submission'
        AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements_text(local.body -> 'local_lemma_ids') source_ref(id)
            JOIN local_lean_lemmas source_lemma ON source_lemma.id = source_ref.id::uuid
            WHERE NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(NEW.body -> 'local_lemma_ids') dest_ref(id)
                JOIN local_lean_lemmas dest_lemma ON dest_lemma.id = dest_ref.id::uuid
                WHERE dest_lemma.statement_id = checked.id
                    AND dest_lemma.body #>> '{working_lemma_replay,lemma_id}' = source_ref.id
                    AND dest_lemma.body #> '{working_lemma_replay,submission}' = source_lemma.body -> 'submission'
                    AND NOT EXISTS (
                        SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = dest_lemma.id
                    )
            )
        )
        AND original.body #> '{environment,external}' IS NOT DISTINCT FROM checked.body #> '{environment,external}'
        AND local.recorded_at <= NEW.recorded_at
        AND NOT EXISTS (SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = local.id);
    IF NOT coalesce(source, false) THEN
        RAISE EXCEPTION 'the originating working theorem is stale or outside this workspace scope' USING ERRCODE = '23514';
    END IF;
    INSERT INTO local_lean_lemma_dependencies (lemma_id, prerequisite_id)
        VALUES (NEW.id, (replay_binding ->> 'lemma_id')::uuid);
    RETURN NEW;
END $$;

