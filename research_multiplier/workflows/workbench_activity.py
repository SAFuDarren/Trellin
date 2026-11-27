"""Bounded, claim-scoped activity from the existing job and Lean pass ledgers."""

from uuid import UUID

from research_multiplier.storage.lean_progress import ACTIVE_LEAN_POLICY_SQL

_MAX_ACTIVITY = 60


class ClaimActivityNotFound(ValueError):
    """The selected workspace does not have this frozen claim."""


_SNAPSHOT = f"""
WITH selected AS MATERIALIZED (
    SELECT id, contract_sha256 FROM rm.claims
    WHERE id = %s::uuid AND contract_sha256 IS NOT NULL
),
jobs AS (
    SELECT j.id, t.kind, t.enqueued_at, a.submitted_at, t.cancelled_at, t.finding, t.failure_code,
           j.payload ->> 'target' AS target, j.payload ->> 'lean_progress_policy_id' AS policy_id,
           j.payload ->> 'workbench_focus_node_id' AS workbench_node,
           coalesce(a.model_provider, CASE WHEN j.payload ? 'workbench_guidance_model' THEN 'codex-cli' END)
               AS model_provider,
           coalesce(a.model_id, j.payload ->> 'workbench_guidance_model') AS model_id,
           a.model_parameters ->> 'reasoning_effort' AS effort,
           coalesce(j.payload ->> 'lean_statement_id', j.payload ->> 'author_statement_id') AS statement_id,
           j.payload -> 'lean_search' = 'true'::jsonb AS model_job,
           j.payload -> 'lean_author' = 'true'::jsonb AS author_job,
           CASE WHEN j.status = 'running' AND j.lease_until <= statement_timestamp() THEN 'lease_expired'
                WHEN j.status = 'queued' AND j.ready_at > statement_timestamp() THEN 'waiting_retry'
                ELSE j.status::text END AS state
    FROM selected
    JOIN rm.jobs j ON (j.claim_id,j.contract_sha256) = (selected.id,selected.contract_sha256)
    JOIN rm.research_task_status t ON t.id = j.id
    LEFT JOIN rm.attempts a ON a.id = t.attempt_id
    ORDER BY (j.status = 'running' AND j.lease_until > statement_timestamp()) DESC,
             (j.status = 'queued') DESC, coalesce(a.submitted_at, t.cancelled_at, t.enqueued_at) DESC, j.id
    LIMIT 36
),
policies AS MATERIALIZED (
    SELECT p.id, p.policy #>> '{{lean,statement_id}}' AS statement_id,
           {ACTIVE_LEAN_POLICY_SQL} AS running
    FROM selected JOIN rm.research_progress_status p
      ON (p.claim_id,p.contract_sha256) = (selected.id,selected.contract_sha256)
    WHERE p.policy ? 'lean' AND p.approved_at IS NOT NULL
    ORDER BY running DESC, p.approved_at DESC, p.id LIMIT 8
),
passes AS (
    SELECT p.id AS policy_id, p.statement_id, history.recorded_at,
           jsonb_build_object('status', history.result ->> 'status',
                              'reason', left(history.result ->> 'reason', 1000),
                              'reduction_id', history.result ->> 'reduction_id',
                              'rounds', history.result -> 'rounds',
                              'route_attempts', history.result -> 'route_attempts') AS result
    FROM policies p CROSS JOIN LATERAL rm.lean_progress_history(p.id, 4) history
    ORDER BY history.recorded_at DESC, p.id LIMIT 16
),
obligations AS (
    SELECT route.implication_id, requirement.proposition, requirement.position
    FROM selected JOIN rm.lean_statement_status statement ON statement.claim_id = selected.id
         AND statement."current" AND statement.contract_sha256 = selected.contract_sha256
    CROSS JOIN LATERAL (
        SELECT r.implication_id, r.obligations FROM rm.lean_reductions r
        WHERE r.statement_id = statement.id ORDER BY r.recorded_at DESC, r.implication_id LIMIT 48
    ) route
    CROSS JOIN LATERAL unnest(route.obligations) WITH ORDINALITY AS requirement(proposition, position)
)
SELECT selected.id AS claim_id,
       coalesce((SELECT jsonb_agg(to_jsonb(j)) FROM jobs j), '[]'::jsonb) AS jobs,
       coalesce((SELECT jsonb_agg(to_jsonb(p)) FROM policies p), '[]'::jsonb) AS policies,
       coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.recorded_at DESC) FROM passes p), '[]'::jsonb) AS passes,
       coalesce((SELECT jsonb_agg(to_jsonb(o)) FROM obligations o), '[]'::jsonb) AS obligations
FROM selected
"""

