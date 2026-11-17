"""Show the current research decisions and accepted meaning from one database snapshot."""

import json

from research_multiplier.protocol.serialization import terminal_line


DEFAULT_LIMIT = 5
MAX_LIMIT = 20

_PROGRESS_NEXT = {
    "awaiting_approval": ("review_progress_policy", "Review the proposed progress objective, metric and budget."),
    "awaiting_evidence": ("continue_proof_work", "Continue investigating under the reviewed scope and submit exact evidence."),
    "ready": ("run_progress_worker", "The approved progress worker can take the next step."),
    "needs_plan": ("run_progress_worker", "The approved progress worker can schedule the next step."),
    "waiting_verifier": ("await_verification", "The verifier can record the reviewed result."),
    "route_stopped": ("inspect_progress_route", "Inspect why this route stopped before deciding whether to continue."),
    "budget_exhausted": ("review_progress_budget", "Review what was learned before committing more work."),
}


_REDUCTION_NEXT = {
    "ready": ("close_reduction", "All obligations are verified. Check the frozen goal using this reduction."),
    "awaiting_obligations": ("prove_reduction_obligations", "Prove the remaining obligations from the same frozen basis."),
}


_SNAPSHOT = """
WITH parameters AS (SELECT %s::uuid AS claim_id, %s::integer AS page),
subjects AS MATERIALIZED (
    SELECT claim.id, claim.title, claim.domain, claim.statement, claim.sensitivity,
           claim.contract_snapshot, claim.contract_sha256, claim.decisive_evidence_id, claim.frozen_at
    FROM rm.claims claim
    WHERE ((SELECT claim_id FROM parameters) IS NULL AND claim.kind = 'researcher_claim'
           AND claim.status NOT IN ('archived', 'superseded')) OR claim.id = (SELECT claim_id FROM parameters)
    ORDER BY claim.created_at DESC, claim.id
    LIMIT (SELECT page FROM parameters)
),
research AS (
    SELECT selected.id, selected.title, selected.domain, selected.statement, selected.sensitivity,
           selected.contract_snapshot, selected.contract_sha256, selected.frozen_at,
           state.status, state.recorded_status, state.interpretation_hold,
           to_jsonb(packet) AS packet, to_jsonb(lean) AS lean, to_jsonb(decisive) AS decisive,
           to_jsonb(progress_policy) AS progress, to_jsonb(lean_advance) AS lean_advance,
           to_jsonb(budget) - ARRAY['statement_id','claim_id','contract_sha256'] AS search_budget,
           coalesce(basis.items, '[]'::jsonb) AS basis, coalesce(questions.items, '[]'::jsonb) AS questions,
           coalesce(activity.items, '[]'::jsonb) AS activity,
           coalesce(reductions.items, '[]'::jsonb) AS reductions,
           coalesce(recent_receipts.items, '[]'::jsonb) AS receipts,
           coalesce(recent_receipts.conflicts, false) AS evidence_conflict
    FROM subjects selected
    JOIN rm.claim_foundation_status state ON state.id = selected.id
    LEFT JOIN LATERAL (
        SELECT p.id, p.version, p.packet_sha256, p.approved,
               rm.research_step_scope_ready(p.id, selected.id, p.packet_sha256, selected.contract_sha256) AS proof_ready
        FROM rm.formalization_status p WHERE p.claim_id = selected.id AND NOT p.superseded
        ORDER BY p.approved DESC, p.proposed_at DESC, p.id LIMIT 1
    ) packet ON true
    LEFT JOIN LATERAL (
        SELECT s.id, s.version, s.approved, s.check_ready, s.statement_sha256,
               s.body ->> 'theorem_type' AS proposition
        FROM rm.lean_statement_status s WHERE s.packet_id = packet.id AND s."current"
        ORDER BY s.proposed_at DESC, s.id LIMIT 1
    ) lean ON true
    LEFT JOIN rm.lean_search_budget_status budget ON budget.statement_id = lean.id
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(to_jsonb(r)) AS items FROM (
            SELECT * FROM rm.lean_reduction_status(lean.id)
            ORDER BY CASE status WHEN 'ready' THEN 0 WHEN 'awaiting_obligations' THEN 1
                                 WHEN 'goal_checked' THEN 2 ELSE 3 END, recorded_at, implication_id
            LIMIT (SELECT page FROM parameters)
        ) r
    ) reductions ON true
    LEFT JOIN LATERAL (
        SELECT e.id, e.kind, e.issued_at, e.review_status, e.needs_recheck, e.verdict, e.diff_status,
               e.result ->> 'refutation_mode' AS refutation_mode
        FROM rm.evidence_foundation_status e
        WHERE e.id = selected.decisive_evidence_id AND e.contract_sha256 = selected.contract_sha256
    ) decisive ON true
    LEFT JOIN LATERAL (
        SELECT p.id, p.state, p.latest_round, p.progress, p.policy ->> 'metric' AS metric, p.stop_reason,
               p.policy ? 'lean' AS lean_policy,
               p.policy #>> '{lean,statement_id}' AS lean_statement_id
        FROM rm.research_progress_status p WHERE p.packet_id = packet.id
        ORDER BY p.approved_at DESC NULLS LAST, p.proposed_at DESC, p.id LIMIT 1
    ) progress_policy ON true
    LEFT JOIN rm.lean_progress_last_run lean_advance ON lean_advance.policy_id = progress_policy.id
        AND progress_policy.lean_statement_id = lean.id::text
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(to_jsonb(b)) AS items FROM (
            SELECT item_version_id, packet_label, logical_key, version, version_sha256,
                   body ->> 'kind' AS kind, body ->> 'natural' AS statement, body -> 'formal' AS formal
            FROM rm.get_packet_basis(packet.id)
            ORDER BY packet_label NULLS LAST, logical_key, version, item_version_id LIMIT (SELECT page FROM parameters)
        ) b
    ) basis ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(to_jsonb(q)) AS items FROM (
            SELECT kind, question, item_id, detail, waiting_since
            FROM rm.research_actionable_review_inbox
            WHERE claim_id = selected.id
            ORDER BY (kind = 'decide_condition') DESC, waiting_since, kind, item_id LIMIT (SELECT page FROM parameters)
        ) q
    ) questions ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(to_jsonb(t)) AS items FROM (
            SELECT t.kind, t.status, t.finding, t.failure_code, t.id, t.attempt_id, t.enqueued_at,
                   job.payload ->> 'target' AS target
            FROM rm.research_task_status t JOIN rm.jobs job ON job.id = t.id WHERE t.claim_id = selected.id
            ORDER BY t.enqueued_at DESC, t.id DESC LIMIT (SELECT page FROM parameters)
        ) t
    ) activity ON true
    LEFT JOIN LATERAL (
        SELECT jsonb_agg(to_jsonb(e) - 'conflicts') AS items, bool_or(e.conflicts) AS conflicts FROM (
            SELECT id, kind, issued_at, review_status, result ->> 'refutation_mode' AS refutation_mode,
                   bool_or(kind = 'counterexample') OVER () AND bool_or(kind <> 'counterexample') OVER () AS conflicts
            FROM rm.evidence_foundation_status WHERE claim_id = selected.id
              AND verdict = 'verified' AND diff_status = 'exact' AND NOT needs_recheck
              AND issuance_sha256 IS NOT NULL AND review_status IN ('pending', 'approved')
              AND kind IN ('lean', 'exact_certificate', 'audited_proof', 'counterexample')
            ORDER BY (review_status = 'approved') DESC, issued_at DESC, id LIMIT (SELECT page FROM parameters)
        ) e
    ) recent_receipts ON true
),
project_decisions AS MATERIALIZED (
    SELECT inbox.kind, inbox.question, inbox.item_id, inbox.claim_id, inbox.waiting_since,
           jsonb_strip_nulls(jsonb_build_object('title', claim.title)) || coalesce(inbox.detail, '{}'::jsonb) AS detail
    FROM rm.research_actionable_review_inbox inbox LEFT JOIN rm.claims claim ON claim.id = inbox.claim_id
    WHERE (SELECT claim_id FROM parameters) IS NULL
    UNION ALL
    SELECT 'checker_hold', 'Review the verification change before releasing or retiring this revision.',
           id, NULL::uuid, recorded_at, jsonb_build_object('checker_revision', checker_revision)
    FROM rm.checker_hold_status WHERE released_at IS NULL AND (SELECT claim_id FROM parameters) IS NULL
),
answered_targets AS (
    SELECT id, title, sensitivity FROM rm.target_clarification_status
    WHERE status = 'answered' AND (SELECT claim_id FROM parameters) IS NULL
    ORDER BY decided_at, id LIMIT (SELECT page FROM parameters)
),
waiting_targets AS (
    SELECT id, body ->> 'title' AS title, body ->> 'statement' AS statement,
           body -> 'formal' AS formal, target_sha256, frozen_at
    FROM rm.research_target_status WHERE claim_id IS NULL AND (SELECT claim_id FROM parameters) IS NULL
    ORDER BY frozen_at DESC, id LIMIT (SELECT page FROM parameters)
),
waiting_frames AS (
    SELECT goal.id, goal.body ->> 'title' AS title, goal.body ->> 'objective' AS objective,
           goal.frame_sha256, goal.approved_at, goal.basis_current, goal.interpretation_hold,
           discussion.record_sha256 AS discussion_sha256, discussion.candidate_count AS discussion_candidates,
           EXISTS (SELECT 1 FROM rm.research_conjectures candidate WHERE candidate.goal_id = goal.id) AS has_conjectures
    FROM rm.research_goal_status goal LEFT JOIN rm.research_open_discussions discussion ON discussion.goal_id = goal.id
    WHERE goal.claim_id IS NULL AND goal.conjecture_id IS NULL AND goal.approved_at IS NOT NULL
      AND (SELECT claim_id FROM parameters) IS NULL
    ORDER BY goal.approved_at DESC, goal.id LIMIT (SELECT page FROM parameters)
)
SELECT coalesce((SELECT jsonb_agg(to_jsonb(row)) FROM research row), '[]'::jsonb) AS claims,
       coalesce((SELECT jsonb_agg(to_jsonb(row)) FROM (
           SELECT * FROM project_decisions WHERE claim_id IS NULL
           ORDER BY (kind = 'checker_hold') DESC, waiting_since, kind, item_id LIMIT (SELECT page FROM parameters)
       ) row), '[]'::jsonb) AS unassigned,
       coalesce((SELECT jsonb_agg(to_jsonb(row)) FROM (
           SELECT * FROM project_decisions
           ORDER BY (kind = 'checker_hold') DESC, (kind = 'decide_condition') DESC, waiting_since, kind, item_id
           LIMIT (SELECT page FROM parameters)
       ) row), '[]'::jsonb) AS project_decisions,
       coalesce((SELECT jsonb_agg(to_jsonb(row)) FROM answered_targets row), '[]'::jsonb) AS answered_targets,
       coalesce((SELECT jsonb_agg(to_jsonb(row)) FROM waiting_targets row), '[]'::jsonb) AS targets,
       coalesce((SELECT jsonb_agg(to_jsonb(row)) FROM waiting_frames row), '[]'::jsonb) AS frames
"""


