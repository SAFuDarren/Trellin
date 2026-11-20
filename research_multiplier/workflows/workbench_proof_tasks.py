"""Expose exact Lean scope and authorized proof policies to the local workbench."""

import re
from uuid import UUID

from research_multiplier.models.model_errors import ModelFailure
from research_multiplier.protocol.serialization import fingerprint
from research_multiplier.storage.lean_progress import ACTIVE_LEAN_POLICY_SQL
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows import research_progress, research_resume


class ProofTaskError(ValueError):
    def __init__(self, code):
        self.code = code if code.startswith("proof_task.") else f"proof_task.{code}"
        super().__init__(self.code)


_POLICY = f"""
SELECT p.id, p.policy_sha256, p.state, p.stopped_at,
       (p.policy #>> '{{lean,model_rounds}}')::integer AS model_rounds,
       p.policy #> '{{lean,private_model}}' AS private_model,
       p.policy #> '{{lean,public_model}}' AS public_model,
       run.result ->> 'status' AS phase,
       jsonb_path_query_array(run.result, '$.rounds[*].usage') AS model_usage,
       {ACTIVE_LEAN_POLICY_SQL} AS running
FROM rm.research_progress_status p
LEFT JOIN rm.lean_progress_last_run run ON run.policy_id = p.id
WHERE p.claim_id = %s AND p.packet_id = %s AND p.approved_at IS NOT NULL
  AND p.policy #>> '{{lean,statement_id}}' = %s::text
  AND p.policy #>> '{{lean,statement_sha256}}' = %s
  AND (p.policy #> '{{lean,public_model}}' IS NULL OR p.policy #> '{{lean,public_workspace}}' = (
      SELECT jsonb_build_array(workspace_id, database_id) FROM rm.current_workspace_binding()
  ))
ORDER BY (p.stopped_at IS NULL) DESC, p.approved_at DESC, p.id DESC LIMIT 1
"""

_RECEIPT = """
SELECT e.id, e.review_status, e.kind
FROM rm.evidence_foundation_status e
JOIN rm.lean_check_status checked ON checked.id = e.lean_check_id
WHERE checked.statement_id = %s AND e.verdict = 'verified' AND e.diff_status = 'exact'
  AND NOT e.needs_recheck AND e.issuance_sha256 IS NOT NULL
  AND e.review_status IN ('approved', 'pending')
ORDER BY (e.review_status = 'approved') DESC, e.issued_at DESC, e.id LIMIT 1
"""

_BASIS = """
SELECT item_version_id, version_sha256, packet_label, logical_key,
       body ->> 'natural' AS statement, body -> 'formal' AS formal
FROM rm.get_packet_basis(%s)
WHERE body ->> 'kind' IS DISTINCT FROM 'goal'
ORDER BY packet_label NULLS LAST, logical_key, item_version_id
"""


def _local_model(sensitivity):
    if sensitivity != "restricted":
        return None
    from research_multiplier.model_adapters import ModelConfig

    try:
        return ModelConfig.from_env("LEAN_AUTHOR", private=True).private_provenance()
    except ModelFailure:
        return None


def _public_model_identity(connection_id):
    from research_multiplier.models.connections import ConnectionError, Registry

    try:
        return Registry().identity(connection_id)
    except ConnectionError:
        return None


def _public_models_supported(store, sensitivity):
    if sensitivity != "public":
        return False
    row, = store.query("human", """
        SELECT to_regprocedure('rm.propose_public_model_lean_progress(uuid,integer,integer,jsonb)')
               IS NOT NULL AS available
    """)
    return row["available"]