_JOB_KINDS = {
    "model": "Model proposal",
    "lean": "Lean verification",
    "task": "Research task",
}
_JOB_PHASES = {
    "running": "leased to a worker",
    "queued": "queued",
    "waiting_retry": "waiting for a retry",
    "lease_expired": "worker lease expired",
    "failed": "failed",
    "cancelled": "cancelled",
    "succeeded": "finished",
}
_LIVE_ORDER = {"running": 2, "queued": 1, "waiting_retry": 1}


def _identifier(value):
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _text(value):
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value[:1000] if char.isprintable() or char in "\n\t").strip()


def _label(code):
    return _text(code).replace("_", " ")


def _recorded_state(value):
    state = _text(value)
    return state if state and state not in _LIVE_ORDER else "recorded"


def _event(identifier, kind, state, text, at, *, node, goal, **references):
    return {"id": identifier, "kind": kind, "state": state, "text": text, "at": at,
            "node_id": node, "fallback_node_id": goal,
            **{key: value for key, value in references.items() if value is not None}}


def _graph_node(value, goal):
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    kind = parts[0]
    if kind == "goal":
        return goal if value == goal else None
    if kind in {"item", "local-lemma", "lean"} and len(parts) == 2 and _identifier(parts[1]):
        return value
    if (kind == "obligation" and len(parts) == 3
            and _identifier(parts[1]) and parts[2] in {"1", "2", "3", "4", "5", "6", "7", "8"}):
        return value
    return None


def _job_event(job, goal):
    kind = ("model" if job["model_job"] or job["author_job"]
            else "lean" if job["statement_id"] and job["kind"] == "verify" else "task")
    action = _JOB_KINDS[kind]
    if kind == "lean" and job["target"] in {"goal", "refutation", "local_lemma"}:
        action += f" ({_label(job['target'])})"
    if kind == "task":
        action += f" ({_label(job['kind'])})"
    state = job["state"]
    text = f"{action} {_JOB_PHASES.get(state, _label(state))}."
    if job["failure_code"]:
        text += f" {_label(job['failure_code'])}."
    elif job["finding"]:
        text += f" {_label(job['finding'])}."
    return _event(f"job:{job['id']}", kind, state, text,
                  job["submitted_at"] or job["cancelled_at"] or job["enqueued_at"],
                  node=_graph_node(job["workbench_node"], goal) or goal, goal=goal,
                  policy_id=_identifier(job["policy_id"]), job_id=job["id"],
                  statement_id=_identifier(job["statement_id"]), provider=job["model_provider"],
                  model=_text(job["model_id"]) or None, effort=_text(job.get("effort")) or None)


def _worker_event(policy, goal):
    return _event(f"worker:{policy['id']}", "worker", "running", "Lean advancement has an active worker.",
                  None, node=goal, goal=goal, policy_id=policy["id"], statement_id=policy["statement_id"])


def _target(step, goal, obligations):
    if lemma := _identifier(step.get("lemma_id")):
        return f"local-lemma:{lemma}"
    focus = step.get("focus")
    if not isinstance(focus, dict):
        focus = {}
    route = _identifier(step.get("reduction_id") or step.get("implication_id") or focus.get("implication_id"))
    if route:
        proposition = step.get("proposition") or focus.get("proposition")
        if isinstance(proposition, str) and (position := obligations.get((route, proposition))) is not None:
            return f"obligation:{route}:{position}"
        return f"local-lemma:{route}"
    return goal


