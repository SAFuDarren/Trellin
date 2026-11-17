-- Read receipt liveness without reading proof readiness. A proof cannot close
-- its own clarification by relying on a view that already requires closure.
CREATE FUNCTION rm.condition_receipt_live(evidence_id uuid) RETURNS boolean
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
                (r.input_kind = 'verifier' AND r.sha256 = coalesce(c.body ->> 'checker_revision',
                    s.body #>> '{environment,inputs,RMFlow/Audit.lean}'))
                OR (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                                 c.body ->> 'verification_environment_sha256'))
                OR (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
                    c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}', c.body ->> 'refutation_source_sha256')))
        )
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

CREATE VIEW rm.research_condition_effective WITH (security_barrier = true) AS
    SELECT q.id, q.goal_id, q.clarification_id, q.body, q.requested_at, decision.action,
        replacement.replacement_request_id,
        decision.action IS NOT DISTINCT FROM 'derived' AND replacement.request_id IS NULL
            AND NOT rm.condition_receipt_live(decision.evidence_id) AS stale_derivation
    FROM rm.research_condition_requests q
    LEFT JOIN rm.research_condition_decisions decision ON decision.request_id = q.id
    LEFT JOIN rm.research_condition_rechecks replacement ON replacement.request_id = q.id;

CREATE OR REPLACE VIEW rm.formalization_clarification_status WITH (security_barrier = true) AS
    SELECT q.*, (v.request_id IS NOT NULL AND NOT coalesce(review.stale_derivation, false)) AS resolved,
        v.resolved_by, v.resolved_at, v.reason AS resolution_reason
    FROM rm.clarification_requests q LEFT JOIN rm.clarification_resolutions v ON v.request_id = q.id
    LEFT JOIN rm.research_condition_effective review ON review.clarification_id = q.id;

CREATE OR REPLACE VIEW rm.formalization_status WITH (security_barrier = true) AS
    SELECT p.id, p.review_id, p.claim_id, p.version, p.packet, p.packet_sha256, p.proposed_by, p.proposed_at,
        a.packet_id IS NOT NULL AS approved, a.approved_by, a.approved_at,
        p.version <> r.active_version AS superseded, pending.count AS pending_clarifications,
        p.claim_id IS NOT NULL AND p.version = r.active_version AND a.packet_id IS NOT NULL
            AND pending.count = 0 AS proof_ready,
        a.confirmed_item_ids, a.reason AS approval_reason, link.basis_packet_id, basis.packet_sha256 AS basis_packet_sha256
    FROM rm.formalization_packets p
    JOIN rm.formalization_reviews r ON r.id = p.review_id
    LEFT JOIN rm.exploration_goal_links link ON link.goal_review_id = p.review_id
    LEFT JOIN rm.formalization_packets basis ON basis.id = link.basis_packet_id
    LEFT JOIN rm.formalization_approvals a ON a.packet_id = p.id
    CROSS JOIN LATERAL (
        SELECT (
            (SELECT count(*) FROM rm.formalization_clarification_status c WHERE c.packet_id = p.id AND NOT c.resolved)
            + (SELECT count(*) FROM rm.research_goal_selections s
               JOIN rm.research_condition_effective q ON q.goal_id = s.goal_id
               WHERE s.packet_id = p.id AND q.clarification_id IS NULL AND q.body ->> 'kind' = 'interpretation'
                 AND (q.action IS NULL OR q.action = 'revise' OR q.stale_derivation))
        )::integer AS count
    ) pending;