def _state(record, lean, policy, receipt, local_model):
    scope = record["scope"]
    if record["next"]["action"] in ("review_interpretation", "review_evidence_conflict", "reopen_for_recheck"):
        return "blocked", "proof_task.decision_needed"
    if receipt:
        return "verified", None
    if record["status"] in ("proved", "refuted", "archived", "superseded"):
        return "blocked", "proof_task.completed"
    if record["details"]["domain"] != "lean-core":
        return "blocked", "proof_task.unsupported"
    if not scope["proof_ready"]:
        return "blocked", "proof_task.scope_unconfirmed"
    if not lean or not lean["check_ready"]:
        return "blocked", "proof_task.lean_unconfirmed"
    if record["decisions"]["items"]:
        return "blocked", "proof_task.decision_needed"
    if policy is None:
        return "ready", None
    if policy["stopped_at"] is not None:
        return "stopped", "proof_task.stopped"
    if policy["state"] != "awaiting_evidence":
        return "waiting", "proof_task.policy_conflict"
    if policy.get("running"):
        return "running", None
    if policy["model_rounds"]:
        public_model = policy.get("public_model")
        if public_model is not None:
            if _public_model_identity(public_model["id"]) != public_model:
                return "waiting", "proof_task.external_unavailable"
        elif policy["private_model"] is None:
            return "waiting", "proof_task.policy_conflict"
        elif policy["private_model"] != local_model:
            return "waiting", "proof_task.local_unavailable"
    if policy["phase"] not in (None, "search_continues"):
        return "waiting", "proof_task.unresolved"
    return "authorized", None


def _read(store, claim_id):
    try:
        record = research_resume.snapshot(store, claim_id, limit=research_resume.MAX_LIMIT, details=True)
    except DatabaseOperationError:
        raise
    except ValueError:
        raise ProofTaskError("missing") from None
    details = record["details"]
    lean, packet = details["lean"], details["packet"]
    sensitivity = details.get("sensitivity")
    model = _local_model(sensitivity)
    receipt = policy = token = None
    basis = store.query("human", _BASIS, packet["id"]) if packet else []
    if lean and packet:
        policies = store.query("human", _POLICY, claim_id, packet["id"], lean["id"], lean["statement_sha256"])
        policy = policies[0] if policies else None
        receipts = store.query("human", _RECEIPT, lean["id"])
        receipt = receipts[0] if receipts else None
        if record["scope"]["proof_ready"] and lean["check_ready"]:
            token = fingerprint({
                "claim": str(claim_id), "contract_sha256": details["contract_sha256"],
                "packet": str(packet["id"]), "packet_sha256": packet["packet_sha256"],
                "statement": str(lean["id"]), "statement_sha256": lean["statement_sha256"],
                "basis": [(str(item["item_version_id"]), item["version_sha256"]) for item in basis],
            })
    state, blocker = _state(record, lean, policy, receipt, model)
    activity = (record.get("lean_advance", {})
                if policy and str(policy["id"]) == str(details["progress_policy_id"]) else {})
    obligations = activity.get("obligations", {}).get("items", [])
    if not obligations:
        obligations = [
            obligation for reduction in record["reductions"]["items"] for obligation in reduction["obligations"]
            if not obligation["verified"]
        ]
    public_model = policy.get("public_model") if policy else None
    supports_external = _public_models_supported(store, sensitivity) if token is not None else False
    replace_stopped_public_model = state == "stopped" and supports_external
    result = {
        "claim_id": str(claim_id), "title": record["title"], "state": state, "blocker": blocker,
        "can_start": state in ("ready", "authorized") or replace_stopped_public_model,
        "can_stop": bool(policy and policy["stopped_at"] is None and state != "verified"),
        "scope": {
            "token": token, "proposition": record["scope"]["lean_proposition"],
            "basis": [{"label": item["packet_label"] or item["logical_key"],
                       "statement": item["statement"], "formal": item["formal"]} for item in basis],
            "omitted_basis": 0,
        },
        "decisions": [{key: row[key] for key in ("kind", "question")} for row in record["decisions"]["items"]],
        "policy": {"id": str(policy["id"]), "fingerprint": policy["policy_sha256"],
                   "model_rounds": policy["model_rounds"], "public_model": public_model,
                   "model_usage": policy.get("model_usage") or []} if policy else None,
        "receipt": {**receipt, "id": str(receipt["id"])} if state == "verified" else None,
        "local_model_allowed": model is not None, "external_model_allowed": supports_external,
        "phase": policy["phase"] if policy else None,
        "obligations": [{"proposition": row["proposition"], "verified": bool(row.get("verified"))}
                        for row in obligations],
        "adopted": bool(state == "verified" and record["conclusion"]
                        and str(record["conclusion"]["reference"]["evidence_id"]) == str(receipt["id"])),
    }
    return result, packet, lean