def _pass_events(passage, goal, obligations):
    result = passage["result"]
    state = _recorded_state(result.get("status"))
    policy_id = passage["policy_id"]
    at = passage["recorded_at"]
    identifier = f"pass:{policy_id}:{at}"
    stage = _label(state)
    reason = _text(result.get("reason"))
    yield _event(identifier, "pass", state, f"Lean advancement recorded: {stage}." + (f" {reason}" if reason else ""),
                 at, node=_target(result, goal, obligations), goal=goal, policy_id=policy_id,
                 statement_id=passage["statement_id"])

    for index, step in enumerate(result.get("rounds") or ()):
        if not isinstance(step, dict):
            continue
        state = _recorded_state(step.get("status"))
        attempt = _identifier(step.get("model_attempt_id"))
        round_id = f"model:{attempt}" if attempt else f"{identifier}:round:{index}"
        where = _graph_node(step.get("workbench_focus_node_id"), goal) or _target(step, goal, obligations)
        provider = _text(step.get("model_provider")) or None
        model = _text(step.get("model_id") or step.get("model")) or None
        method = _text(step.get("method"))
        reason = _text(step.get("reason"))
        reply = _text(step.get("workbench_reply"))
        if attempt and reply:
            yield _event(f"{round_id}:workbench-reply", "agent_feedback", state, reply, at,
                         node=where, goal=goal, policy_id=policy_id, attempt_id=attempt, provider=provider, model=model)
        if attempt and state == "blocked" and reason:
            yield _event(f"{round_id}:reply", "agent_feedback", state, reason, at,
                         node=where, goal=goal, policy_id=policy_id, attempt_id=attempt, provider=provider, model=model)
        elif reason:
            yield _event(f"{round_id}:outcome", "pass", state, reason, at,
                         node=where, goal=goal, policy_id=policy_id, attempt_id=attempt)
        if attempt and method:
            yield _event(f"{round_id}:method", "agent_feedback", state, method, at,
                         node=where, goal=goal, policy_id=policy_id, attempt_id=attempt, provider=provider, model=model)
        if feedback := _text(step.get("feedback")):
            yield _event(f"{round_id}:feedback", "verifier_feedback", state, feedback, at,
                         node=where, goal=goal, policy_id=policy_id, attempt_id=attempt)

    for index, attempt in enumerate(result.get("route_attempts") or ()):
        if not isinstance(attempt, dict) or not (reason := _text(attempt.get("reason"))):
            continue
        state = _recorded_state(attempt.get("status"))
        attempt_id = _identifier(attempt.get("attempt_id"))
        event_id = f"route:{attempt_id}" if attempt_id else f"{identifier}:route:{index}"
        yield _event(event_id, "verifier_feedback", state, reason, at,
                     node=_target(attempt, goal, obligations), goal=goal, policy_id=policy_id, attempt_id=attempt_id)


def snapshot(store, claim_id):
    """Report only durable job state and recorded outcomes for this frozen claim."""
    rows = store.query("human", _SNAPSHOT, claim_id)
    if not rows:
        raise ClaimActivityNotFound("No frozen research claim matches this ID.")
    row, = rows
    claim = str(row["claim_id"])
    goal = f"goal:{claim}"
    obligations = {(str(item["implication_id"]), item["proposition"]): item["position"]
                   for item in row["obligations"]}
    activity = [_job_event(job, goal) for job in row["jobs"]]
    activity.extend(_worker_event(policy, goal) for policy in row["policies"] if policy["running"])
    for passage in row["passes"]:
        activity.extend(_pass_events(passage, goal, obligations))
    activity.sort(key=lambda event: (_LIVE_ORDER.get(event["state"], 0), event["at"] or ""), reverse=True)

    # A later pass may carry earlier rounds forward in its final history.
    unique = {}
    for event in activity:
        unique.setdefault(event["id"], event)
    return {"claim_id": claim, "activity": list(unique.values())[:_MAX_ACTIVITY]}
