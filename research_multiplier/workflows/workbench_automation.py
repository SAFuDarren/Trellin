"""Create, approve and stop automation rules from the workbench (#94).

An automation rule is a research progress policy. The workbench offers templates backed by the existing rule
engine, lists the claims each template can run on and says what is missing elsewhere, proposes the exact policy
for review, and records approval and stopping with the same database functions as `advance propose`,
`advance approve` and `advance stop`. Proposing never runs anything; only an approved rule is worked on.
"""

from uuid import UUID

from research_multiplier.workflows import research_progress
from research_multiplier.workflows.proof_graph import _CLAIM_ORDER, _CLAIMS_FROM, _CLAIMS_VISIBLE

# Deterministic Lean reuse and reductions with zero model rounds, and observation of verified evidence.
TEMPLATES = ("lean-reuse", "observe")
_ACTOR = "Trellin local researcher"


class AutomationError(ValueError):
    def __init__(self, code):
        self.code = code if code.startswith("automation.") else f"automation.{code}"
        super().__init__(self.code)


_CANDIDATES = """
SELECT claim.id AS claim_id, claim.title, claim.domain, packet.id AS packet_id,
       coalesce(packet.proof_ready, false) AS proof_ready, lean.id AS lean_statement_id,
       coalesce(lean.check_ready, false) AS lean_ready
""" + _CLAIMS_FROM + """
    LEFT JOIN LATERAL (
        SELECT p.id, p.proof_ready FROM rm.formalization_status p
        WHERE p.claim_id = claim.id AND NOT p.superseded ORDER BY p.version DESC LIMIT 1
    ) packet ON true
    LEFT JOIN LATERAL (
        SELECT s.id, s.check_ready FROM rm.lean_statement_status s
        WHERE s.packet_id = packet.id AND s.body ->> 'theorem' = 'RMTheory.expected'
        ORDER BY s.check_ready DESC, s.id LIMIT 1
    ) lean ON true
""" + _CLAIMS_VISIBLE


def _missing(template, row):
    """The prerequisite a claim lacks for a template, or None when the template can run on it."""
    if row["packet_id"] is None:
        return "basis"
    if not row["proof_ready"]:
        return "questions"
    if template == "lean-reuse" and (row["domain"] != "lean-core" or not row["lean_ready"]):
        return "lean_statement"
    return None


def templates(store):
    """Each template with the claims it can run on now and the prerequisite missing for the others."""
    rows = store.query("human", _CANDIDATES + _CLAIM_ORDER + " LIMIT 100")
    return {"templates": [
        {"id": template, "claims": [
            {"claim_id": str(row["claim_id"]), "title": row["title"], "missing": _missing(template, row)}
            for row in rows
        ]}
        for template in TEMPLATES
    ]}


def _specification(template, row):
    packet = str(row["packet_id"])
    if template == "lean-reuse":
        return {"packet_id": packet, "lean_statement_id": str(row["lean_statement_id"]), "metric": "exact-closure",
                "actions": ["lean"], "lean_model_rounds": 0}
    metric = "flow-interval-width" if row["domain"] == "fixed-directed-max-flow" else "exact-closure"
    return {"packet_id": packet, "metric": metric, "actions": []}


def _fields(document, fields):
    if not isinstance(document, dict) or document.keys() != fields:
        raise AutomationError("invalid")


def _uuid(value):
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise AutomationError("invalid") from None


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise AutomationError("reason")
    return value.strip()


def propose(store, document):
    """Record the exact policy for a template and claim. Nothing runs until it is approved."""
    _fields(document, {"claim_id", "template"})
    claim_id, template = _uuid(document["claim_id"]), document["template"]
    if template not in TEMPLATES:
        raise AutomationError("invalid")
    rows = store.query("human", _CANDIDATES + " AND claim.id = %s", claim_id)
    if not rows:
        raise AutomationError("missing")
    missing = _missing(template, rows[0])
    if missing:
        raise AutomationError(f"unavailable_{missing}")
    policy = research_progress.propose(_specification(template, rows[0]), store=store)
    return review(store, policy["id"])


_REVIEW = """
SELECT p.id, p.claim_id, c.title AS claim_title, p.policy, p.policy_sha256, p.state, p.approved_at,
       p.stopped_at, p.proposed_at
FROM rm.research_progress_status p JOIN rm.claims c ON c.id = p.claim_id WHERE p.id = %s
"""


def review(store, policy_id):
    """The exact proposal a researcher approves: claim, actions, model allowance and budget."""
    rows = store.query("human", _REVIEW, policy_id)
    if not rows:
        raise AutomationError("missing")
    row = rows[0]
    policy = row["policy"]
    return {
        "policy_id": str(row["id"]), "claim_id": str(row["claim_id"]), "claim_title": row["claim_title"],
        "fingerprint": row["policy_sha256"], "state": row["state"],
        "approved": row["approved_at"] is not None, "stopped": row["stopped_at"] is not None,
        "template": "lean-reuse" if policy.get("actions") == ["lean"] else "observe",
        "metric": policy.get("metric"), "actions": policy.get("actions"),
        "model_rounds": policy.get("lean_model_rounds", 0),
        "budget_seconds": policy.get("budget_seconds"), "max_rounds": policy.get("max_rounds"),
    }


def approve(store, document):
    """Approve exactly the reviewed proposal. A changed proposal has a different fingerprint and is refused."""
    _fields(document, {"policy_id", "fingerprint", "reason"})
    policy_id, reason = _uuid(document["policy_id"]), _reason(document["reason"])
    current = review(store, policy_id)
    if current["fingerprint"] != document["fingerprint"]:
        raise AutomationError("stale")
    if current["stopped"]:
        raise AutomationError("stopped")
    try:
        # Rules run with the project's scheduler and verifier logins; check them before approving.
        store.verify_runtime_roles(roles=("scheduler", "verifier"))
    except (ValueError, OSError):
        raise AutomationError("worker_unavailable") from None
    # The database also records the authenticated local researcher for this session.
    research_progress.approve(policy_id, current["fingerprint"], _ACTOR, reason, store=store)
    return review(store, policy_id)


def stop(store, document):
    """Stop a rule. Its plans stop; recorded results and run history stay."""
    _fields(document, {"policy_id", "reason"})
    policy_id, reason = _uuid(document["policy_id"]), _reason(document["reason"])
    review(store, policy_id)
    research_progress.stop(policy_id, reason, store=store)
    return review(store, policy_id)
