CREATE FUNCTION rm.is_target_first_goal(goal_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT (research_target_for_goal($1)).id IS NOT NULL
$$;
REVOKE ALL ON FUNCTION rm.is_target_first_goal(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.is_target_first_goal(uuid) TO rm_human;

-- An already reviewed target is held by a researcher's decision. An unsuccessful
-- research worker can record an attempt; it cannot reopen the approved meaning.
CREATE FUNCTION rm.guard_target_reinterpretation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.body ->> 'kind' = 'interpretation'
        AND NOT pg_has_role(session_user, 'rm_human', 'USAGE')
        AND is_target_first_goal(NEW.goal_id)
    THEN
        RAISE EXCEPTION 'only a researcher can reopen the meaning of a reviewed target' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER guard_target_reinterpretation BEFORE INSERT ON rm.research_condition_requests
    FOR EACH ROW EXECUTE FUNCTION rm.guard_target_reinterpretation();

CREATE FUNCTION rm.guard_target_clarification() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT pg_has_role(session_user, 'rm_human', 'USAGE') AND EXISTS (
        SELECT FROM formalization_packets p
        JOIN research_goal_selections selected ON selected.claim_id = p.claim_id
        WHERE p.id = NEW.packet_id AND is_target_first_goal(selected.goal_id)
    ) THEN
        RAISE EXCEPTION 'the current target and basis require a research decision to reopen'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER guard_target_clarification BEFORE INSERT ON rm.clarification_requests
    FOR EACH ROW EXECUTE FUNCTION rm.guard_target_clarification();

REVOKE ALL ON FUNCTION rm.guard_target_clarification(), rm.guard_target_reinterpretation() FROM PUBLIC;

-- Require current exact proof/refutation evidence to close over the approved
-- foundational items. This is checked at issuance, where the verifier's
-- actual-use rows already exist.
CREATE FUNCTION rm.guard_reviewed_target_basis() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE proof_packet uuid;
DECLARE allowed jsonb;
BEGIN
    IF NEW.lean_check_id IS NULL OR NOT EXISTS (
        SELECT FROM research_goal_selections selected WHERE selected.claim_id = NEW.claim_id
          AND is_target_first_goal(selected.goal_id)
    ) THEN
        RETURN NEW;
    END IF;
    SELECT statement.packet_id INTO STRICT proof_packet
    FROM lean_checks checked JOIN lean_statements statement ON statement.id = checked.statement_id
    WHERE checked.id = NEW.lean_check_id AND statement.claim_id = NEW.claim_id;
    SELECT coalesce(jsonb_agg(permitted.version_id), '[]'::jsonb) INTO allowed
    FROM (
        SELECT version_id FROM packet_basis_items WHERE packet_id = proof_packet
        UNION
        SELECT version_id FROM packet_library_items WHERE packet_id = proof_packet
    ) permitted;
    IF NOT coalesce(lean_check_basis_closed(NEW.lean_check_id, allowed), false) THEN
        RAISE EXCEPTION 'the verified proof or refutation uses facts outside the reviewed basis'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER guard_reviewed_target_basis BEFORE INSERT ON rm.evidence
    FOR EACH ROW EXECUTE FUNCTION rm.guard_reviewed_target_basis();
REVOKE ALL ON FUNCTION rm.guard_reviewed_target_basis() FROM PUBLIC;

-- Route questions stay in the investigation history. An exact, current and
-- bound counterexample needs a human decision and preserves the original claim.
CREATE VIEW rm.research_actionable_review_inbox WITH (security_barrier = true) AS
    SELECT item.kind, item.item_id, item.claim_id, item.waiting_since,
        CASE WHEN item.kind = 'review_evidence' AND item.detail ->> 'kind' = 'counterexample'
            THEN 'This verified result refutes the frozen target. Review the original assumptions and the refutation.'
            ELSE item.question END AS question,
        item.detail
    FROM rm.research_review_inbox item
    WHERE NOT (
        item.kind = 'decide_condition' AND item.detail ->> 'kind' = 'route_condition'
        AND EXISTS (SELECT FROM rm.research_goal_selections selected WHERE selected.claim_id = item.claim_id
            AND rm.is_target_first_goal(selected.goal_id))
    );

REVOKE ALL ON rm.research_actionable_review_inbox FROM PUBLIC;
GRANT SELECT ON rm.research_actionable_review_inbox TO rm_human;
