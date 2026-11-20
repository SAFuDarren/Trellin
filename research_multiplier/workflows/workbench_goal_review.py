"""Resume and select an already prepared conjecture in a local workbench."""

import re
from uuid import UUID

from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows import goal_suggestions


class GoalReviewError(ValueError):
    """A fixed workbench error key."""


_SHA256 = re.compile(r"[0-9a-f]{64}")
_FIELDS = {"goal_id", "frame_sha256", "candidate_id", "candidate_sha256", "reason"}


def _researcher(store):
    if not store.local_identity_installed():
        raise GoalReviewError("goalReview.unavailable")
    try:
        user = store.local_user()
    except DatabaseOperationError:
        raise
    except ValueError:
        raise GoalReviewError("goalReview.identity") from None
    if user is None:
        raise GoalReviewError("goalReview.identity")
    return user


def _frame(store, goal_id):
    rows = store.query("human", """
        SELECT id, body, frame_sha256, approved_at, basis_current, interpretation_hold, conjecture_id, claim_id
        FROM rm.research_goal_status WHERE id = %s
    """, goal_id)
    if not rows:
        raise GoalReviewError("goalReview.missing")
    frame = rows[0]
    if not frame["approved_at"] or frame["body"]["domain"] != "lean-core":
        raise GoalReviewError("goalReview.unavailable")
    return frame


def _basis(store, frame):
    rows = store.query("human", """
        SELECT version_id, version, version_sha256, body, needs_recheck
        FROM rm.research_goal_basis WHERE goal_id = %s ORDER BY position
    """, frame["id"])
    return [{
        "id": str(row["version_id"]), "version": row["version"], "sha256": row["version_sha256"],
        "kind": row["body"]["kind"], "wording": row["body"]["natural"], "formal": row["body"]["formal"],
        **({"source": row["body"]["source"]} if "source" in row["body"] else {}),
        "stale": row["needs_recheck"],
    } for row in rows]


def _discussion(store, frame, status):
    if status != "ready" or not store.query("human", """
        SELECT 1 FROM rm.research_open_discussions WHERE goal_id = %s
    """, frame["id"]):
        return {"state": "none", "alternatives": []}
    try:
        saved = goal_suggestions.latest_discussion(store, frame["id"])
        proposals = saved["proposals"]
        allowed = set(frame["body"]["basis_version_ids"])
        if (saved["frame_sha256"] != frame["frame_sha256"] or not 1 <= len(proposals) <= goal_suggestions.MAX_PROPOSALS
                or not all(goal_suggestions._discussion_candidate(item, allowed) for item in proposals)):
            return {"state": "unavailable", "alternatives": []}
    except DatabaseOperationError:
        raise
    except (KeyError, OSError, TypeError, ValueError):
        return {"state": "unavailable", "alternatives": []}
    return {"state": "available", "alternatives": [
        {"wording": item["statement"], "reason": item["reason"], "basis_ids": item["basis_version_ids"]}
        for item in proposals
    ]}


def _candidate(row):
    body = row["body"]
    return {
        "id": str(row["id"]), "sha256": row["conjecture_sha256"], "wording": body["statement"],
        "formal": body["formal"], "provenance_kind": body["provenance"]["kind"],
    }


def detail(store, goal_id):
    """Read recorded review material, with no retrieval or model dispatch."""
    _researcher(store)
    frame = _frame(store, goal_id)
    basis = _basis(store, frame)
    if frame["conjecture_id"] is not None:
        status = "selected"
    elif (not frame["basis_current"] or [row["id"] for row in basis] != frame["body"]["basis_version_ids"]):
        status = "stale"
    elif frame["interpretation_hold"]:
        status = "blocked"
    else:
        status = "ready"
    candidates = store.query("human", """
        SELECT id, conjecture_sha256, body FROM rm.research_conjectures
        WHERE goal_id = %s ORDER BY proposed_at, id
    """, goal_id)
    return {
        "goal_id": str(frame["id"]), **{name: frame["body"][name] for name in ("title", "domain", "objective", "sensitivity")},
        "frame_sha256": frame["frame_sha256"], "status": status,
        "claim_id": str(frame["claim_id"]) if frame["claim_id"] else None,
        "selected_candidate_id": str(frame["conjecture_id"]) if frame["conjecture_id"] else None,
        "basis": basis, "discussion": _discussion(store, frame, status),
        "candidates": [_candidate(row) for row in candidates],
    }


def _selection(document):
    if not isinstance(document, dict) or document.keys() != _FIELDS:
        raise GoalReviewError("goalReview.invalid")
    try:
        goal_id = UUID(document["goal_id"])
        candidate_id = UUID(document["candidate_id"])
    except (AttributeError, TypeError, ValueError):
        raise GoalReviewError("goalReview.invalid") from None
    frame_hash = document["frame_sha256"]
    candidate_hash = document["candidate_sha256"]
    reason = document["reason"]
    if (not all(isinstance(digest, str) and _SHA256.fullmatch(digest) for digest in (frame_hash, candidate_hash))
            or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000 or "\0" in reason):
        raise GoalReviewError("goalReview.invalid")
    try:
        reason.encode("utf-8")
    except UnicodeError:
        raise GoalReviewError("goalReview.invalid") from None
    return goal_id, candidate_id, frame_hash, candidate_hash, reason.strip()


def select(store, document):
    """Send the exact reviewed candidate through the existing kernel selection."""
    goal_id, candidate_id, frame_hash, candidate_hash, reason = _selection(document)
    actor = _researcher(store)
    frame = _frame(store, goal_id)
    if frame["frame_sha256"] != frame_hash:
        raise GoalReviewError("goalReview.stale")
    candidate = store.query("human", """
        SELECT 1 FROM rm.research_conjectures
        WHERE goal_id = %s AND id = %s AND conjecture_sha256 = %s
          AND jsonb_typeof(body #> '{formal,lean,proposition}') = 'string'
          AND nullif(btrim(body #>> '{formal,lean,proposition}'), '') IS NOT NULL
    """, goal_id, candidate_id, candidate_hash)
    if not candidate:
        raise GoalReviewError("goalReview.stale")
    if frame["conjecture_id"] is None and (not frame["basis_current"] or frame["interpretation_hold"]):
        raise GoalReviewError("goalReview.stale")
    # The kernel locks and rechecks the same basis on selection, then freezes
    # its complete ordered scope. Candidate provenance never sets the basis.
    try:
        result = store.query("human", "SELECT * FROM rm.select_research_conjecture(%s,%s,%s,%s)",
                             candidate_id, candidate_hash, actor["display_name"], reason)[0]
    except DatabaseOperationError as error:
        if error.state in {"23505", "23514", "P0002"}:
            raise GoalReviewError("goalReview.stale") from None
        raise
    return {"claim_id": str(result["claim_id"])}
