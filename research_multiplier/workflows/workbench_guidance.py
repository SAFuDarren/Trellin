"""Keep researcher messages attached to an exact, reviewed Lean scope."""

from uuid import UUID

from . import proof_graph


def installed(store, role):
    return store.query(role, """
        SELECT to_regprocedure('rm.pending_workbench_guidance(uuid)') IS NOT NULL AS installed
    """)[0]["installed"]


def _codex_installed(store, role):
    return store.query(role, """
        SELECT to_regprocedure('rm.released_workbench_guidance(uuid,uuid[])') IS NOT NULL AS installed
    """)[0]["installed"]


def _anchor(store, claim_id):
    rows = store.query("human", """
        SELECT s.id, c.sensitivity FROM rm.lean_statement_status s JOIN rm.claims c ON c.id = s.claim_id
        WHERE c.id = %s AND s."current" AND s.approved AND s.check_ready
        ORDER BY s.proposed_at DESC, s.id DESC LIMIT 1
    """, claim_id)
    return rows[0] if rows else None


def _channels(store, anchor, codex_schema):
    if anchor is None:
        return []
    if anchor["sensitivity"] == "restricted":
        return ["local"]
    if codex_schema and store.query("human", "SELECT rm.workbench_codex_available(%s) AS allowed",
                                   anchor["id"])[0]["allowed"]:
        return ["codex"]
    return []


def activity(store, claim_id):
    if not installed(store, "human"):
        return {"guidance": [], "guidance_available": False, "guidance_channels": [],
                "guidance_reason": "Notes need database version 0070 to be enabled."}
    anchor = _anchor(store, claim_id)
    codex_schema = _codex_installed(store, "human")
    channels = _channels(store, anchor, codex_schema)
    audience = "n.audience" if codex_schema else "'local'::text AS audience"
    rows = store.query("human", f"""
        SELECT n.id, n.node_id, n.text, n.created_at, s.submitted_at, s.attempt_id, {audience},
               n.statement_id IS NOT DISTINCT FROM %s::uuid AS current_scope
        FROM rm.workbench_guidance n
        LEFT JOIN rm.workbench_guidance_submissions s ON s.guidance_id = n.id
        WHERE n.claim_id = %s
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT 40
    """, anchor["id"] if anchor else None, claim_id)
    if channels:
        reason = ""
    elif anchor is None:
        reason = "Confirm the Lean statement and its basis for this claim first."
    elif not codex_schema:
        reason = "Notes to Codex need database version 0071 to be enabled."
    else:
        reason = "Notes to Codex need a public basis that may be shared and an approved public automation policy."
    return {"guidance": rows, "guidance_available": bool(channels),
            "guidance_channels": channels, "guidance_reason": reason}


class GuidanceError(ValueError):
    """An error that is safe to show in the local workbench."""


def add(store, claim_id, statement_id, request_id, node_id, text, *, audience="local", confirm_release=False):
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 128:
        raise GuidanceError("Choose a node on the graph.")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1500 or "\x00" in text:
        raise GuidanceError("Enter 1 to 1500 characters.")
    if audience not in ("local", "codex") or type(confirm_release) is not bool:
        raise GuidanceError("Choose who should receive the note.")
    if confirm_release != (audience == "codex"):
        raise GuidanceError("Sending this note to Codex needs an explicit confirmation.")
    if not installed(store, "human"):
        raise GuidanceError("Notes need database version 0070 to be enabled.")
    codex_schema = _codex_installed(store, "human")
    if audience == "codex" and not codex_schema:
        raise GuidanceError("Notes to Codex need database version 0071 to be enabled.")
    text = text.strip()
    # A retry must find the original receipt even if the scope subsequently changed.
    audience_field = "n.audience" if codex_schema else "'local'::text AS audience"
    saved = store.query("human", f"""
        SELECT n.id, n.node_id, n.text, n.created_at, s.submitted_at, s.attempt_id, {audience_field}
        FROM rm.workbench_guidance n
        LEFT JOIN rm.workbench_guidance_submissions s ON s.guidance_id = n.id
        WHERE n.claim_id = %s AND n.statement_id = %s AND n.id = %s
    """, claim_id, statement_id, request_id)
    if saved:
        if (saved[0]["node_id"], saved[0]["text"], saved[0]["audience"]) != (node_id, text, audience):
            raise GuidanceError("This request was already used for another note; send a new one.")
        return saved[0]
    anchor = _anchor(store, claim_id)
    if anchor is None or anchor["id"] != statement_id:
        raise GuidanceError("The Lean statement is unconfirmed or has changed; refresh and try again.")
    if audience not in _channels(store, anchor, codex_schema):
        raise GuidanceError("This claim does not accept notes on this channel.")
    graph = proof_graph.snapshot(store, claim_id)
    if graph["claim"]["statement_id"] != str(statement_id) or not any(node["id"] == node_id for node in graph["nodes"]):
        raise GuidanceError("This node has changed; refresh the claim first.")
    if codex_schema:
        return store.query("human", """
            SELECT id, node_id, text, created_at, audience FROM rm.add_workbench_guidance(%s,%s,%s,%s,%s,%s,%s)
        """, request_id, claim_id, statement_id, node_id, text, audience, confirm_release)[0]
    return store.query("human", """
        SELECT id, node_id, text, created_at, 'local'::text AS audience
        FROM rm.add_workbench_guidance(%s,%s,%s,%s,%s)
    """, request_id, claim_id, statement_id, node_id, text)[0]


def _json_rows(rows):
    return [{**row, "id": str(row["id"])} for row in rows]


def pending(store, statement_id, audience="local"):
    """Read pending messages for the worker's governed destination."""
    if audience == "local" and installed(store, "verifier"):
        return _json_rows(store.query("verifier", "SELECT * FROM rm.pending_workbench_guidance(%s)", statement_id))
    if audience == "codex" and _codex_installed(store, "verifier"):
        return _json_rows(store.query("verifier", "SELECT * FROM rm.pending_workbench_guidance(%s,%s)",
                                      statement_id, audience))
    return []


def released(store, statement_id, hint_ids):
    """Read the exact release records for a previously persisted Codex request."""
    if not hint_ids or not _codex_installed(store, "verifier"):
        return []
    return _json_rows(store.query("verifier", "SELECT * FROM rm.released_workbench_guidance(%s,%s::uuid[])",
                                  statement_id, [UUID(str(hint)) for hint in hint_ids]))


def submitted(store, statement_id, hint_ids, model_attempt_id):
    if hint_ids:
        store.query("verifier", "SELECT rm.record_workbench_guidance_submission(%s,%s::uuid[],%s)",
                    statement_id, [UUID(str(hint)) for hint in hint_ids], model_attempt_id)
