-- Reuse the withdrawal graph for read-only basis change previews.
CREATE VIEW rm.foundation_dependency_edges (parent_kind, parent_id, child_kind, child_id) AS
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
            AND e.kind = 'exact_certificate' AND e.verdict = 'verified';

CREATE OR REPLACE VIEW rm.foundation_stale WITH (security_barrier = true) AS
    WITH RECURSIVE roots(kind, id) AS (
        SELECT 'item', version_id FROM rm.formal_item_retirements
        UNION SELECT 'lean', check_id FROM rm.superseded_lean_refutations
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
        UNION SELECT 'lean', retired.id FROM rm.retired_lean_checks retired
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
        SELECT e.child_kind, e.child_id FROM obsolete o JOIN rm.foundation_dependency_edges e
            ON e.parent_kind = o.kind AND e.parent_id = o.id
    )
    SELECT kind, id FROM obsolete;

-- Human reviewers can see frozen scope even when its versions no longer admit proof work.
CREATE FUNCTION rm.proof_graph_frozen_basis(selected_packet uuid)
RETURNS TABLE (version_id uuid, item_label text)
LANGUAGE sql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT basis.version_id, basis.item_label
    FROM packet_basis_items basis
    WHERE basis.packet_id = $1
$$;

CREATE FUNCTION rm.proof_graph_basis_impact(selected_packet uuid, selected_version uuid)
RETURNS TABLE (kind text, id uuid, claim_id uuid, direct boolean, needs_recheck boolean)
LANGUAGE sql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH RECURSIVE affected(kind, id) AS (
        SELECT 'item'::text, basis.version_id
        FROM packet_basis_items basis
        JOIN formal_item_versions item ON item.id = basis.version_id
        WHERE basis.packet_id = $1 AND basis.version_id = $2
          AND item.body ->> 'kind' = 'hypothesis'
        UNION
        SELECT edge.child_kind, edge.child_id
        FROM affected parent JOIN foundation_dependency_edges edge
          ON (edge.parent_kind, edge.parent_id) = (parent.kind, parent.id)
    )
    SELECT a.kind, a.id,
           coalesce(check_statement.claim_id, lemma_statement.claim_id, receipt.claim_id) AS claim_id,
           EXISTS (
               SELECT FROM foundation_dependency_edges edge
               WHERE (edge.parent_kind, edge.parent_id) = ('item', $2)
                 AND (edge.child_kind, edge.child_id) = (a.kind, a.id)
           ) AS direct,
           EXISTS (SELECT FROM foundation_stale s WHERE (s.kind, s.id) = (a.kind, a.id))
    FROM affected a
    LEFT JOIN lean_checks checked ON a.kind = 'lean' AND checked.id = a.id
    LEFT JOIN lean_statements check_statement ON check_statement.id = checked.statement_id
    LEFT JOIN local_lean_lemmas lemma ON a.kind = 'local-lemma' AND lemma.id = a.id
    LEFT JOIN lean_statements lemma_statement ON lemma_statement.id = lemma.statement_id
    LEFT JOIN evidence receipt ON a.kind = 'evidence' AND receipt.id = a.id
    WHERE (a.kind, a.id) <> ('item', $2)
    UNION ALL
    SELECT DISTINCT 'scope', packet.claim_id, packet.claim_id, true, false
    FROM affected root
    JOIN packet_basis_items basis ON root.kind = 'item' AND root.id = $2 AND basis.version_id = root.id
    JOIN formalization_status packet ON packet.id = basis.packet_id AND NOT packet.superseded
$$;

REVOKE ALL ON FUNCTION rm.proof_graph_frozen_basis(uuid), rm.proof_graph_basis_impact(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.proof_graph_frozen_basis(uuid), rm.proof_graph_basis_impact(uuid, uuid) TO rm_human;
