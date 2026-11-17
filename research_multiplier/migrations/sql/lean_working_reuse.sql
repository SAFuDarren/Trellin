-- A checked local proof is a replay candidate, not an attached assumption.
-- Only the verifier can read the immutable proof reference and originating scope.
-- A coarse SQL shape keeps candidate lookup and wake-up bounded. Python
-- compares the typed binders and the kernel rechecks the destination proof.
CREATE FUNCTION rm.lean_working_proposition_shape(proposition text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT regexp_replace(regexp_replace($1, '[[:alpha:]_][[:alnum:]_'']*', '_', 'g'),
                          '[[:space:]()]', '', 'g')
$$;
REVOKE ALL ON FUNCTION rm.lean_working_proposition_shape(text) FROM PUBLIC;
CREATE FUNCTION rm.lean_working_lemma_candidates(destination_id uuid, targets text[])
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
        AND local.body -> 'local_lemma_ids' = '[]'::jsonb
        AND lean_artifact_ref_valid(local.body -> 'submission')
        AND NOT EXISTS (
            SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = local.id
        )
    ORDER BY (local.proposition = ANY($2)) DESC, local.recorded_at DESC, local.id
    LIMIT 48;
$$;

-- Reuse travels along the existing proof dependency graph. The new lemma
-- was independently checked against its destination and remains tied to
-- the exact originating working receipt for withdrawal and audit.
CREATE FUNCTION rm.capture_working_lemma_replay() RETURNS trigger
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
        AND local.body -> 'local_lemma_ids' = '[]'::jsonb
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

CREATE TRIGGER bind_working_lemma_replay AFTER INSERT ON rm.local_lean_lemmas
    FOR EACH ROW EXECUTE FUNCTION rm.capture_working_lemma_replay();

REVOKE ALL ON FUNCTION rm.lean_working_lemma_candidates(uuid,text[]), rm.capture_working_lemma_replay() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.lean_working_lemma_candidates(uuid,text[]) TO rm_verifier;


-- A newly checked, potentially relevant theorem can resume a stopped
-- destination without a human reset or another signed allowance.
ALTER FUNCTION rm.lean_inquiry_base(uuid) RENAME TO lean_inquiry_base_before_working_reuse;
CREATE FUNCTION rm.lean_inquiry_base(policy_id uuid)
RETURNS TABLE (statement_id uuid, model_rounds integer, base_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT initial.statement_id, initial.model_rounds,
           CASE WHEN candidates.ids IS NULL THEN initial.base_sha256
                ELSE encode(sha256(convert_to(jsonb_build_array(initial.base_sha256, candidates.ids)::text, 'UTF8')), 'hex')
           END
    FROM lean_inquiry_base_before_working_reuse($1) initial
    LEFT JOIN LATERAL (
        SELECT array_agg(candidate.lemma_id ORDER BY candidate.lemma_id) AS ids
        FROM lean_working_lemma_candidates(initial.statement_id, ARRAY(
            SELECT DISTINCT item ->> 'proposition'
            FROM lean_reduction_status(initial.statement_id) route
            CROSS JOIN LATERAL jsonb_array_elements(route.obligations) AS obligation(item)
            WHERE route.status = 'awaiting_obligations' AND item -> 'lemma_id' = 'null'::jsonb
            ORDER BY item ->> 'proposition'
            LIMIT 64
        )) candidate
    ) candidates ON true
$$;
REVOKE ALL ON FUNCTION rm.lean_inquiry_base(uuid) FROM PUBLIC;
