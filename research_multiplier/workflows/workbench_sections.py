"""Workspace-wide read models behind the workbench's review, library, conversation, team and progress pages."""

import os
import platform
import shutil

from research_multiplier.policy import project_budget
from research_multiplier.workflows import (proof_graph, research_review, workbench_activity, workbench_clarification,
                                          workbench_goal_review, workbench_guidance, workbench_review, workspace_events)

_MAX_THREAD_CLAIMS = 30
_MAX_THREAD_EVENTS = 40
_CONVERSATION_KINDS = {"agent_feedback", "verifier_feedback", "model"}


_REVIEW_RESULTS = """
SELECT evidence.id, coalesce(nullif(checked.body ->> 'source_declaration', ''),
                             nullif(checked.body ->> 'candidate_theorem', '')) AS declaration,
       checked.body ->> 'target' AS target
FROM rm.evidence_foundation_status evidence
JOIN rm.lean_check_status checked ON checked.id = evidence.lean_check_id
WHERE evidence.id = ANY(%s::uuid[])
"""


def inbox(store):
    """Review items with their claim and, when recorded, the checked declaration."""
    items = research_review.inbox(store, None, 200)
    titles = {claim["id"]: claim["title"] for claim in proof_graph.claims(store)}
    evidence_ids = [item["item_id"] for item in items if item["kind"] == "review_evidence"]
    results = {row["id"]: row for row in store.query("human", _REVIEW_RESULTS, evidence_ids)} if evidence_ids else {}
    for item in items:
        claim = str(item["claim_id"]) if item["claim_id"] else None
        item["claim_title"] = titles.get(claim)
        if item["kind"] == "review_evidence" and (result := results.get(item["item_id"])):
            item["detail"] = {**item["detail"], **{key: value for key, value in result.items() if key != "id" and value}}
    identity = workbench_review.capability(store)
    result = {"items": items, **identity, **workspace_events.capability(store),
              **workbench_clarification.capability(store)}
    if identity:
        result["open_research_goals"] = workbench_goal_review.open_goals(store)
    return result


_LIBRARY = """
SELECT i.id, i.logical_key, i.version, i.origin, i.admitted, i.needs_recheck, i.created_at,
       i.body ->> 'kind' AS kind, left(i.body ->> 'natural', 2000) AS natural,
       i.body #>> '{formal,lean,declaration}' AS declaration,
       CASE jsonb_typeof(i.body #> '{formal,lean}')
           WHEN 'object' THEN i.body #>> '{formal,lean,proposition}'
           WHEN 'string' THEN i.body #>> '{formal,lean}' END AS lean,
       coalesce(i.body #>> '{formal,latex}', i.body #>> '{formal,formula}') AS formula,
       EXISTS (SELECT FROM rm.formal_item_retirements r WHERE r.version_id = i.id) AS retired,
       coalesce(uses.claims, '[]'::jsonb) AS used_by
FROM rm.formal_item_status i
LEFT JOIN LATERAL (
    SELECT jsonb_agg(DISTINCT jsonb_build_object('id', c.id, 'title', c.title)) AS claims
    FROM rm.verification_basis_use u
    JOIN rm.lean_check_status k ON k.id = u.check_id
    JOIN rm.lean_statement_status s ON s.id = k.statement_id
    JOIN rm.claims c ON c.id = s.claim_id
    WHERE u.version_id = i.id
) uses ON true
ORDER BY i.created_at DESC, i.logical_key, i.version DESC
LIMIT 500
"""


def library(store):
    """Every recorded definition, hypothesis and theorem version, with the claims whose checks used it."""
    items = store.query("human", _LIBRARY)
    goals = [item["id"] for item in items if item["kind"] == "goal"]
    if goals and store.query("human", """
        SELECT to_regprocedure('rm.library_statement_revisions(uuid[])') IS NOT NULL AS installed
    """)[0]["installed"]:
        revisions = {row["id"]: row["revision"] for row in store.query(
            "human", "SELECT * FROM rm.library_statement_revisions(%s)", goals,
        )}
        for item in items:
            if item["id"] in revisions:
                item["statement_revision"] = revisions[item["id"]]
    return {"items": items}


