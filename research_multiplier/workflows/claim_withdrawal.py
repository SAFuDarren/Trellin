"""Withdraw a frozen claim that cannot proceed, and restore it (#297).

A withdrawn claim keeps its contract, evidence and decision history. It leaves the project map and claim lists,
which show only current claims. When a corrected claim replaces it, the replacement is linked so history shows why.
"""

_WITHDRAWN = """
    SELECT c.id, c.title, c.status::text AS status, decision.actor_ref, decision.reason, decision.decided_at,
           replacement.source_id AS superseded_by
    FROM rm.claims c
    JOIN LATERAL (
        SELECT d.actor_ref, d.reason, d.decided_at FROM rm.decisions d
        WHERE d.claim_id = c.id AND d.action IN ('archive', 'supersede')
        ORDER BY d.sequence_no DESC LIMIT 1
    ) decision ON true
    LEFT JOIN LATERAL (
        SELECT e.source_id FROM rm.claim_edges e WHERE e.target_id = c.id AND e.kind = 'supersedes'
        ORDER BY e.source_id LIMIT 1
    ) replacement ON true
    WHERE c.kind = 'researcher_claim' AND c.status IN ('archived', 'superseded')
"""


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Give a {label}.")
    return value.strip()


def withdraw(store, claim_id, actor, reason, superseded_by=None):
    """Withdraw an open claim, optionally naming the frozen claim that replaces it."""
    store.query("human", "SELECT rm.withdraw_claim(%s,%s,%s,%s) AS id",
                claim_id, _text(actor, "actor"), _text(reason, "reason"), superseded_by)
    return status(store, claim_id)


def restore(store, claim_id, actor, reason):
    """Return a withdrawn claim to the open claims. Its replacement link stays in history."""
    store.query("human", "SELECT rm.restore_claim(%s,%s,%s) AS id",
                claim_id, _text(actor, "actor"), _text(reason, "reason"))
    return status(store, claim_id)


def status(store, claim_id):
    rows = store.query("human", "SELECT id, title, status::text AS status FROM rm.claims WHERE id = %s", claim_id)
    if not rows:
        raise ValueError("No claim matches this ID.")
    return rows[0]


def withdrawn(store):
    """List withdrawn claims with the decision that withdrew them, newest first."""
    return store.query("human", _WITHDRAWN + " ORDER BY decision.decided_at DESC, c.id")
