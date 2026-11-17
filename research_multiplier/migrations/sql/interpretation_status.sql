-- Keep the recorded verdict distinct from a hold on using its interpretation.
-- Match the active packet's clarification gates without reading back through
-- research_goal_status, which itself presents the effective claim status.
CREATE OR REPLACE VIEW rm.claim_foundation_status WITH (security_barrier = true) AS
    SELECT c.id, c.domain, c.title, c.contract_sha256, c.decisive_evidence_id,
        CASE WHEN review.interpretation_hold THEN 'awaiting_interpretation_review'
             WHEN decisive.needs_recheck THEN 'needs_recheck' ELSE c.status::text END AS status,
        c.status::text AS recorded_status, review.interpretation_hold
    FROM rm.claims c
    LEFT JOIN rm.evidence_foundation_status decisive ON decisive.id = c.decisive_evidence_id
    CROSS JOIN LATERAL (
        SELECT c.status IN ('open', 'proved', 'refuted') AND EXISTS (
            SELECT FROM rm.research_goal_selections selected
            JOIN rm.formalization_packets packet ON packet.id = selected.packet_id
            JOIN rm.formalization_reviews series ON series.id = packet.review_id
                AND series.active_version = packet.version
            JOIN rm.research_condition_effective question ON question.goal_id = selected.goal_id
            WHERE selected.claim_id = c.id AND question.body ->> 'kind' = 'interpretation'
              AND (question.stale_derivation
                OR (question.clarification_id IS NULL AND (question.action IS NULL OR question.action = 'revise'))
                OR (question.clarification_id IS NOT NULL AND NOT EXISTS (
                    SELECT FROM rm.clarification_resolutions resolved
                    WHERE resolved.request_id = question.clarification_id
                )))
        ) AS interpretation_hold
    ) review;

CREATE OR REPLACE VIEW rm.research_goal_status WITH (security_barrier = true) AS
    SELECT g.*, a.approved_at, a.actor_ref AS approved_by, s.conjecture_id, s.packet_id, s.claim_id,
           claim.status AS claim_status, packet.proof_ready,
           NOT EXISTS (SELECT 1 FROM rm.research_goal_basis b
                       WHERE b.goal_id = g.id AND b.needs_recheck) AS basis_current,
           coalesce(claim.interpretation_hold, EXISTS (
               SELECT FROM rm.research_condition_effective q WHERE q.goal_id = g.id
                   AND q.body ->> 'kind' = 'interpretation'
                   AND (q.action IS NULL OR q.action = 'revise' OR q.stale_derivation)
           )) AS interpretation_hold, claim.recorded_status AS recorded_claim_status
    FROM rm.research_goals g
    LEFT JOIN rm.research_goal_approvals a ON a.goal_id = g.id
    LEFT JOIN rm.research_goal_selections s ON s.goal_id = g.id
    LEFT JOIN rm.formalization_status packet ON packet.id = s.packet_id
    LEFT JOIN rm.claim_foundation_status claim ON claim.id = s.claim_id;