def _only(row, keys):
    return {key: row[key] for key in keys if row.get(key) is not None}


def _bounded(rows, limit):
    return {"items": rows[:limit], "has_more": len(rows) > limit}


def _decision(row, details):
    projected = _only(row, ("kind", "question"))
    projected.update(_only(row["detail"], ("title", "statement", "impact", "identifier", "outstanding", "candidates")))
    projected["reference"] = {"item_id": row["item_id"]}
    if row.get("claim_id") is not None:
        projected["reference"]["claim_id"] = row["claim_id"]
    if row["kind"] == "clarify_formalization":
        projected["questions"] = row["detail"]["questions"]
    if row["kind"] == "review_lean_scope_change":
        projected["reference"]["statement_id"] = row["detail"]["statement_id"]
        projected["changes"] = _bounded(row["detail"]["changes"], DEFAULT_LIMIT)
    if row["kind"] == "review_formalpedia_basis":
        projected["reference"]["statement_id"] = row["detail"]["statement_id"]
        projected["candidate"] = row["detail"]["candidate"]
        projected["obligations"] = _bounded(row["detail"]["obligations"], DEFAULT_LIMIT)
    if details:
        projected["details"] = {"waiting_since": row["waiting_since"], **row["detail"]}
    return projected


def _receipt(row, status):
    result = {"status": status, "evidence_grade": row["kind"], "verified_at": row["issued_at"]}
    result.update(_only(row, ("refutation_mode", "review_status")))
    result["reference"] = {"evidence_id": row["id"]}
    return result


