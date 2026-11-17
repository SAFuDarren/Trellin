-- Report bytes are bound to the verifier attempt separately from candidate bytes.
CREATE OR REPLACE VIEW rm.retired_lean_checks WITH (security_barrier = true) AS
    SELECT DISTINCT c.id
    FROM rm.lean_checks c
    JOIN rm.lean_statements s ON s.id = c.statement_id
    JOIN rm.verification_input_retirements r ON
        (r.input_kind = 'verifier' AND r.sha256 IN (coalesce(c.body ->> 'checker_revision',
            s.body #>> '{environment,inputs,RMFlow/Audit.lean}'), c.body ->> 'local_checker_revision')) OR
        (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                      c.body ->> 'verification_environment_sha256')) OR
        (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
            c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
            c.body ->> 'refutation_source_sha256', c.body #>> '{report,sha256}'));

CREATE FUNCTION rm.guard_lean_report_retirement() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM lock_live_foundation();
    IF EXISTS (
        SELECT 1 FROM verification_input_retirements retired
        WHERE retired.input_kind = 'source' AND retired.sha256 = NEW.body #>> '{report,sha256}'
    ) THEN
        RAISE EXCEPTION 'Lean verification report requires recheck' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_lean_report_retirement BEFORE INSERT ON rm.lean_checks
    FOR EACH ROW EXECUTE FUNCTION rm.guard_lean_report_retirement();
REVOKE ALL ON FUNCTION rm.guard_lean_report_retirement() FROM PUBLIC;

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
            AND NOT EXISTS (SELECT FROM retired_lean_checks retired WHERE retired.id = c.id)
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
