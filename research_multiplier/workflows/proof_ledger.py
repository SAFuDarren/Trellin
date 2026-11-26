"""A claim's verified ledger: what earlier proof rounds left behind (#356).

Lean search already keeps a checked lemma when its round fails and records a route whose
closure failed or whose obligation was refuted. This module reads those records once, for both
the workbench and the next proof round, so the researcher and the model see the same summary.
Nothing here approves or changes a record (B2); it only arranges what is already verified.
"""

MAX_LEMMAS = 40
MAX_ROUTES = 16
MAX_PROPOSITION = 400
MAX_REASON = 500

# A route stays open while its obligations wait for proof; these statuses close it.
_TRIED = {"closure_rejected", "refuted_obligation", "needs_recheck"}


def route_failure(row):
    """One recorded route attempt as the proof round and the ledger read it."""
    failure = dict(row)
    if "proposition" in failure:
        failure["proposition"] = failure["proposition"][:MAX_PROPOSITION]
    return failure


def summarize(lemmas, routes, attempts=()):
    """Arrange recorded lemmas, routes and route attempts into the claim's ledger.

    `lemmas` are rows of `rm.local_lean_lemma_status`, `routes` rows of `rm.lean_reduction_status`
    and `attempts` the route attempts of the latest search run, newest last.
    """
    implications = {str(route["implication_id"]) for route in routes}
    used_by = {}
    for route in routes:
        for obligation in route.get("obligations") or ():
            if obligation.get("lemma_id"):
                used_by.setdefault(str(obligation["lemma_id"]), []).append(str(route["implication_id"]))

    checked = []
    for lemma in lemmas:
        identifier = str(lemma["id"])
        if identifier in implications or lemma.get("status") != "verified":
            continue
        checked.append({
            "lemma_id": identifier,
            "proposition": lemma["proposition"][:MAX_PROPOSITION],
            "verification_attempt_id": _text(lemma.get("verification_attempt_id")),
            "recorded_at": _text(lemma.get("recorded_at")),
            "used_by": used_by.get(identifier, []),
        })

    failures = {}
    for row in attempts or ():
        if isinstance(row, dict) and isinstance(row.get("implication_id"), str):
            failures[row["implication_id"]] = route_failure(row)

    tried = []
    for route in routes:
        identifier = str(route["implication_id"])
        failure = failures.pop(identifier, None)
        if route["status"] not in _TRIED and failure is None:
            continue
        obligations = route.get("obligations") or []
        open_items = [item for item in obligations if not item.get("lemma_id")]
        tried.append({
            "implication_id": identifier,
            "status": failure["status"] if failure and route["status"] not in _TRIED else route["status"],
            "obligations": [item["proposition"][:MAX_PROPOSITION] for item in obligations][:8],
            "open_obligation": open_items[0]["proposition"][:MAX_PROPOSITION] if open_items else None,
            "reason": (failure or {}).get("reason", "")[:MAX_REASON] or None,
            "attempt_id": _text((failure or {}).get("attempt_id")),
            "recorded_at": _text(route.get("recorded_at")),
        })
    # A refuted route the reduction view no longer lists still closed a direction.
    for identifier, failure in failures.items():
        tried.append({
            "implication_id": identifier, "status": failure["status"], "obligations": [],
            "open_obligation": failure.get("proposition"), "reason": failure.get("reason", "")[:MAX_REASON] or None,
            "attempt_id": _text(failure.get("attempt_id")), "recorded_at": None,
        })

    checked.sort(key=lambda item: (item["recorded_at"] or "", item["lemma_id"]), reverse=True)
    tried.sort(key=lambda item: (item["recorded_at"] or "", item["implication_id"]), reverse=True)
    return {"lemmas": checked[:MAX_LEMMAS], "tried_routes": tried[:MAX_ROUTES],
            "counts": {"lemmas": len(checked), "tried_routes": len(tried)}}


def _text(value):
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
