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
                "guidance_reason": "提交意见需要显式启用数据库版本 0070。"}
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
        reason = "请先确认当前课题的 Lean 陈述和依据。"
    elif not codex_schema:
        reason = "Codex 意见通道需要显式启用数据库版本 0071。"
    else:
        reason = "Codex 意见通道需要可外发的公开依据和已批准的公开推进策略。"
    return {"guidance": rows, "guidance_available": bool(channels),
            "guidance_channels": channels, "guidance_reason": reason}


class GuidanceError(ValueError):
    """An error that is safe to show in the local workbench."""


def add(store, claim_id, statement_id, request_id, node_id, text, *, audience="local", confirm_release=False):
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 128:
        raise GuidanceError("请选择图上的一个节点。")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1500 or "\x00" in text:
        raise GuidanceError("请输入 1 到 1500 个字符。")
    if audience not in ("local", "codex") or type(confirm_release) is not bool:
        raise GuidanceError("请选择有效的意见接收方。")
    if confirm_release != (audience == "codex"):
        raise GuidanceError("向 Codex 发送本条意见需要明确确认。")
    if not installed(store, "human"):
        raise GuidanceError("提交意见需要显式启用数据库版本 0070。")
    codex_schema = _codex_installed(store, "human")
    if audience == "codex" and not codex_schema:
        raise GuidanceError("Codex 意见通道需要显式启用数据库版本 0071。")
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
            raise GuidanceError("这个请求已用于另一条意见，请提交新请求。")
        return saved[0]
    anchor = _anchor(store, claim_id)
    if anchor is None or anchor["id"] != statement_id:
        raise GuidanceError("当前 Lean 陈述未确认或已经变更，请刷新后重试。")
    if audience not in _channels(store, anchor, codex_schema):
        raise GuidanceError("该课题没有启用此意见通道。")
    graph = proof_graph.snapshot(store, claim_id)
    if graph["claim"]["statement_id"] != str(statement_id) or not any(node["id"] == node_id for node in graph["nodes"]):
        raise GuidanceError("此节点已变更，请先刷新课题。")
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