def _reduction(row, details):
    result = {
        "status": row["status"], "review_status": row["review_status"],
        "obligations": [{"proposition": item["proposition"], "verified": item["lemma_id"] is not None}
                        for item in row["obligations"]],
        "reference": _only(row, ("implication_id", "evidence_id")),
    }
    if details:
        result["details"] = {
            **_only(row, ("recorded_at", "stale_evidence_id")),
            "obligation_lemma_ids": [item["lemma_id"] for item in row["obligations"]],
        }
    return result


def _next(row, decisions, approved):
    if row["status"] in ("archived", "superseded"):
        return {"action": "closed", "message": "This research question is closed."}
    if row["interpretation_hold"]:
        return {"action": "review_interpretation", "message": "Resolve the open question about the intended meaning."}
    if row["status"] == "needs_recheck":
        return {"action": "reopen_for_recheck", "message": "Review the withdrawn basis or proof before reopening."}
    if row["evidence_conflict"] or (row["progress"] and row["progress"]["state"] == "conflicting_receipts"):
        return {"action": "review_evidence_conflict", "message": "Resolve the conflicting evidence before adoption."}
    if decisions:
        first = decisions[0]
        return {"action": first["kind"], "message": first["question"], "reference": first["reference"]}
    if row["status"] in ("proved", "refuted"):
        return {"action": "inspect_conclusion", "message": "Read the adopted exact conclusion and its evidence."}
    if approved:
        return {"action": "review_promotion", "message": "Decide whether to adopt the approved evidence.",
                "reference": {"evidence_id": approved[0]["id"]}}
    if not row["packet"] or not row["packet"]["approved"]:
        return {"action": "prepare_scope", "message": "Review the exact starting statement and basis."}
    if not row["packet"]["proof_ready"]:
        return {"action": "check_scope", "message": "Inspect the starting scope before more proof work."}
    progress = row["progress"]
    advance = row["lean_advance"]
    if progress and progress["state"] == "awaiting_evidence" and advance:
        last = advance["result"]
        if last["status"] == "search_continues":
            return {"action": "run_progress_worker", "state": progress["state"],
                    "message": "The signed inquiry can continue from its last checked step.",
                    "reference": {"policy_id": progress["id"]}}
        if last["status"] == "verification_queue_blocked":
            return {"action": "inspect_verification_queue", "state": progress["state"],
                    "message": last.get("reason") or "Inspect the stopped Lean verification task.",
                    "reference": {"policy_id": progress["id"]}}
        if last["status"] == "budget_exhausted" and row["lean"]:
            return {"action": "inspect_lean_budget", "state": progress["state"],
                    "message": "The Lean search budget has been reached.",
                    "reference": {"statement_id": row["lean"]["id"]}}
        if last.get("reason") == "route_stall_limit" and row["lean"]:
            return {"action": "inspect_lean_obligations", "state": progress["state"],
                    "message": "The open proof routes reached their no-progress limit. Inspect the verified work and remaining obligations.",
                    "reference": {"statement_id": row["lean"]["id"]}}
        if (last["status"] == "proof_search_blocked" and last.get("route_attempts")
                and last.get("open_routes") == [] and row["lean"]):
            return {"action": "inspect_lean_obligations", "state": progress["state"],
                    "message": "The checked routes are blocked. Inspect the recorded search before choosing further work.",
                    "reference": {"statement_id": row["lean"]["id"]}}
    if row["lean"] and row["lean"]["check_ready"] and row["reductions"]:
        route = row["reductions"][0]
        if route["status"] in _REDUCTION_NEXT:
            action, message = _REDUCTION_NEXT[route["status"]]
            return {"action": action, "message": message,
                    "reference": {"statement_id": row["lean"]["id"], "implication_id": route["implication_id"]}}
    if row["progress"]:
        policy = row["progress"]
        state = policy["state"]
        if state == "awaiting_evidence" and policy.get("lean_policy") and row["lean"]:
            if policy["lean_statement_id"] != row["lean"]["id"]:
                return {"action": "check_lean_scope", "state": state,
                        "message": "Inspect the Lean statement before starting a policy for this version.",
                        "reference": {"statement_id": row["lean"]["id"]}}
            if not row["lean_advance"]:
                return {"action": "run_progress_worker", "state": state,
                        "message": "The approved Lean worker can try the current basis.",
                        "reference": {"policy_id": policy["id"]}}
            result = row["lean_advance"]["result"]
            if result["status"] == "proof_search_blocked":
                return {"action": "inspect_lean_obligations", "state": state,
                        "message": "The current route is unresolved. Check the remaining proof obligations.",
                        "reference": {"statement_id": row["lean"]["id"]}}
        action, message = _PROGRESS_NEXT.get(state, ("inspect_progress", "Inspect the recorded research progress."))
        return {"action": action, "state": state, "message": message, "reference": {"policy_id": policy["id"]}}
    if row["domain"] == "lean-core" and not row["lean"]:
        return {"action": "prepare_lean_statement", "message": "Prepare the exact Lean statement from the reviewed scope."}
    if row["lean"] and not row["lean"]["check_ready"]:
        return {"action": "check_lean_scope", "message": "Inspect the Lean statement and its exact environment."}
    return {"action": "continue_proof_work", "message": "Continue proof work under the reviewed scope."}


