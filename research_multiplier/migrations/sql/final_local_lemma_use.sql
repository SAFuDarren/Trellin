CREATE TABLE rm.verification_local_lemma_use (
    check_id uuid NOT NULL REFERENCES rm.lean_execution_links(check_id),
    lemma_id uuid NOT NULL REFERENCES rm.local_lean_lemmas(id),
    PRIMARY KEY (check_id, lemma_id)
);
CREATE INDEX verification_local_lemma_by_lemma ON rm.verification_local_lemma_use (lemma_id);
CREATE TRIGGER verification_local_lemma_use_append_only BEFORE UPDATE OR DELETE ON rm.verification_local_lemma_use
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
GRANT SELECT ON rm.verification_local_lemma_use TO rm_human, rm_verifier;

CREATE FUNCTION rm.valid_final_lemma_ids(ids jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT CASE WHEN jsonb_typeof($1) = 'array' THEN
        jsonb_array_length($1) <= 24 AND NOT EXISTS (
            SELECT FROM jsonb_array_elements($1) id
            WHERE jsonb_typeof(id) <> 'string'
              OR id #>> '{}' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        ) AND $1 = coalesce((
            SELECT jsonb_agg(DISTINCT id ORDER BY id) FROM jsonb_array_elements($1) id
        ), '[]'::jsonb)
    ELSE false END
$$;