_JOBS = """
SELECT t.id, t.claim_id, c.title AS claim_title, t.kind, t.enqueued_at, t.execution_ms, t.queue_ms,
       t.finding, t.failure_code, t.cancelled_at, a.submitted_at,
       coalesce(a.model_provider, CASE WHEN j.payload ? 'workbench_guidance_model' THEN 'codex-cli' END) AS provider,
       coalesce(a.model_id, j.payload ->> 'workbench_guidance_model') AS model,
       (j.payload -> 'lean_search' = 'true'::jsonb OR j.payload -> 'lean_author' = 'true'::jsonb) AS model_job,
       j.payload ->> 'target' AS target, j.payload ->> 'workbench_focus_node_id' AS node_id,
       CASE WHEN j.status = 'running' AND j.lease_until <= statement_timestamp() THEN 'lease_expired'
            WHEN j.status = 'queued' AND j.ready_at > statement_timestamp() THEN 'waiting_retry'
            ELSE j.status::text END AS state,
       CASE WHEN j.status = 'running' AND j.lease_until > statement_timestamp() THEN j.leased_at END AS running_since
FROM rm.jobs j
JOIN rm.research_task_status t ON t.id = j.id
LEFT JOIN rm.attempts a ON a.id = t.attempt_id
LEFT JOIN rm.claims c ON c.id = t.claim_id
ORDER BY (j.status = 'running' AND j.lease_until > statement_timestamp()) DESC, (j.status = 'queued') DESC,
         coalesce(a.submitted_at, t.cancelled_at, t.enqueued_at) DESC, j.id
LIMIT 80
"""

_AGENTS = """
SELECT a.model_provider AS provider, a.model_id AS model, count(*) AS runs, max(a.submitted_at) AS last_at,
       count(*) FILTER (WHERE t.failure_code IS NOT NULL) AS failures,
       round(avg(t.execution_ms)) AS average_ms,
       sum(t.input_tokens) AS input_tokens, sum(t.output_tokens) AS output_tokens, sum(t.cost_usd) AS cost_usd
FROM rm.research_task_status t JOIN rm.attempts a ON a.id = t.attempt_id
WHERE a.model_provider IS NOT NULL
GROUP BY a.model_provider, a.model_id
ORDER BY last_at DESC NULLS LAST, provider, model
"""

_VERIFIER = """
SELECT count(*) AS runs, max(a.submitted_at) AS last_at,
       count(*) FILTER (WHERE t.failure_code IS NOT NULL) AS failures,
       round(avg(t.execution_ms)) AS average_ms
FROM rm.research_task_status t JOIN rm.attempts a ON a.id = t.attempt_id
WHERE a.model_provider IS NULL AND t.kind = 'verify'
"""


def _services(agents):
    """Model services the adapters know about: installed command-line tools and configured API keys."""
    from research_multiplier.model_adapters import PROVIDERS

    usage = {}
    for agent in agents:
        usage[agent["provider"]] = usage.get(agent["provider"], 0) + agent["runs"]
    services = []
    for name, (transport, location, _family, key) in PROVIDERS.items():
        if transport == "cli":
            ready, via, detail = bool(shutil.which(location)), "command", location
        elif key:
            ready, via, detail = bool(os.environ.get(key)), "environment", key
        else:
            ready, via, detail = None, "local" if location and "127.0.0.1" in location else "endpoint", ""
        if ready is None and not usage.get(name):
            continue
        services.append({"provider": name, "transport": transport, "ready": ready, "via": via, "detail": detail,
                         "runs": usage.get(name, 0)})
    services.sort(key=lambda service: (-service["runs"], not service["ready"], service["provider"]))
    return services