def _lean_activity(row, limit, details):
    result = row["result"]
    activity = {"status": result["status"], "recorded_at": row["recorded_at"]}
    activity.update(_only(result, ("evidence_id", "reduction_id", "theorem_version_id",
                                   "method", "target", "witness", "next_round")))
    if result.get("rounds"):
        latest = result["rounds"][-min(limit, 4):]
        activity["recent_rounds"] = [
            _only(step, ("round", "action", "status", "reason", "focus", "method", "lemma_id", "reduction_id",
                         "usage", "route_checkpoint"))
            for step in latest
        ]
    for key in ("native_obligations", "program_obligations"):
        if isinstance(result.get(key), list):
            activity[key] = _bounded(result[key], limit)
    open_obligations = list(result.get("obligations") or [])
    for route in result.get("open_routes", []):
        open_obligations.extend(item for item in route["obligations"] if not item.get("lemma_id"))
    for attempt in result.get("native_attempts", []):
        open_obligations.extend(attempt.get("obligations") or [])
    if open_obligations:
        unique = {item["proposition"]: item for item in open_obligations
                  if isinstance(item, dict) and item.get("proposition")}
        activity["obligations"] = _bounded(list(unique.values()), limit)
    if isinstance(result.get("route_comparison"), list):
        activity["route_comparison"] = _bounded(result["route_comparison"], limit)
    if isinstance(result.get("route_lanes"), list):
        activity["route_lanes"] = _bounded(result["route_lanes"], limit)
    if isinstance(result.get("route_attempts"), list):
        fields = ("status", "proposition", "reason")
        if details:
            fields += ("implication_id", "obligation_lemma_ids", "lemma_id", "attempt_id", "witness")
        activity["route_attempts"] = _bounded([_only(item, fields) for item in reversed(result["route_attempts"])], limit)
    if details:
        activity["details"] = _only(result, ("record", "reason", "changes", "worker_elapsed_ms", "parallel_batch"))
        if isinstance(result.get("native_attempts"), list):
            activity["details"]["native_attempts"] = _bounded(result["native_attempts"], limit)
    return activity


