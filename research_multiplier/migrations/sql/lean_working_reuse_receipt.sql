CREATE OR REPLACE FUNCTION rm.capture_final_local_lemma_use() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
DECLARE anchor lean_statements;
DECLARE input jsonb;
DECLARE actual jsonb;
DECLARE expected_expanded jsonb;
DECLARE allowed jsonb;
BEGIN
    SELECT * INTO STRICT checked FROM lean_checks c WHERE c.id = NEW.check_id;
    SELECT j.payload INTO STRICT input FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = NEW.verification_attempt_id;
    IF NOT input ? 'local_lemma_ids' THEN
        IF checked.body ?| ARRAY['local_lemma_ids','expanded_local_lemma_ids','local_checker_revision'] THEN
            RAISE EXCEPTION 'the final verification job did not bind local theorem inputs' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    SELECT * INTO STRICT anchor FROM lean_statements s WHERE s.id = checked.statement_id;
    actual := checked.body -> 'local_lemma_ids';
    IF NEW.target NOT IN ('goal','refutation')
        OR anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR checked.body ->> 'local_checker_revision' IS DISTINCT FROM input ->> 'local_checker_revision'
        OR NOT valid_final_lemma_ids(actual)
        OR NOT valid_final_lemma_ids(checked.body -> 'expanded_local_lemma_ids')
        OR NOT (input -> 'local_lemma_ids' @> actual) THEN
        RAISE EXCEPTION 'verified local theorem usage does not match the final verification job' USING ERRCODE = '23514';
    END IF;
    IF actual IS DISTINCT FROM final_lemma_ids_from_audit(checked.body #> '{dependencies,direct_constants}')
        OR checked.body -> 'expanded_local_lemma_ids' IS DISTINCT FROM
            final_lemma_ids_from_audit(checked.body #> '{dependencies,references}') THEN
        RAISE EXCEPTION 'verified local theorem usage does not match the Lean audit references' USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_final_lemma_inputs(anchor.id, actual);
    WITH RECURSIVE ancestry(id) AS (
        SELECT id::uuid FROM jsonb_array_elements_text(actual) requested(id)
        UNION
        SELECT dependency.prerequisite_id FROM ancestry node
        JOIN local_lean_lemma_dependencies dependency ON dependency.lemma_id = node.id
        -- Cross-claim lineage is tracked for recheck in the foundation
        -- graph. Lean elaborated the replayed body in this statement.
        JOIN local_lean_lemmas prerequisite ON prerequisite.id = dependency.prerequisite_id
            AND prerequisite.statement_id = anchor.id
    )
    SELECT coalesce(jsonb_agg(id ORDER BY id), '[]'::jsonb) INTO expected_expanded FROM ancestry;
    IF expected_expanded IS DISTINCT FROM checked.body -> 'expanded_local_lemma_ids' THEN
        RAISE EXCEPTION 'verified local theorem usage is missing a transitive proof dependency' USING ERRCODE = '23514';
    END IF;
    SELECT coalesce(jsonb_agg(permitted.version_id), '[]'::jsonb) INTO allowed
    FROM (
        SELECT version_id FROM packet_basis_items WHERE packet_id = anchor.packet_id
        UNION
        SELECT version_id FROM packet_library_items WHERE packet_id = anchor.packet_id
    ) permitted;
    IF EXISTS (
        SELECT FROM jsonb_array_elements_text(expected_expanded) ancestor(id)
        JOIN local_lean_lemma_basis used ON used.lemma_id = ancestor.id::uuid
        WHERE NOT proof_dependencies_authorized('item', used.version_id, allowed)
    ) THEN
        RAISE EXCEPTION 'the final proof uses a scoped theorem outside the approved basis' USING ERRCODE = '23514';
    END IF;
    INSERT INTO verification_local_lemma_use (check_id, lemma_id)
    SELECT NEW.check_id, id::uuid FROM jsonb_array_elements_text(actual) requested(id);
    RETURN NEW;
END $$;
