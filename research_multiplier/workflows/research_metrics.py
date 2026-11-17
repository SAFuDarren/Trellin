"""Measure a committed Lean policy and its frozen goal's model activity."""

from decimal import Decimal

from research_multiplier.storage.postgres import Store


_SNAPSHOT = """
WITH scope AS (
    SELECT p.id, p.claim_id, p.contract_sha256, p.state,
           (p.policy #>> '{lean,statement_id}')::uuid AS statement_id
    FROM rm.research_progress_status p WHERE p.id = %s AND p.policy ? 'lean'
)
SELECT to_jsonb(scope) AS policy, to_jsonb(passes) AS passes,
       coalesce(model.items, '[]'::jsonb) AS calls,
       coalesce(inbox.items, '[]'::jsonb) AS pending,
       coalesce(researcher.items, '[]'::jsonb) AS human_time
FROM scope
CROSS JOIN LATERAL rm.lean_inquiry_pass_metrics(scope.id) passes
CROSS JOIN LATERAL (
    SELECT jsonb_agg(to_jsonb(call) ORDER BY call.reserved_at NULLS FIRST, call.job_id, call.lease_generation) AS items
    FROM (
        SELECT h.job_id, h.lease_generation, h.attempt_id, h.reserved_at, h.status,
               h.model_provider, h.model_id, h.input_tokens, h.output_tokens, h.cost_usd::text AS cost_usd,
               h.execution_ms,
               h.failure_code,
               CASE WHEN jsonb_typeof(a.model_parameters -> 'cached_input_tokens') = 'number'
                    THEN (a.model_parameters ->> 'cached_input_tokens')::numeric END AS cached_input_tokens
        FROM rm.lean_search_call_history h LEFT JOIN rm.attempts a ON a.id = h.attempt_id
        WHERE (h.claim_id, h.contract_sha256) = (scope.claim_id, scope.contract_sha256)
    ) call
) model
CROSS JOIN LATERAL (
    SELECT jsonb_agg(to_jsonb(item) ORDER BY item.kind, item.item_id) AS items
    FROM (
        SELECT DISTINCT q.kind, q.item_id FROM rm.research_actionable_review_inbox q WHERE q.claim_id = scope.claim_id
    ) item
) inbox
CROSS JOIN LATERAL (
    SELECT jsonb_agg(to_jsonb(t) ORDER BY t.activity) AS items
    FROM (
        SELECT activity, count(*) AS entries, sum(minutes) AS minutes
        FROM rm.human_time_entries WHERE claim_id = scope.claim_id GROUP BY activity
    ) t
) researcher
"""


def _coverage(rows, key, *, convert=int):
    values = [convert(row[key]) for row in rows if row[key] is not None]
    total = sum(values) if values else None
    if isinstance(total, Decimal):
        total = str(total)
    return {"reported_total": total, "reported_calls": len(values), "unknown_calls": len(rows) - len(values)}


def inquiry(policy_id, *, details=False):
    with Store() as store:
        records = store.query("human", _SNAPSHOT, policy_id)
    if not records:
        raise ValueError("Select an existing Lean advancement policy.")
    [record] = records
    calls = record["calls"]
    model = {
        "scope": "frozen_goal_across_policies",
        "reservation_count": len(calls),
        "statuses": {status: sum(row["status"] == status for row in calls)
                     for status in ("responded", "failed", "leased", "no_report")},
        **{name: _coverage(calls, name) for name in ("input_tokens", "cached_input_tokens", "output_tokens",
                                                   "execution_ms")},
        "cost_usd": _coverage(calls, "cost_usd", convert=Decimal),
    }
    if details:
        model["calls"] = calls
    return {"policy": record["policy"], "passes": record["passes"], "model": model,
            "pending_human_decisions": {"count": len(record["pending"]), "items": record["pending"]},
            "logged_human_time": record["human_time"]}