CREATE FUNCTION rm.final_lemma_ids_from_audit(constants jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT CASE WHEN jsonb_typeof($1) = 'array' THEN coalesce((
        SELECT jsonb_agg(DISTINCT found.id ORDER BY found.id) FROM (
            SELECT right(constant, 32)::uuid AS id
            FROM jsonb_array_elements_text($1) declaration(constant)
            WHERE constant ~ '^RMTheory[.]Proof[.]basis_local_[0-9a-f]{32}$'
        ) found
    ), '[]'::jsonb) END
$$;

CREATE FUNCTION rm.require_final_lemma_inputs(anchor uuid, ids jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT valid_final_lemma_ids(ids) THEN
        RAISE EXCEPTION 'local theorem references require distinct, ordered version UUIDs' USING ERRCODE = '23514';
    END IF;
    IF ids = '[]'::jsonb THEN
        RETURN;
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements_text(ids) requested(id)
        WHERE NOT EXISTS (SELECT FROM local_lean_lemma_status(anchor) lemma
                          WHERE lemma.id = requested.id::uuid AND lemma.status = 'verified')
    ) THEN
        RAISE EXCEPTION 'local theorem is missing, out of scope, or needs recheck' USING ERRCODE = '23514';
    END IF;
END $$;

-- Keep verification and implication scopes independent. The raw predicate is
-- also needed by the clarification traversal, which must not query readiness.
CREATE FUNCTION rm.local_lean_lemma_source_live(lemma_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM local_lean_lemmas l
        JOIN lean_statements statement ON statement.id = l.statement_id
        JOIN lean_statement_approvals approved ON approved.statement_id = statement.id
        JOIN formalization_packets packet ON packet.id = statement.packet_id
        JOIN formalization_reviews series ON series.id = packet.review_id AND series.active_version = packet.version
        JOIN formalization_approvals scope ON scope.packet_id = packet.id
        JOIN claims claim ON (claim.id, claim.contract_sha256) = (statement.claim_id, statement.contract_sha256)
        WHERE l.id = $1 AND claim.status IN ('open','proved','refuted')
          AND NOT local_lean_lemma_input_retired(l.body)
          AND NOT EXISTS (SELECT FROM lean_statements newer WHERE newer.packet_id = packet.id
                          AND newer.version > statement.version)
    )
$$;

CREATE FUNCTION rm.capture_final_local_lemma_use() RETURNS trigger
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
CREATE TRIGGER capture_final_local_lemma_use AFTER INSERT ON rm.lean_execution_links
    FOR EACH ROW EXECUTE FUNCTION rm.capture_final_local_lemma_use();

REVOKE ALL ON FUNCTION rm.valid_final_lemma_ids(jsonb), rm.final_lemma_ids_from_audit(jsonb),
    rm.require_final_lemma_inputs(uuid,jsonb),
    rm.local_lean_lemma_source_live(uuid), rm.capture_final_local_lemma_use() FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
BEGIN
    IF job.payload ->> 'target' IS DISTINCT FROM 'local_lemma' THEN
        anchor := require_prior_dynamic_lean_job(job);
        IF job.payload ?| ARRAY['local_lemma_ids','local_checker_revision'] THEN
            IF job.payload ->> 'target' NOT IN ('goal','refutation')
                OR anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
                OR job.budget ->> 'max_model_calls' IS DISTINCT FROM '0'
                OR NOT coalesce(job.payload ->> 'local_checker_revision' ~ '^[0-9a-f]{64}$', false)
                OR NOT valid_final_lemma_ids(job.payload -> 'local_lemma_ids')
                OR job.payload -> 'local_lemma_ids' = '[]'::jsonb THEN
                RAISE EXCEPTION 'final Lean verification must bind its local checker and exact local version inputs'
                    USING ERRCODE = '23514';
            END IF;
            IF EXISTS (SELECT FROM verification_input_retirements retirement
                WHERE retirement.input_kind = 'verifier'
                    AND retirement.sha256 = job.payload ->> 'local_checker_revision') THEN
                RAISE EXCEPTION 'the local checker revision requires recheck' USING ERRCODE = '23514';
            END IF;
            IF EXISTS (SELECT FROM checker_hold_status held WHERE held.released_at IS NULL
                AND held.checker_revision = job.payload ->> 'local_checker_revision') THEN
                RAISE EXCEPTION 'checker revision awaiting researcher review after a monitoring alert'
                    USING ERRCODE = '23514';
            END IF;
            PERFORM require_final_lemma_inputs(anchor.id, job.payload -> 'local_lemma_ids');
        END IF;
        RETURN anchor;
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


CREATE OR REPLACE FUNCTION rm.condition_receipt_live(evidence_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE edges(parent_kind, parent_id, child_kind, child_id) AS NOT MATERIALIZED (
        SELECT 'receipt', e.id, 'check', e.lean_check_id FROM evidence e WHERE e.lean_check_id IS NOT NULL
        UNION ALL SELECT 'receipt', e.evidence_id, 'item', e.version_id FROM evidence_basis_use e
        UNION ALL SELECT 'receipt', e.id, 'receipt', decision.evidence_id FROM evidence e
            JOIN research_goal_selections selected ON selected.claim_id = e.claim_id
            JOIN research_condition_requests question ON question.goal_id = selected.goal_id
            JOIN research_condition_decisions decision ON decision.request_id = question.id
            WHERE question.body ->> 'kind' = 'interpretation' AND decision.action = 'derived'
              AND NOT EXISTS (SELECT FROM research_condition_rechecks r WHERE r.request_id = question.id)
        UNION ALL SELECT 'check', c.check_id, 'local-lemma', c.lemma_id FROM verification_local_lemma_use c
        UNION ALL SELECT 'local-lemma', d.lemma_id, 'local-lemma', d.prerequisite_id FROM local_lean_lemma_dependencies d
        UNION ALL SELECT 'local-lemma', d.lemma_id, 'item', d.version_id FROM local_lean_lemma_basis d
        UNION ALL SELECT 'local-lemma', l.id, 'item', scoped.version_id FROM local_lean_lemmas l
            JOIN lean_statements statement ON statement.id = l.statement_id
            JOIN packet_basis_items scoped ON scoped.packet_id = statement.packet_id
        UNION ALL SELECT 'check', c.check_id, 'item', c.version_id FROM verification_basis_use c
        UNION ALL SELECT 'check', c.id, 'item', b.version_id FROM lean_checks c
            JOIN lean_statements s ON s.id = c.statement_id JOIN packet_basis_items b ON b.packet_id = s.packet_id
            WHERE NOT EXISTS (SELECT FROM lean_execution_links l WHERE l.check_id = c.id)
        UNION ALL SELECT 'item', t.version_id, 'receipt', t.evidence_id FROM lean_core_exports t
        UNION ALL SELECT 'item', d.version_id, 'check', d.check_id FROM formal_lean_derivations d
        UNION ALL SELECT 'item', d.version_id, 'receipt', e.id FROM formal_certificate_derivations d
            JOIN flow_derivation_records fact ON d.derivation_id = fact.id
            JOIN evidence e ON e.verification_attempt_id = fact.verification_attempt_id
            WHERE e.kind = 'exact_certificate' AND e.verdict = 'verified'
        UNION ALL SELECT 'item', d.version_id, 'item', b.version_id FROM formal_certificate_derivations d
            JOIN flow_derivation_records fact ON d.derivation_id = fact.id
            JOIN packet_basis_items b ON b.packet_id = fact.packet_id AND b.item_label = ANY(fact.premise_item_ids)
    ), ancestry(kind, id) AS (
        SELECT 'receipt'::text, $1
        UNION
        SELECT edge.child_kind, edge.child_id FROM ancestry node
        JOIN edges edge ON (edge.parent_kind, edge.parent_id) = (node.kind, node.id)
    )
    SELECT NOT EXISTS (
        SELECT FROM ancestry node WHERE NOT CASE node.kind
        WHEN 'receipt' THEN EXISTS (
            SELECT FROM evidence e WHERE e.id = node.id AND e.verdict = 'verified' AND e.diff_status = 'exact'
            AND (SELECT review.action FROM decisions review WHERE review.evidence_id = e.id
                 AND review.action IN ('approve','return') ORDER BY review.sequence_no DESC LIMIT 1) IS DISTINCT FROM 'return'
            AND NOT EXISTS (SELECT FROM verification_input_retirements r
                WHERE (r.input_kind, r.sha256) IN (('verifier',e.code_revision),('environment',e.environment_sha256),
                                                 ('source',e.artifact_sha256)))
        )
        WHEN 'check' THEN EXISTS (
            SELECT FROM lean_checks c JOIN lean_statements s ON s.id = c.statement_id
            JOIN formalization_packets p ON p.id = s.packet_id
            JOIN formalization_reviews series ON series.id = p.review_id AND series.active_version = p.version
            WHERE c.id = node.id
            AND (SELECT review.action FROM lean_check_reviews review WHERE review.check_id = c.id
                 ORDER BY review.sequence_no DESC LIMIT 1) IS DISTINCT FROM 'return'
            AND NOT EXISTS (SELECT FROM lean_statements newer WHERE newer.packet_id = s.packet_id
                            AND newer.version > s.version)
            AND NOT EXISTS (SELECT FROM clarification_requests q
                LEFT JOIN clarification_resolutions r ON r.request_id = q.id
                WHERE q.packet_id = p.id AND r.request_id IS NULL)
            AND NOT EXISTS (SELECT FROM verification_input_retirements r WHERE
                (r.input_kind = 'verifier' AND r.sha256 IN (coalesce(c.body ->> 'checker_revision',
                    s.body #>> '{environment,inputs,RMFlow/Audit.lean}'), c.body ->> 'local_checker_revision'))
                OR (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                                 c.body ->> 'verification_environment_sha256'))
                OR (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
                    c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}', c.body ->> 'refutation_source_sha256')))
        )
        WHEN 'local-lemma' THEN local_lean_lemma_source_live(node.id)
        WHEN 'item' THEN EXISTS (
            SELECT FROM formal_item_versions item WHERE item.id = node.id
            AND NOT EXISTS (SELECT FROM formal_item_retirements retired WHERE retired.version_id = item.id)
            AND NOT EXISTS (SELECT FROM verification_input_retirements r
                            WHERE r.input_kind = 'source' AND r.sha256 = item.body #>> '{source,sha256}')
            AND NOT EXISTS (SELECT FROM lean_core_exports exported
                JOIN evidence e ON e.id = exported.evidence_id JOIN claims c ON c.id = e.claim_id
                WHERE exported.version_id = item.id AND (c.status <> 'proved' OR c.decisive_evidence_id IS DISTINCT FROM e.id))
        )
        ELSE false END
    )
$$;


CREATE OR REPLACE VIEW rm.foundation_stale WITH (security_barrier = true) AS
    WITH RECURSIVE edges(parent_kind, parent_id, child_kind, child_id) AS (
        SELECT 'item', version_id, 'lean', check_id FROM rm.verification_basis_use
        UNION ALL SELECT 'local-lemma', lemma_id, 'lean', check_id FROM rm.verification_local_lemma_use
        UNION ALL SELECT 'local-lemma', prerequisite_id, 'local-lemma', lemma_id FROM rm.local_lean_lemma_dependencies
        UNION ALL SELECT 'item', version_id, 'local-lemma', lemma_id FROM rm.local_lean_lemma_basis
        UNION ALL SELECT 'item', scoped.version_id, 'local-lemma', l.id FROM rm.local_lean_lemmas l
            JOIN rm.lean_statements statement ON statement.id = l.statement_id
            JOIN rm.packet_basis_items scoped ON scoped.packet_id = statement.packet_id
        UNION ALL SELECT 'item', basis.version_id, 'lean', legacy.id FROM rm.lean_checks legacy
            JOIN rm.lean_statements statement ON statement.id = legacy.statement_id
            JOIN rm.packet_basis_items basis ON basis.packet_id = statement.packet_id
            WHERE NOT EXISTS (SELECT 1 FROM rm.lean_execution_links execution WHERE execution.check_id = legacy.id)
        UNION ALL SELECT 'item', version_id, 'evidence', evidence_id FROM rm.evidence_basis_use
        UNION ALL SELECT 'lean', check_id, 'item', version_id FROM rm.formal_lean_derivations
        UNION ALL SELECT 'evidence', evidence_id, 'item', version_id FROM rm.lean_core_exports
        UNION ALL SELECT 'lean', lean_check_id, 'evidence', id FROM rm.evidence WHERE lean_check_id IS NOT NULL
        UNION ALL SELECT 'item', basis.version_id, 'item', bridge.version_id
            FROM rm.formal_certificate_derivations bridge JOIN rm.flow_derivation_records fact
                ON fact.id = bridge.derivation_id
            JOIN rm.packet_basis_items basis ON basis.packet_id = fact.packet_id
                AND basis.item_label = ANY(fact.premise_item_ids)
        UNION ALL SELECT 'evidence', e.id, 'item', bridge.version_id
            FROM rm.formal_certificate_derivations bridge JOIN rm.flow_derivation_records fact
                ON fact.id = bridge.derivation_id
            JOIN rm.evidence e ON e.verification_attempt_id = fact.verification_attempt_id
                AND e.kind = 'exact_certificate' AND e.verdict = 'verified'
    ), roots(kind, id) AS (
        SELECT 'item', version_id FROM rm.formal_item_retirements
        UNION SELECT 'local-lemma', l.id FROM rm.local_lean_lemmas l
            JOIN rm.lean_statements statement ON statement.id = l.statement_id
            JOIN rm.formalization_status packet ON packet.id = statement.packet_id
            WHERE NOT packet.proof_ready OR NOT rm.local_lean_lemma_source_live(l.id)
        UNION SELECT 'item', exported.version_id FROM rm.lean_core_exports exported
            WHERE (SELECT review.action FROM rm.decisions review WHERE review.evidence_id = exported.evidence_id
                AND review.action IN ('approve','return') ORDER BY review.sequence_no DESC LIMIT 1) = 'return'
        UNION SELECT 'item', derived.version_id FROM rm.formal_lean_derivations derived
            WHERE (SELECT review.action FROM rm.lean_check_reviews review WHERE review.check_id = derived.check_id
                ORDER BY review.sequence_no DESC LIMIT 1) = 'return'
        UNION SELECT 'item', publication.version_id FROM rm.lean_core_exports publication
            JOIN rm.evidence receipt ON receipt.id = publication.evidence_id
            JOIN rm.claims c ON c.id = receipt.claim_id
            JOIN rm.lean_checks checked ON checked.id = receipt.lean_check_id
            JOIN rm.lean_statement_status statement ON statement.id = checked.statement_id
            WHERE c.status <> 'proved' OR c.decisive_evidence_id IS DISTINCT FROM receipt.id
               OR NOT statement.check_ready
        UNION SELECT 'item', v.id FROM rm.formal_item_versions v JOIN rm.verification_input_retirements r
            ON r.input_kind = 'source' AND r.sha256 = v.body #>> '{source,sha256}'
        UNION SELECT 'lean', c.id FROM rm.lean_checks c
            JOIN rm.lean_statements s ON s.id = c.statement_id
            JOIN rm.verification_input_retirements r ON
                (r.input_kind = 'verifier' AND r.sha256 IN (coalesce(c.body ->> 'checker_revision',
                    s.body #>> '{environment,inputs,RMFlow/Audit.lean}'), c.body ->> 'local_checker_revision')) OR
                (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                              c.body ->> 'verification_environment_sha256')) OR
                (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
                    c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
                    c.body ->> 'refutation_source_sha256'))
        UNION SELECT 'lean', c.id FROM rm.lean_checks c JOIN rm.lean_statements s ON s.id = c.statement_id
            WHERE EXISTS (SELECT 1 FROM rm.lean_statements newer WHERE newer.packet_id = s.packet_id
                          AND newer.version > s.version)
        UNION SELECT 'item', bridge.version_id FROM rm.formal_certificate_derivations bridge
            JOIN rm.flow_derivation_records fact ON fact.id = bridge.derivation_id
            JOIN rm.attempts attempt ON attempt.id = fact.verification_attempt_id
            JOIN rm.jobs job ON job.id = attempt.job_id
            JOIN rm.verification_input_retirements r ON r.input_kind = 'verifier'
                AND r.sha256 = job.payload ->> 'checker_revision'
        UNION SELECT 'evidence', e.id FROM rm.evidence e JOIN rm.verification_input_retirements r
            ON (r.input_kind = 'verifier' AND r.sha256 = e.code_revision)
            OR (r.input_kind = 'environment' AND r.sha256 = e.environment_sha256)
            OR (r.input_kind = 'source' AND r.sha256 = e.artifact_sha256)
    ), obsolete(kind, id) AS (
        SELECT kind, id FROM roots
        UNION
        SELECT e.child_kind, e.child_id FROM obsolete o JOIN edges e
            ON e.parent_kind = o.kind AND e.parent_id = o.id
    )
    SELECT kind, id FROM obsolete;

GRANT EXECUTE ON FUNCTION rm.local_lean_lemma_source_live(uuid) TO rm_human, rm_verifier, rm_scheduler, rm_agent;

CREATE OR REPLACE FUNCTION rm.local_lean_lemma_status(anchor_id uuid)
RETURNS TABLE (id uuid, statement_id uuid, verification_attempt_id uuid, proposition text,
               body jsonb, recorded_at timestamptz, status text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT l.id, l.statement_id, l.verification_attempt_id, l.proposition, l.body, l.recorded_at,
           CASE WHEN EXISTS (SELECT FROM foundation_stale stale WHERE stale.kind = 'local-lemma' AND stale.id = l.id)
                THEN 'needs_recheck' ELSE 'verified' END
    FROM local_lean_lemmas l WHERE l.statement_id = anchor_id
$$;

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
        UNION ALL SELECT 'item', exported.version_id, 'receipt', exported.evidence_id FROM lean_core_exports exported
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

CREATE FUNCTION rm.lean_receipt_has_local_inputs(evidence_id uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM evidence e JOIN lean_execution_links execution ON execution.check_id = e.lean_check_id
        JOIN attempts a ON a.id = execution.verification_attempt_id JOIN jobs j ON j.id = a.job_id
        WHERE e.id = $1 AND coalesce(j.payload -> 'local_lemma_ids', '[]'::jsonb) <> '[]'::jsonb
    )
$$;
REVOKE ALL ON FUNCTION rm.lean_receipt_has_local_inputs(uuid) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.publish_lean_core_theorem(evidence_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE source record;
DECLARE saved formal_item_versions;
BEGIN
    PERFORM lock_live_foundation();
    IF lean_receipt_has_local_inputs(evidence_id) THEN
        RAISE EXCEPTION 'a proof using local theorems needs a portable proof before cross-claim export'
            USING ERRCODE = '23514';
    END IF;
    SELECT e.id AS eid, e.claim_id, e.contract_sha256, e.code_revision, e.environment_sha256,
           statement.id AS sid, statement.statement_sha256, statement.packet_id, statement.body AS approved,
           f.packet_sha256, c.statement AS natural, checked.id AS check_id, checked.body AS report,
           proof.id AS attempt_id, j.payload AS task
    INTO source
    FROM evidence_foundation_status e
    JOIN claims c ON c.id = e.claim_id AND c.decisive_evidence_id = e.id AND c.status = 'proved'
    JOIN lean_checks checked ON checked.id = e.lean_check_id
    JOIN lean_statement_status statement ON statement.id = checked.statement_id
    JOIN formalization_status f ON f.id = statement.packet_id
    JOIN lean_execution_links execution ON execution.check_id = checked.id AND execution.target = 'goal'
         AND execution.verification_attempt_id = e.verification_attempt_id
    JOIN attempts proof ON proof.id = execution.verification_attempt_id AND proof.role = 'verify'
         AND proof.finding = 'exact_proof'
    JOIN jobs j ON j.id = proof.job_id
    WHERE e.id = publish_lean_core_theorem.evidence_id
        AND c.domain = 'lean-core' AND c.sensitivity = 'public'
        AND e.kind = 'lean' AND e.verdict = 'verified' AND e.diff_status = 'exact'
        AND e.review_status = 'approved' AND NOT e.needs_recheck
        AND statement.check_ready
        AND statement.body ->> 'theorem' = 'RMTheory.expected'
        AND statement.body -> 'checker' = '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
        AND checked.body ->> 'checker_revision' = e.code_revision
        AND checked.body ->> 'verification_environment_sha256' = e.environment_sha256
        AND checked.body ->> 'trusted_source_sha256' = statement.body #>> '{trusted_source,sha256}'
        AND checked.body -> 'submission' = j.payload -> 'submission'
        AND checked.body -> 'candidate' = jsonb_build_object('uri', e.artifact_uri, 'sha256', e.artifact_sha256)
        AND statement.statement_sha256 = j.payload ->> 'statement_sha256';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'only the current promoted exact public Lean Core receipt can be exported'
            USING ERRCODE = '23514';
    END IF;

    saved := insert_formal_item('lean:export:' || source.eid, 'lean',
        jsonb_build_object('kind', 'theorem', 'format', 'lean-core-proof', 'natural', source.natural,
            'formal', jsonb_build_object('lean', jsonb_build_object(
                'proposition', source.approved -> 'theorem_type',
                'antecedents', source.approved #> '{scope,lean_hypotheses}')),
            'source', jsonb_build_object(
                'kind', 'lean', 'evidence_id', source.eid, 'claim_id', source.claim_id,
                'contract_sha256', source.contract_sha256,
                'check_id', source.check_id, 'verification_attempt_id', source.attempt_id,
                'statement_id', source.sid, 'statement_sha256', source.statement_sha256,
                'packet_id', source.packet_id, 'packet_sha256', source.packet_sha256,
                'trusted_source', source.approved -> 'trusted_source',
                'checker', source.approved -> 'checker', 'checker_revision', source.code_revision,
                'environment', source.approved -> 'environment',
                'environment_sha256', source.environment_sha256,
                'candidate', source.report -> 'candidate', 'submission', source.report -> 'submission',
                'basis_version_ids', source.task -> 'basis_version_ids')));
    IF EXISTS (SELECT 1 FROM foundation_stale WHERE kind = 'item' AND id = saved.id) THEN
        RAISE EXCEPTION 'this Lean theorem export has been retired' USING ERRCODE = '23514';
    END IF;
    INSERT INTO formal_lean_derivations (version_id, packet_id, check_id)
        VALUES (saved.id, source.packet_id, source.check_id) ON CONFLICT (version_id) DO NOTHING;
    INSERT INTO lean_core_exports (version_id, evidence_id)
        VALUES (saved.id, source.eid) ON CONFLICT (version_id) DO NOTHING;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.capture_promoted_lean_core_theorem() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT lean_receipt_has_local_inputs(NEW.decisive_evidence_id) THEN
        PERFORM publish_lean_core_theorem(NEW.decisive_evidence_id);
    END IF;
    RETURN NEW;
END $$;