def team(store):
    """Who is working: model agents, the Lean verifier, the job queue, this machine and the model allowance."""
    agents = store.query("human", _AGENTS)
    verifier = store.query("human", _VERIFIER)[0]
    return {
        "jobs": store.query("human", _JOBS),
        "agents": agents,
        "verifier": verifier,
        "services": _services(agents),
        "machine": {"name": platform.node() or "", "system": platform.system(), "arch": platform.machine(),
                    "python": platform.python_version()},
        "budget": project_budget.status(store),
    }


_PROGRESS = """
SELECT p.id, p.claim_id, c.title AS claim_title, p.state, p.policy, p.proposed_at, p.approved_by, p.approved_at,
       p.stopped_at, p.stop_reason, p.latest_round, p.progress, p.current_evidence_id,
       run.recorded_at AS last_run_at, run.result ->> 'status' AS last_status,
       left(run.result ->> 'reason', 600) AS last_reason
FROM rm.research_progress_status p
JOIN rm.claims c ON c.id = p.claim_id
LEFT JOIN rm.lean_progress_last_run run ON run.policy_id = p.id
ORDER BY p.stopped_at IS NOT NULL, p.approved_at IS NULL, p.proposed_at DESC, p.id
LIMIT 100
"""


def progress(store):
    """Approved, proposed and stopped progress policies with their latest Lean pass."""
    return {"policies": store.query("human", _PROGRESS)}


_NODE_FIELDS = ("id", "kind", "title", "citation", "proposition", "formula", "status")


def _nodes(store, claim_id):
    try:
        graph = proof_graph.snapshot(store, claim_id)
    except ValueError:
        return {}
    return {node["id"]: {key: node[key] for key in _NODE_FIELDS if key in node} for node in graph["nodes"]}


def threads(store):
    """Group recorded model replies, verifier feedback and researcher guidance by the graph node they concern."""
    found = []
    for claim in proof_graph.claims(store)[:_MAX_THREAD_CLAIMS]:
        try:
            events = workbench_activity.snapshot(store, claim["id"])["activity"]
        except ValueError:
            continue
        guidance = workbench_guidance.activity(store, claim["id"]).get("guidance", [])
        by_node = {}
        for event in events:
            if event["kind"] not in _CONVERSATION_KINDS:
                continue
            if event["kind"] == "model" and event["state"] not in {"running", "queued", "waiting_retry"}:
                continue
            by_node.setdefault(event["node_id"], []).append(event)
        for note in guidance:
            by_node.setdefault(note["node_id"], []).append({
                "id": f"guidance:{note['id']}", "kind": "guidance", "state": "recorded", "text": note["text"],
                "at": note.get("submitted_at") or note["created_at"], "audience": note.get("audience", "local"),
                "node_id": note["node_id"]})
        if not by_node:
            continue
        nodes = _nodes(store, claim["id"])
        for node_id, items in by_node.items():
            items.sort(key=lambda event: str(event.get("at") or ""))
            live = [event for event in items if event["kind"] == "model"]
            models = sorted({event["model"] for event in items if event.get("model")})
            stamps = [str(event["at"]) for event in items if event.get("at")]
            found.append({
                "claim_id": claim["id"], "claim_title": claim["title"], "node_id": node_id,
                "node": nodes.get(node_id, {"id": node_id, "kind": node_id.split(":")[0]}),
                "live": len(live), "models": models, "last_at": max(stamps) if stamps else None,
                "events": items[-_MAX_THREAD_EVENTS:],
            })
    found.sort(key=lambda thread: (thread["live"] > 0, thread["last_at"] or ""), reverse=True)
    return {"threads": found}


def review(store, evidence_id, action, actor, reason):
    """Record the researcher's decision on one verified result from the review page."""
    return workbench_review.legacy(store, evidence_id, action, actor, reason)
