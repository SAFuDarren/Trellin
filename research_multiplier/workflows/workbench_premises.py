"""Review a proposed change to a selected Lean research scope."""

import re
from uuid import UUID

from research_multiplier.protocol.serialization import fingerprint
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows import proof_graph


class PremiseError(ValueError):
    """A fixed workbench error key."""


_ORIGIN = "workbench_premise"
_INACTIVE = {"archived", "superseded"}
_SCOPE = """
SELECT g.id, g.frame_sha256, g.basis_current, g.claim_status, g.body, c.body AS target
FROM rm.research_goal_status g JOIN rm.research_conjectures c ON c.id = g.conjecture_id
WHERE g.claim_id = %s AND g.approved_at IS NOT NULL AND g.body ->> 'domain' = 'lean-core'
  AND jsonb_typeof(c.body #> '{formal,lean,proposition}') = 'string'
"""
_PROPOSALS = """
SELECT q.id, q.request_sha256, q.body, q.action, q.reason, next.claim_id AS revised_claim_id
FROM rm.research_condition_status q
LEFT JOIN rm.research_goal_status next ON next.id = q.revised_goal_id
WHERE q.goal_id = %s AND q.body ->> 'origin' = 'workbench_premise'
  AND q.body ->> 'kind' = 'interpretation'
  AND jsonb_typeof(q.body -> 'researcher_question') = 'string'
  AND jsonb_typeof(q.body #> '{formal,lean,proposition}') = 'string'
"""


def _scope(store, claim_id):
    return next(iter(store.query("human", _SCOPE, claim_id)), None)


def _basis(store, scope):
    return store.query("human", """
        SELECT version_id, body FROM rm.research_goal_basis WHERE goal_id = %s ORDER BY position
    """, scope["id"])


def _present(row):
    body = row["body"]
    state = {None: "pending", "reject": "rejected", "revise": "revised"}.get(row["action"], "resolved")
    if state == "revised" and not row["revised_claim_id"]:
        state = "revision_pending"
    return {
        "id": str(row["id"]), "sha256": row["request_sha256"],
        "wording": body["statement"], "lean": body["formal"]["lean"]["proposition"],
        "question": body["researcher_question"], "impact": body["impact"],
        "state": state,
        **({"reason": row["reason"]} if row["reason"] else {}),
        **({"revised_claim_id": str(row["revised_claim_id"])} if row["revised_claim_id"] else {}),
    }


def _meanings(body, natural):
    formal = body.get("formal")
    lean = formal.get("lean") if isinstance(formal, dict) else None
    proposition = lean.get("proposition") if isinstance(lean, dict) else None
    return {"wording": body[natural], **({"lean": proposition} if isinstance(proposition, str) else {})}


def graph(store, claim_id):
    """Add private review nodes only to the authenticated Workbench graph."""
    graph = proof_graph.snapshot(store, claim_id, include_lemma_plan=True)
    if not store.local_identity_installed() or not (scope := _scope(store, claim_id)):
        return graph
    proposals = [_present(row) for row in store.query("human", _PROPOSALS + """
        ORDER BY (q.action IS NULL) DESC, q.requested_at DESC, q.id
    """, scope["id"])]
    graph["claim"]["premise_review"] = {
        "frame_sha256": scope["frame_sha256"],
        "target": _meanings(scope["target"], "statement"),
        "basis": [
            {"id": str(item["version_id"]), "kind": item["body"]["kind"], **_meanings(item["body"], "natural")}
            for item in _basis(store, scope)
        ],
        "proposals": proposals,
        "available": scope["basis_current"] and scope["claim_status"] not in _INACTIVE,
    }
    graph["nodes"].extend(
        {"id": "proposed-premise:" + proposal["id"], "kind": "proposed_premise", "status": "pending",
         "title": proposal["wording"], "statement": proposal["wording"], "formalization": proposal["lean"],
         "reference": {"claim_id": str(claim_id), "condition_id": proposal["id"]}}
        for proposal in proposals if proposal["state"] == "pending"
    )
    return graph