def snapshot(store, claim_id):
    """Read proof state and its review basis from the selected workspace."""
    return _read(store, claim_id)[0]


def _validated(document, fields):
    if not isinstance(document, dict) or document.keys() != fields:
        raise ProofTaskError("invalid")


def _uuid(value):
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise ProofTaskError("invalid") from None


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ProofTaskError("invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ProofTaskError("invalid") from None
    return value.strip()


def _external_identity(document, rounds, view):
    selection = document["public_model"]
    _validated(selection, {"id", "fingerprint"})
    if (document["confirm_external"] is not True or not rounds
            or not isinstance(selection["fingerprint"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", selection["fingerprint"])):
        raise ProofTaskError("invalid")
    connection_id = str(_uuid(selection["id"]))
    if not view["external_model_allowed"]:
        raise ProofTaskError("external_unavailable")
    identity = _public_model_identity(connection_id)
    if identity is None or identity["fingerprint"] != selection["fingerprint"]:
        raise ProofTaskError("external_unavailable")
    return identity


def start(store, document):
    """Record an exact bounded authorization within the caller's human transaction."""
    fields = {"claim_id", "token", "model_rounds", "reason"}
    external = isinstance(document, dict) and "public_model" in document
    _validated(document, fields | {"public_model", "confirm_external"} if external else fields)
    claim_id = _uuid(document["claim_id"])
    token, rounds = document["token"], document["model_rounds"]
    if (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token)
            or type(rounds) is not int or not 0 <= rounds <= 8):
        raise ProofTaskError("invalid")
    reason = _text(document["reason"], 2000)
    store.query("human", "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", f"workbench-proof:{claim_id}")
    view, packet, lean = _read(store, claim_id)
    if view["scope"]["token"] != token:
        raise ProofTaskError("stale")
    if not view["can_start"]:
        raise ProofTaskError(view["blocker"] or "policy_conflict")
    public_model = _external_identity(document, rounds, view) if external else None
    known = view["policy"]
    if known is not None:
        if view["state"] == "stopped":
            if public_model is None or (known["public_model"] == public_model and known["model_rounds"] == rounds):
                raise ProofTaskError("stopped")
            known = None
        elif known["model_rounds"] != rounds or known["public_model"] != public_model:
            raise ProofTaskError("policy_conflict")
    if rounds and public_model is None and not view["local_model_allowed"]:
        raise ProofTaskError("local_unavailable")
    try:
        roles = ("scheduler", "verifier", "agent") if rounds else ("scheduler", "verifier")
        store.verify_runtime_roles(roles=roles)
    except (ValueError, OSError):
        raise ProofTaskError("worker_unavailable") from None
    specification = {"packet_id": str(packet["id"]), "lean_statement_id": str(lean["id"]),
                     "metric": "exact-closure", "actions": ["lean"], "lean_model_rounds": rounds}
    if public_model is not None:
        try:
            policy = research_progress.propose(specification, store=store, public_model=public_model)
        except DatabaseOperationError as error:
            if error.state == "23514":
                raise ProofTaskError("public_release_required") from None
            raise
    else:
        if rounds:
            specification["lean_model_scope"] = "local"
        policy = research_progress.propose(specification, store=store)
    identifier = str(policy["id"])
    if known is None:
        # The database separately records the authenticated login for this session.
        research_progress.approve(policy["id"], policy["policy_sha256"], "Trellin local researcher", reason, store=store)
    elif known["id"] != identifier or known["fingerprint"] != policy["policy_sha256"]:
        raise ProofTaskError("policy_conflict")
    return {"policy_id": identifier}


def stop(store, document):
    """Stop the displayed policy within its own claim and current Lean scope."""
    _validated(document, {"claim_id", "policy_id", "reason"})
    claim_id, policy_id = _uuid(document["claim_id"]), _uuid(document["policy_id"])
    reason = _text(document["reason"], 2000)
    store.query("human", "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", f"workbench-proof:{claim_id}")
    view = snapshot(store, claim_id)
    policy = view["policy"]
    if policy is None or policy["id"] != str(policy_id) or not (view["can_stop"] or view["state"] == "stopped"):
        raise ProofTaskError("stale")
    research_progress.stop(policy_id, reason, store=store)
    return {"policy_id": str(policy_id)}
