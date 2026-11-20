"""Persist a researcher's private editor state for an exact proof node."""

from . import proof_graph, workbench_guidance


class ProofDraftError(ValueError):
    """A stable error code for the bundled proof composer."""


def installed(store):
    [row] = store.query("human", """
        SELECT to_regprocedure('rm.read_workbench_proof_draft(uuid,uuid,text)') IS NOT NULL AS installed
    """)
    return row["installed"]


def _require_node(store, claim_id, node_id):
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 128 or "\x00" in node_id:
        raise ProofDraftError("proof_draft_invalid")
    if not installed(store):
        raise ProofDraftError("proof_draft_unavailable")
    try:
        graph = proof_graph.snapshot(store, claim_id, references=False)
    except ValueError:
        raise ProofDraftError("proof_draft_missing") from None
    if not any(node["id"] == node_id for node in graph["nodes"]):
        raise ProofDraftError("proof_draft_missing")


def read(store, claim_id, node_id):
    _require_node(store, claim_id, node_id)
    rows = store.query("human", """
        SELECT * FROM rm.read_workbench_proof_draft(%s,%s,%s)
    """, store.workspace_id, claim_id, node_id)
    return {"draft": rows[0] if rows else None}


def save(store, claim_id, node_id, expected_revision, text):
    if not isinstance(text, str) or not 1 <= len(text) <= 1500 or not text.strip() or "\x00" in text:
        raise ProofDraftError("proof_draft_invalid")
    _require_node(store, claim_id, node_id)
    rows = store.query("human", """
        SELECT * FROM rm.save_workbench_proof_draft(%s,%s,%s,%s,%s)
    """, store.workspace_id, claim_id, node_id, expected_revision, text)
    if not rows:
        raise ProofDraftError("proof_draft_conflict")
    return {"draft": rows[0]}


def _discard_revision(store, claim_id, node_id, expected_revision, required_text=None):
    [row] = store.query("human", """
        SELECT rm.discard_workbench_proof_draft(%s,%s,%s,%s,%s) AS discarded
    """, store.workspace_id, claim_id, node_id, expected_revision, required_text)
    return row["discarded"]


def discard(store, claim_id, node_id, expected_revision):
    _require_node(store, claim_id, node_id)
    if not _discard_revision(store, claim_id, node_id, expected_revision):
        raise ProofDraftError("proof_draft_conflict")
    return {"draft": None}


def queue_guidance(store, claim_id, statement_id, request_id, node_id, text, audience, confirmed, draft_revision):
    """Queue guidance and consume a matching saved draft within the caller's transaction."""
    receipt = workbench_guidance.add(
        store, claim_id, statement_id, request_id, node_id, text, audience=audience, confirm_release=confirmed,
    )
    if draft_revision is not None:
        discarded = installed(store) and _discard_revision(store, claim_id, node_id, draft_revision, text)
        receipt = {**receipt, "draft_consumed": discarded}
    return receipt