def _claim(row, limit, details):
    contract = row["contract_snapshot"]
    packet = row["packet"]
    questions = [_decision(question, details) for question in row["questions"]]
    basis = []
    for item in row["basis"]:
        presented = _only(item, ("kind", "packet_label", "statement", "formal"))
        if details:
            presented["reference"] = _only(item, ("item_version_id", "logical_key", "version", "version_sha256"))
        basis.append(presented)
    approved = [receipt for receipt in row["receipts"] if receipt["review_status"] == "approved"]
    decisive = row["decisive"]
    accepted = (row["status"] in ("proved", "refuted") and decisive and not decisive["needs_recheck"]
                and decisive["verdict"] == "verified" and decisive["diff_status"] == "exact"
                and decisive["review_status"] == "approved")
    formal = contract["formal_skeleton"] if contract else {}
    lean = row["lean"]
    output = {
        "title": row["title"], "question": row["statement"], "status": row["status"],
        "scope": {
            "frozen": contract is not None, "basis_reviewed": bool(packet and packet["approved"]),
            "proof_ready": bool(packet and packet["proof_ready"] and not row["interpretation_hold"]),
            "hypotheses": contract["hypotheses"] if contract else [],
            "conclusion": contract["conclusion"] if contract else None,
            "formal_target": formal.get("target"),
            "lean_proposition": lean["proposition"] if lean and lean["approved"] else None,
            "basis": _bounded(basis, limit),
        },
        "conclusion": _receipt(decisive, row["status"]) if accepted else None,
        "decisions": _bounded(questions, limit),
        "progress": _only(row["progress"], ("metric", "progress", "state", "latest_round", "stop_reason"))
        if row["progress"] else None,
        **({"lean_advance": _lean_activity(row["lean_advance"], limit, details)} if row["lean_advance"] else {}),
        **({"search_budget": row["search_budget"]} if row["search_budget"] else {}),
        "reductions": _bounded([_reduction(route, details) for route in row["reductions"]], limit),
        "recent_activity": _bounded([
            _only(task, ("kind", "target", "status", "finding", "enqueued_at")) for task in row["activity"]
        ], limit),
        "next": _next(row, questions, approved),
        "reference": {"claim_id": row["id"], "packet_id": packet["id"] if packet else None},
    }
    if details:
        output["details"] = {
            **_only(row, ("domain", "sensitivity", "contract_sha256", "frozen_at", "recorded_status", "interpretation_hold")),
            "packet": packet, "lean": row["lean"], "prior_decisive_evidence": decisive if not accepted else None,
            "current_evidence": _bounded([_receipt(receipt, "verified") for receipt in row["receipts"]], limit),
            "recent_task_references": _bounded([_only(task, ("id", "attempt_id", "failure_code")) for task in row["activity"]], limit),
            "progress_policy_id": row["progress"]["id"] if row["progress"] else None,
        }
    return output