CREATE OR REPLACE VIEW rm.research_goal_status WITH (security_barrier = true) AS
    SELECT g.*, a.approved_at, a.actor_ref AS approved_by, s.conjecture_id, s.packet_id, s.claim_id,
           claim.status AS claim_status, packet.proof_ready,
           NOT EXISTS (SELECT 1 FROM rm.research_goal_basis b
                       WHERE b.goal_id = g.id AND b.needs_recheck) AS basis_current,
           EXISTS (SELECT 1 FROM rm.research_condition_effective q
                   WHERE q.goal_id = g.id AND q.body ->> 'kind' = 'interpretation'
                       AND (q.action IS NULL OR q.action = 'revise' OR q.stale_derivation)) AS interpretation_hold
    FROM rm.research_goals g
    LEFT JOIN rm.research_goal_approvals a ON a.goal_id = g.id
    LEFT JOIN rm.research_goal_selections s ON s.goal_id = g.id
    LEFT JOIN rm.formalization_status packet ON packet.id = s.packet_id
    LEFT JOIN rm.claim_foundation_status claim ON claim.id = s.claim_id;

CREATE FUNCTION rm.guard_stale_research_condition() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT FROM research_condition_effective q WHERE q.goal_id = NEW.goal_id
        AND q.stale_derivation AND q.body ->> 'kind' = 'interpretation') THEN
        RAISE EXCEPTION 'recheck stale interpretation evidence before choosing a conjecture' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_stale_condition BEFORE INSERT ON rm.research_conjectures
    FOR EACH ROW EXECUTE FUNCTION rm.guard_stale_research_condition();
CREATE TRIGGER check_stale_condition BEFORE INSERT ON rm.research_goal_selections
    FOR EACH ROW EXECUTE FUNCTION rm.guard_stale_research_condition();

