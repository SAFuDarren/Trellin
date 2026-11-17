-- A closed exported Lean theorem proves its full implication. Its antecedents
-- are checked at the destination call; only the source proof's actual imports
-- are inherited. A packet-local subgoal still inherits its original scope.
CREATE FUNCTION rm.proof_dependencies_authorized(root_kind text, root_id uuid, admitted jsonb)
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

CREATE FUNCTION rm.lean_check_basis_closed(check_id uuid, allowed jsonb)
RETURNS boolean LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT proof_dependencies_authorized('check', $1, $2)
$$;

CREATE FUNCTION rm.condition_derivation_current(request_id uuid, evidence_id uuid)
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
                  AND destination_statement.check_ready
              WHERE destination.goal_id = target.id AND destination_statement.body #> '{environment,external}'
                  = statement.body #> '{environment,external}'
          ))
          AND proof_dependencies_authorized('receipt', e.id, admitted.versions)
    )
$$;

-- Preserve historical dispositions and reopen the question in an independent
-- request when its source cannot discharge the condition under this policy.
CREATE TABLE rm.research_condition_rechecks (
    request_id uuid PRIMARY KEY REFERENCES rm.research_condition_decisions(request_id),
    replacement_request_id uuid UNIQUE NOT NULL REFERENCES rm.research_condition_requests(id),
    reason text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER research_condition_rechecks_append_only BEFORE UPDATE OR DELETE ON rm.research_condition_rechecks
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

DO $$
DECLARE obsolete record;
DECLARE replacement uuid;
BEGIN
    FOR obsolete IN
        SELECT q.id, q.goal_id, q.body FROM rm.research_condition_requests q
        JOIN rm.research_condition_decisions d ON d.request_id = q.id
        WHERE d.action = 'derived' AND NOT rm.condition_derivation_current(q.id, d.evidence_id)
    LOOP
        SELECT request.id INTO STRICT replacement
        FROM rm.request_research_condition(obsolete.goal_id, obsolete.body || jsonb_build_object(
            'question', format('Recheck derivation %s. %s', obsolete.id, left(obsolete.body ->> 'question', 900)),
            'reopened_from', obsolete.id)) request;
        INSERT INTO rm.research_condition_rechecks (request_id, replacement_request_id, reason)
        VALUES (obsolete.id, replacement, 'The recorded receipt could not discharge the condition under the current proof-basis checks.');
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION rm.decide_research_condition(request_id uuid, action text, actor_ref text, reason text,
    version_id uuid DEFAULT NULL, evidence_id uuid DEFAULT NULL) RETURNS rm.research_condition_decisions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE question research_condition_requests;
DECLARE target research_goals;
DECLARE saved research_condition_decisions;
DECLARE new_goal uuid;
DECLARE old_version text;
DECLARE new_basis jsonb;
BEGIN
    IF coalesce(action, '') NOT IN ('reaffirm','reject','revise','derived')
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL
        OR ((action IN ('reaffirm','revise')) <> (version_id IS NOT NULL))
        OR ((action = 'derived') <> (evidence_id IS NOT NULL)) THEN
        RAISE EXCEPTION 'provide the decision reason and its required version or verification'
            USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT question FROM research_condition_requests q WHERE q.id = request_id;
    SELECT * INTO STRICT target FROM research_goals g WHERE g.id = question.goal_id FOR UPDATE;
    PERFORM lock_live_foundation();
    IF action = 'derived' AND (
        EXISTS (SELECT 1 FROM research_condition_rechecks r WHERE r.request_id = question.id)
        OR NOT condition_derivation_current(question.id, evidence_id)
    ) THEN
        RAISE EXCEPTION 'derived conditions require current exact evidence with a complete proof basis within this frame'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO saved FROM research_condition_decisions d WHERE d.request_id = question.id;
    IF FOUND THEN
        IF ROW(saved.action,saved.actor_ref,saved.reason,saved.version_id,saved.evidence_id,saved.authenticated_role)
            IS DISTINCT FROM ROW(action,actor_ref,reason,version_id,evidence_id,session_user) THEN
            RAISE EXCEPTION 'the condition already has a different decision' USING ERRCODE = '23514';
        END IF;
        RETURN saved;
    END IF;
    IF action IN ('reaffirm','revise') THEN
        PERFORM require_research_goal_basis(jsonb_build_array(version_id));
        IF NOT EXISTS (SELECT 1 FROM formal_item_versions v WHERE v.id = version_id
            AND v.body ->> 'kind' = 'hypothesis' AND v.body -> 'formal' = question.body -> 'formal') THEN
            RAISE EXCEPTION 'the condition does not have the same formal meaning as the named hypothesis'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF action = 'reaffirm' AND NOT ((target.body -> 'basis_version_ids') ? version_id::text) THEN
        RAISE EXCEPTION 'reaffirmation can only use an existing hypothesis in this frame' USING ERRCODE = '23514';
    ELSIF action = 'revise' THEN
        IF (target.body -> 'basis_version_ids') ? version_id::text THEN
            RAISE EXCEPTION 'an existing hypothesis requires reaffirmation' USING ERRCODE = '23514';
        END IF;
        IF question.body ->> 'kind' <> 'interpretation' THEN
            RAISE EXCEPTION 'a proof route condition cannot revise the research frame' USING ERRCODE = '23514';
        END IF;
        old_version := question.body ->> 'replaces_version_id';
        IF old_version IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM formal_item_versions v WHERE v.id::text = old_version AND v.body ->> 'kind' = 'hypothesis'
        ) THEN
            RAISE EXCEPTION 'revising a hypothesis can replace only a hypothesis' USING ERRCODE = '23514';
        END IF;
        new_basis := CASE WHEN old_version IS NULL THEN target.body -> 'basis_version_ids'
            ELSE (target.body -> 'basis_version_ids') - old_version END;
        new_goal := (create_research_goal(target.body || jsonb_build_object(
            'basis_version_ids', new_basis || to_jsonb(version_id)), target.id)).id;
    END IF;
    INSERT INTO research_condition_decisions (request_id, action, actor_ref, reason, version_id,
                                              evidence_id, revised_goal_id)
    VALUES (question.id, action, actor_ref, reason, version_id, evidence_id, new_goal) RETURNING * INTO saved;
    IF question.clarification_id IS NOT NULL AND action <> 'revise' THEN
        PERFORM resolve_clarification(question.clarification_id, actor_ref, reason);
    END IF;
    RETURN saved;
END $$;

CREATE OR REPLACE VIEW rm.research_condition_status WITH (security_barrier = true) AS
    SELECT q.*, decision.action, decision.actor_ref, decision.reason, decision.version_id,
           decision.evidence_id, decision.revised_goal_id, decision.decided_at,
           CASE WHEN decision.evidence_id IS NOT NULL THEN
               recheck.request_id IS NULL AND rm.condition_derivation_current(q.id, decision.evidence_id)
           END AS derivation_current,
           recheck.replacement_request_id, recheck.reason AS derivation_recheck_reason
    FROM rm.research_condition_requests q
    LEFT JOIN rm.research_condition_decisions decision ON decision.request_id = q.id
    LEFT JOIN rm.research_condition_rechecks recheck ON recheck.request_id = q.id;

REVOKE ALL ON FUNCTION rm.proof_dependencies_authorized(text, uuid, jsonb),
    rm.lean_check_basis_closed(uuid, jsonb), rm.condition_derivation_current(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.condition_derivation_current(uuid, uuid) TO rm_human;