def snapshot(store, claim_id=None, *, limit=DEFAULT_LIMIT, details=False):
    """Read a bounded, consistent projection while keeping the accepted scope explicit."""
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"Choose a resume limit between 1 and {MAX_LIMIT}.")
    row, = store.query("human", _SNAPSHOT, claim_id, limit + 1)
    if claim_id is not None:
        if not row["claims"]:
            raise ValueError("No research claim matches this ID.")
        return _claim(row["claims"][0], limit, details)

    targets = []
    for target in row["targets"]:
        presented = _only(target, ("title", "statement", "formal"))
        presented["next"] = {"action": "prepare_basis", "message": "Prepare and review the basis for this frozen target."}
        presented["reference"] = {"target_id": target["id"]}
        if details:
            presented["details"] = _only(target, ("target_sha256", "frozen_at"))
        targets.append(presented)
    frames = []
    for frame in row["frames"]:
        presented = _only(frame, ("title", "objective"))
        if not frame["basis_current"]:
            action, message = "review_frame_basis", "Review the withdrawn basis before exploring this frame."
        elif frame["interpretation_hold"]:
            action, message = "review_interpretation", "Resolve the open question about the intended scope."
        elif frame["has_conjectures"]:
            action, message = "compare_formalized_conjectures", "Compare the formalized candidates and select the intended target."
        elif frame["discussion_sha256"]:
            action, message = "review_discussed_conjectures", "Read the archived candidates and choose one to formalize."
        else:
            action, message = "discuss_conjectures", "Discuss conjectures using the reviewed basis."
        presented["next"] = {"action": action, "message": message}
        presented["reference"] = {"goal_id": frame["id"]}
        if frame["discussion_sha256"]:
            presented["discussion"] = {"sha256": frame["discussion_sha256"], "candidate_count": frame["discussion_candidates"]}
        if details:
            presented["details"] = _only(frame, ("frame_sha256", "approved_at"))
        frames.append(presented)
    return {
        "decisions": _bounded([_decision(question, details) for question in row["project_decisions"]], limit),
        "unassigned_decisions": _bounded([_decision(question, details) for question in row["unassigned"]], limit),
        "answered_formalizations": _bounded(row["answered_targets"], limit),
        "targets_waiting_for_basis": _bounded(targets, limit),
        "frames_waiting_for_conjecture": _bounded(frames, limit),
        "claims": {"items": [_claim(subject, limit, details) for subject in row["claims"][:limit]],
                   "has_more": len(row["claims"]) > limit},
    }


