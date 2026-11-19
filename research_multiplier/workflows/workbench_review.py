"""Use the selected workspace's authenticated user for versioned reviews."""

from research_multiplier.storage.postgres import DatabaseOperationError


class ReviewError(ValueError):
    """A safe error code for the versioned evidence review operation."""


def capability(store):
    if store.local_identity_installed():
        return {"review_identity": {"workspace_id": str(store.workspace_id) if store.workspace_id else None}}
    return {}


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
    try:
        user = store.local_user()
    except DatabaseOperationError:
        raise
    except ValueError:
        raise ReviewError("review.identity_required") from None
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