CREATE FUNCTION rm.reopen_research_condition(request_id uuid, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE original research_condition_requests;
DECLARE replacement uuid;
BEGIN
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'reopening requires a researcher and reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT original FROM research_condition_requests q WHERE q.id = $1;
    PERFORM FROM research_goals g WHERE g.id = original.goal_id FOR UPDATE;
    SELECT r.replacement_request_id INTO replacement FROM research_condition_rechecks r WHERE r.request_id = $1;
    IF FOUND THEN RETURN replacement; END IF;
    PERFORM lock_live_foundation();
    IF NOT EXISTS (SELECT FROM research_condition_effective q WHERE q.id = $1 AND q.stale_derivation) THEN
        RAISE EXCEPTION 'only a derivation that needs rechecking can reopen this way' USING ERRCODE = '23514';
    END IF;
    SELECT request.id INTO STRICT replacement FROM request_research_condition(original.goal_id,
        original.body || jsonb_build_object(
            'question', format('Recheck derivation %s. %s', original.id, left(original.body ->> 'question', 900)),
            'reopened_from', original.id, 'reopened_by', actor_ref)) request;
    INSERT INTO research_condition_rechecks (request_id, replacement_request_id, reason)
        VALUES (original.id, replacement, reason);
    RETURN replacement;
END $$;

-- The extra entries give the existing review interface the same kind and
-- ordering as any other unresolved material condition.
ALTER VIEW rm.research_actionable_review_inbox RENAME TO research_actionable_review_inbox_before_liveness;
CREATE VIEW rm.research_actionable_review_inbox WITH (security_barrier = true) AS
    SELECT * FROM rm.research_actionable_review_inbox_before_liveness
    UNION ALL SELECT 'decide_condition', q.id, s.claim_id, q.requested_at,
        'The supporting proof no longer discharges this interpretation. Reopen its review.',
        jsonb_build_object('goal_id',q.goal_id,'kind','interpretation','requires_reopen',true,
                           'statement',q.body -> 'statement','impact',q.body -> 'impact')
    FROM rm.research_condition_effective q LEFT JOIN rm.research_goal_selections s ON s.goal_id = q.goal_id
    WHERE q.stale_derivation AND q.body ->> 'kind' = 'interpretation';

CREATE FUNCTION rm.fence_evidence_return() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM advance_foundation();
    RETURN NEW;
END $$;
CREATE TRIGGER fence_evidence_return BEFORE INSERT ON rm.decisions
    FOR EACH ROW WHEN (NEW.action = 'return' AND NEW.evidence_id IS NOT NULL)
    EXECUTE FUNCTION rm.fence_evidence_return();
CREATE TRIGGER fence_lean_return BEFORE INSERT ON rm.lean_check_reviews
    FOR EACH ROW WHEN (NEW.action = 'return')
    EXECUTE FUNCTION rm.fence_evidence_return();

REVOKE ALL ON FUNCTION rm.condition_receipt_live(uuid), rm.guard_stale_research_condition(),
    rm.reopen_research_condition(uuid,text,text), rm.fence_evidence_return() FROM PUBLIC;
-- The security-barrier views evaluate this function as the caller. Its only
-- return value is a gate bit, checked against an opaque evidence ID.
GRANT EXECUTE ON FUNCTION rm.condition_receipt_live(uuid) TO rm_human, rm_agent, rm_verifier, rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.reopen_research_condition(uuid,text,text) TO rm_human;
GRANT SELECT ON rm.research_actionable_review_inbox TO rm_human;

CREATE OR REPLACE FUNCTION rm.condition_derivation_current(request_id uuid, evidence_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM research_condition_requests question JOIN research_goals target ON target.id = question.goal_id
        JOIN evidence e ON e.id = $2
        JOIN claims c ON c.id = e.claim_id AND c.contract_sha256 = e.contract_sha256
        JOIN research_goal_selections chosen ON chosen.claim_id = c.id
        JOIN research_goals witness ON witness.id = chosen.goal_id
        LEFT JOIN lean_checks checked ON checked.id = e.lean_check_id
        LEFT JOIN lean_statements statement ON statement.id = checked.statement_id
        CROSS JOIN LATERAL (
            SELECT (target.body -> 'basis_version_ids') || coalesce((
                SELECT jsonb_agg(attached.version_id)
                FROM research_goal_selections selection
                JOIN packet_library_items attached ON attached.packet_id = selection.packet_id
                WHERE selection.goal_id = target.id
            ), '[]'::jsonb) AS versions
        ) admitted
        WHERE question.id = $1
          AND c.domain = target.body ->> 'domain' AND c.sensitivity = target.body ->> 'sensitivity'
          AND c.formal_skeleton ->> 'format' = 'rm-goal-v1'
          AND c.formal_skeleton -> 'target' = question.body -> 'formal'
          AND admitted.versions @> (witness.body -> 'basis_version_ids')
          AND (statement.body #> '{environment,external}' IS NULL OR EXISTS (
              SELECT 1 FROM research_goal_selections destination
              JOIN lean_statement_status destination_statement ON destination_statement.packet_id = destination.packet_id
                  AND destination_statement.approved AND destination_statement."current"
              WHERE destination.goal_id = target.id AND destination_statement.body #> '{environment,external}'
                  = statement.body #> '{environment,external}'
          ))
          AND condition_receipt_live(e.id)
          AND proof_dependencies_authorized('receipt', e.id, admitted.versions)
    )
$$;

CREATE OR REPLACE FUNCTION rm.require_formalization_ready(subject_id uuid, proof_payload jsonb DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE series_id uuid;
BEGIN
    SELECT p.review_id INTO series_id FROM formalization_packets p WHERE p.claim_id = subject_id LIMIT 1;
    IF series_id IS NULL THEN
        IF proof_payload ?| ARRAY['formalization_id', 'formalization_sha256'] THEN
            RAISE EXCEPTION 'proof names a formalization not assigned to its claim' USING ERRCODE = '23514';
        END IF;
        RETURN;
    END IF;
    PERFORM 1 FROM formalization_reviews r WHERE r.id = series_id FOR SHARE;
    PERFORM lock_live_foundation();
    IF NOT EXISTS (
        SELECT 1 FROM formalization_status s WHERE s.claim_id = subject_id AND s.proof_ready
          AND (proof_payload IS NULL OR proof_payload @> jsonb_build_object(
              'formalization_id', s.id, 'formalization_sha256', s.packet_sha256))
    ) THEN
        RAISE EXCEPTION 'proof requires the current approved formalization and resolved clarifications'
            USING ERRCODE = '23514';
    END IF;
END $$;

