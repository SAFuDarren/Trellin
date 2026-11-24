"""Use the selected workspace's authenticated user for versioned reviews."""

from research_multiplier.storage.postgres import DatabaseOperationError


class ReviewError(ValueError):
    """A safe error code for the versioned evidence review operation."""


def capability(store):
    """Tell the workbench who signs decisions, so it never asks the bound researcher for a name (#328)."""
    if not store.local_identity_installed():
        return {}
    identity = {"workspace_id": str(store.workspace_id) if store.workspace_id else None}
    try:
        user = store.local_user()
    except ValueError:
        user = None
    if user is not None and user.get("display_name"):
        identity["display_name"] = user["display_name"]
    return {"review_identity": identity}


def _local_user(store):
    try:
        return store.local_user()
    except DatabaseOperationError:
        raise
    except ValueError:
        raise ReviewError("review.identity_required") from None


def legacy(store, evidence_id, action, actor, reason):
    """Preserve the old review response and support databases predating identity."""
    user = _local_user(store)
    if user is not None:
        actor = user["display_name"]
    elif store.local_identity_installed():
        raise ReviewError("review.identity_required")
    elif actor is None:
        raise ReviewError("review.legacy_name_required")
    return {"id": store.review(evidence_id, action, actor, reason)}


def authenticated(store, workspace_id, evidence_id, document, idempotency_key):
    if workspace_id != store.workspace_id:
        raise ReviewError("review.not_found")
    if not isinstance(document, dict) or document.keys() != {"decision", "reason"}:
        raise ReviewError("request.invalid")
    decision, reason = document["decision"], document["reason"]
    if (decision not in ("approve", "return") or not isinstance(reason, str)
            or not reason.strip() or len(reason) > 2000 or "\x00" in reason):
        raise ReviewError("request.invalid")
    try:
        reason.encode("utf-8")
    except UnicodeError:
        raise ReviewError("request.invalid") from None
    user = _local_user(store)
    if user is None:
        raise ReviewError("review.identity_required")
    try:
        rows = store.query("human", "SELECT rm.review_evidence_as(%s,%s,%s,%s,%s) AS id",
                           user["id"], evidence_id, decision, reason, idempotency_key)
    except DatabaseOperationError as error:
        code = {"22023": "request.invalid", "23505": "idempotency.conflict", "P0002": "review.not_found",
                "23514": "review.not_pending", "42501": "review.identity_required"}.get(error.state)
        if code:
            raise ReviewError(code) from None
        raise
    return {"id": rows[0]["id"], "user_id": user["id"]}