def render(snapshot):
    """Present decisions first and keep repository identifiers out of the narrative."""
    project = "claims" in snapshot
    claims = snapshot["claims"]["items"] if project else [snapshot]
    if project:
        decisions = [(decision.get("title"), decision) for decision in snapshot["decisions"]["items"]]
        more_decisions = snapshot["decisions"]["has_more"]
    else:
        decisions = [(snapshot["title"], decision) for decision in snapshot["decisions"]["items"]]
        more_decisions = snapshot["decisions"]["has_more"]
    lines = ["Decisions for you"]
    if not decisions:
        lines.append("  No pending decisions in this view.")
    for title, decision in decisions:
        subject = f"{terminal_line(title)} · " if title else ""
        lines.append(f"  - {subject}{terminal_line(decision['question'])}")
        if decision["kind"] in {"review_evidence", "review_promotion"}:
            lines.append(f"    research-multiplier review explain {decision['reference']['item_id']}")
        elif decision["kind"] == "review_lean_scope_change":
            lines.append(f"    research-multiplier lean show {decision['reference']['statement_id']}")
        elif decision["kind"] == "review_formalpedia_basis":
            lines.append(f"    Formalpedia · {terminal_line(decision['candidate']['theorem'])}")
            for obligation in decision["obligations"]["items"]:
                proposition = obligation.get("proposition")
                declaration = obligation.get("declaration")
                if declaration:
                    lines.append(f"    Unapproved declaration · {terminal_line(declaration)}")
                if proposition:
                    lines.append(f"    Required proposition · {terminal_line(proposition)}")
            if decision["obligations"]["has_more"]:
                lines.append("    Use --details to read the full list.")
            claim_id = decision["reference"]["claim_id"] if project else snapshot["reference"]["claim_id"]
            lines.append(f"    research-multiplier research resume {claim_id} --details")
        elif decision["kind"] == "clarify_formalization":
            for index, question in enumerate(decision["questions"], 1):
                lines.append(f"    {index}. {terminal_line(question)}")
            lines.append(f"    research-multiplier research clarify {decision['reference']['item_id']}")
    if more_decisions:
        lines.append("  More decisions are available in the review inbox.")

    lines.extend(("", "Research"))
    if project:
        for draft in snapshot["answered_formalizations"]["items"]:
            flag = "--confirm-local" if draft["sensitivity"] == "restricted" else "--confirm-public"
            lines.append(f"  - {terminal_line(draft['title'])} · Recorded answers ready for formalization.")
            lines.append(f"    research-multiplier research clarify {draft['id']} --continue {flag}")
        for target in snapshot["targets_waiting_for_basis"]["items"]:
            lines.append(f"  - {terminal_line(target['title'])} · Prepare reviewed starting basis.")
            lines.append(f"    research-multiplier intake target {target['reference']['target_id']}")
        for frame in snapshot["frames_waiting_for_conjecture"]["items"]:
            lines.append(f"  - {terminal_line(frame['title'])} · {frame['next']['message']}")
            verb = "discussion" if frame["next"]["action"] == "review_discussed_conjectures" else "show"
            lines.append(f"    research-multiplier goal {verb} {frame['reference']['goal_id']}")

    for claim in claims:
        lines.append(f"  - {terminal_line(claim['title'])} · {claim['status']}")
        lines.append(f"    Question: {terminal_line(claim['question'])}")
        action = claim["next"]
        lines.append(f"    {action['message']}")
        reference = action.get("reference", {})
        if action["action"] == "run_progress_worker" and reference.get("policy_id"):
            lines.append(f"    research-multiplier advance worker --policy {reference['policy_id']}")
        if action["action"] == "inspect_lean_budget":
            lines.append(f"    research-multiplier lean budget {reference['statement_id']}")
        if action["action"] == "check_lean_scope" and reference.get("statement_id"):
            lines.append(f"    research-multiplier lean show {reference['statement_id']}")
        advance = claim.get("lean_advance")
        if advance and advance.get("witness") is not None:
            lines.append(f"    Lean-verified counterexample: {terminal_line(json.dumps(advance['witness'], ensure_ascii=False))}")
        if advance:
            paused = [lane for lane in advance.get("route_lanes", {}).get("items", []) if lane.get("paused")]
            for lane in paused[:3]:
                lines.append(f"    Paused proof route · {lane['verified_obligations']} checked, "
                             f"{lane['pending_obligations']} open, {lane['stalled_checkpoints']} stalled checkpoints.")
            for attempt in advance.get("route_attempts", {}).get("items", [])[:2]:
                if attempt["status"] == "route_refuted":
                    lines.append(f"    Lean-verified counterexample to route obligation · {terminal_line(attempt['proposition'])}")
                elif attempt["status"] == "closure_rejected":
                    lines.append(f"    Route's final check stopped · {terminal_line(attempt['reason'])}")
            for step in advance.get("recent_rounds", []):
                focus = step.get("focus", {})
                method = focus.get("method") if isinstance(focus, dict) else None
                description = method or step.get("action") or "proposal"
                lines.append(f"    Round {step['round']} · {terminal_line(description)} · {terminal_line(step['status'])}")
                if step.get("reason"):
                    lines.append(f"      {terminal_line(step['reason'])}")
        if advance and advance["status"] in {"proof_search_blocked", "search_continues", "blocked"}:
            for obligation in advance.get("obligations", {}).get("items", []):
                proposition = obligation.get("proposition") if isinstance(obligation, dict) else obligation
                if proposition:
                    lines.append(f"    Open proof obligation: {terminal_line(proposition)}")
        if claim["conclusion"]:
            evidence = claim["conclusion"]["reference"]["evidence_id"]
            lines.append(f"    research-multiplier review packet {evidence}")
            if claim["conclusion"]["evidence_grade"] == "lean" and claim["status"] == "proved":
                lines.append(f"    research-multiplier research follow-up {claim['reference']['claim_id']}")
    if project and snapshot["claims"]["has_more"]:
        lines.append("  More claims are available with --limit or a claim ID.")
    return "\n".join(lines)
