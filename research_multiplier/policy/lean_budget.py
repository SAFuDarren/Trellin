"""Read and enforce the public Lean search budget for a frozen claim."""

from decimal import Decimal

from research_multiplier.storage.postgres import Store


class Exhausted(ValueError):
    """A new proposal would exceed the frozen goal's call allowance."""


def claim(store, owner, seconds, job_id, *, retry):
    rows = store.query("scheduler", "SELECT * FROM rm.claim_budgeted_lean_search(%s,%s,%s,%s)",
                       owner, seconds, job_id, retry)
    if rows and rows[0]["budget_exhausted"]:
        raise Exhausted("The frozen goal reached its Lean search call limit. See 'lean budget'.")
    return rows


def inspect(statement_id, *, limit=None, actor=None, reason=None, history=False):
    set_limit = limit is not None
    if (set_limit and (not actor or not reason)) or (not set_limit and (actor is not None or reason is not None)):
        raise ValueError("Provide --limit, --actor and --reason together to change a search budget.")
    with Store() as store:
        if set_limit:
            store.query("human", "SELECT rm.set_lean_search_call_limit(%s,%s,%s,%s)",
                        statement_id, limit, actor, reason)
        rows = store.query("human", """
            SELECT b.*, h.calls, p.changes FROM rm.lean_search_budget_status b
            CROSS JOIN LATERAL (
                SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.reserved_at NULLS FIRST, t.job_id, t.lease_generation),
                                '[]'::jsonb) AS calls
                FROM rm.lean_search_call_history t
                WHERE (t.claim_id, t.contract_sha256) = (b.claim_id, b.contract_sha256)
            ) h
            CROSS JOIN LATERAL (
                SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.sequence_no), '[]'::jsonb) AS changes
                FROM rm.lean_search_budget_changes d
                WHERE %s AND (d.claim_id, d.contract_sha256) = (b.claim_id, b.contract_sha256)
            ) p
            WHERE b.statement_id = %s
        """, history, statement_id)
    if not rows:
        raise ValueError("A confirmed public Lean statement is required.")
    report = rows[0]
    calls, changes = report.pop("calls"), report.pop("changes")
    report["outcomes"] = {status: sum(call["status"] == status for call in calls)
                          for status in ("responded", "failed", "leased", "no_report")}
    for field in ("input_tokens", "output_tokens", "cost_usd"):
        known = [call[field] for call in calls if call[field] is not None]
        total = None
        if known:
            total = str(sum(map(Decimal, known))) if field == "cost_usd" else sum(known)
        report[field] = {"reported_total": total,
                         "reported_slots": len(known), "unknown_slots": len(calls) - len(known)}
    if history:
        report.update(calls=calls, policy_changes=changes)
    return report