def _text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum or "\0" in value):
        raise PremiseError("premise.invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise PremiseError("premise.invalid") from None
    return value.strip()


def _uuid(value):
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise PremiseError("premise.invalid") from None


def _review(store, document, fields):
    if not isinstance(document, dict) or document.keys() != fields | {"claim_id", "frame_sha256"}:
        raise PremiseError("premise.invalid")
    claim_id = _uuid(document["claim_id"])
    digest = document["frame_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PremiseError("premise.invalid")
    if not store.local_identity_installed():
        raise PremiseError("premise.unavailable")
    try:
        actor = store.local_user()
    except DatabaseOperationError:
        raise
    except ValueError:
        raise PremiseError("premise.identity") from None
    if actor is None:
        raise PremiseError("premise.identity")
    # Workbench mutations for the same claim must observe earlier submissions.
    store.query("human", "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", f"workbench-premise:{claim_id}")
    scope = _scope(store, claim_id)
    if not scope or scope["frame_sha256"] != digest:
        raise PremiseError("premise.stale")
    return scope, actor["display_name"]


def _duplicate(store, scope, wording, lean):
    target = _meanings(scope["target"], "statement")
    if wording == target["wording"].strip() or lean == target["lean"].strip():
        return True
    return any(
        entry["body"].get("kind") == "hypothesis"
        and _meanings(entry["body"], "natural").get("lean", "").strip() == lean
        for entry in _basis(store, scope)
    )


def propose(store, document):
    """Record a question about the claim's meaning and leave the basis untouched."""
    scope, _ = _review(store, document, {"wording", "lean", "question", "impact"})
    if not scope["basis_current"] or scope["claim_status"] in _INACTIVE:
        raise PremiseError("premise.stale")
    wording = _text(document["wording"], 4000)
    lean = _text(document["lean"], 16000)
    question = _text(document["question"], 900)
    impact = _text(document["impact"], 2000)
    if _duplicate(store, scope, wording, lean):
        raise PremiseError("premise.duplicate")
    body = {"kind": "interpretation", "origin": _ORIGIN, "statement": wording,
            "formal": {"lean": {"proposition": lean}}, "researcher_question": question, "impact": impact}
    # Native clarification identity includes question wording. Bind it to the
    # full proposal so distinct premises with the same question have separate holds.
    body["question"] = f"{question} [{fingerprint(body)}]"
    saved = store.query("human", "SELECT * FROM rm.request_research_condition(%s,%s)",
                        scope["id"], store.json(body))[0]
    return {"id": str(saved["id"])}


def _revise(store, scope, item, actor, reason):
    body = item["body"]
    # Review the same statement that was displayed. Register a distinct item
    # for this exact condition and leave all existing item versions intact.
    hypothesis = {"kind": "hypothesis", "natural": body["statement"], "formal": body["formal"],
                  "source": {"kind": "human_entry", "premise_request_id": str(item["id"])}}
    saved = store.query("human", "SELECT * FROM rm.propose_formal_item(%s,%s)",
                        f"workbench-premise:{item['id']}", store.json(hypothesis))[0]
    store.query("human", "SELECT rm.approve_formal_item(%s,%s,%s,%s) AS id",
                saved["id"], saved["version_sha256"], actor, reason)
    decision = store.query("human", "SELECT * FROM rm.decide_research_condition(%s,%s,%s,%s,%s,%s)",
                           item["id"], "revise", actor, reason, saved["id"], None)[0]
    # The condition decision takes the original goal lock. Check competing
    # interpretation holds while that lock is held and roll back on conflict.
    if store.query("human", """
        SELECT 1 FROM rm.research_condition_status
        WHERE goal_id = %s AND id <> %s AND body ->> 'kind' = 'interpretation'
          AND (action IS NULL OR action = 'revise' OR derivation_current IS FALSE)
        LIMIT 1
    """, scope["id"], item["id"]):
        raise PremiseError("premise.conflicts")
    revised = store.query("human", "SELECT id, frame_sha256 FROM rm.research_goal_status WHERE id = %s",
                          decision["revised_goal_id"])[0]
    store.query("human", "SELECT rm.confirm_research_goal(%s,%s,%s,%s) AS id",
                revised["id"], revised["frame_sha256"], actor, reason)
    candidate = store.query("human", "SELECT * FROM rm.propose_research_conjecture(%s,%s)",
                            revised["id"], store.json(scope["target"]))[0]
    selected = store.query("human", "SELECT * FROM rm.select_research_conjecture(%s,%s,%s,%s)",
                           candidate["id"], candidate["conjecture_sha256"], actor, reason)[0]
    return {"id": str(item["id"]), "revised_claim_id": str(selected["claim_id"])}


def decide(store, document):
    """Reject a proposal or explicitly freeze a separate, revised claim."""
    scope, actor = _review(store, document, {"id", "sha256", "decision", "reason"})
    identifier = _uuid(document["id"])
    action = document["decision"]
    reason = _text(document["reason"], 2000)
    if action not in ("reject", "revise"):
        raise PremiseError("premise.invalid")
    rows = store.query("human", _PROPOSALS + " AND q.id = %s", scope["id"], identifier)
    if not rows or rows[0]["request_sha256"] != document["sha256"]:
        raise PremiseError("premise.stale")
    item = rows[0]
    if item["action"] is not None:
        if (item["action"] == action and item["reason"] == reason
                and (action == "reject" or item["revised_claim_id"])):
            result = _present(item)
            return {key: result[key] for key in ("id", "revised_claim_id") if key in result}
        raise PremiseError("premise.stale")
    if scope["claim_status"] in _INACTIVE or (action == "revise" and not scope["basis_current"]):
        raise PremiseError("premise.stale")
    if action == "revise":
        return _revise(store, scope, item, actor, reason)
    store.query("human", "SELECT * FROM rm.decide_research_condition(%s,%s,%s,%s,%s,%s)",
                identifier, "reject", actor, reason, None, None)
    return {"id": str(identifier)}
