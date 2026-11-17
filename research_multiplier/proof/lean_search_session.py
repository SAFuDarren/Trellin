"""Validate the small per-policy search checkpoint and choose the next method."""

from decimal import Decimal
from uuid import UUID

from .lean_working_reuse import match_key as obligation_key


FORMAT = 1
_HISTORY_FIELDS = (
    "action", "status", "feedback", "reason", "lemma_id", "reduction_id", "obligations", "focus", "method",
    "obligation_progress",
)
MAX_HISTORY_TEXT = 600
MAX_ROUTE_ATTEMPTS = 32
_STALLED = {"blocked", "rejected", "proof_search_blocked", "duplicate_candidate"}
_RECOVERABLE_BUDGET = {"budget_blocked"}


def restore(artifacts, reference, statement, rounds, *, include_routes=False):
    if reference is None:
        return ([], []) if include_routes else []
    checkpoint = artifacts.read_json(reference)
    history = checkpoint.get("rounds") if isinstance(checkpoint, dict) else None
    if (not isinstance(history, list) or len(history) > rounds
            or checkpoint.get("statement_id") != str(statement["id"])
            or checkpoint.get("sustained") != marker(statement)):
        raise ValueError("The search checkpoint does not match this confirmed Lean statement and budget.")
    attempts = set()
    for index, step in enumerate(history, 1):
        if (not isinstance(step, dict) or step.get("round") != index
                or not isinstance(step.get("status"), str)):
            raise ValueError("The search checkpoint has invalid round history.")
        try:
            attempt = UUID(str(step["model_attempt_id"]))
        except (KeyError, ValueError):
            raise ValueError("The search checkpoint has an invalid model attempt.") from None
        if attempt in attempts:
            raise ValueError("The search checkpoint repeats a model attempt.")
        attempts.add(attempt)
        if "route_checkpoint" in step:
            progress = step["route_checkpoint"]
            if (not isinstance(progress, dict) or set(progress) != {"verified", "pending", "stalled"}
                    or any(type(value) is not int or not 0 <= value <= 8 for value in progress.values())):
                raise ValueError("The search checkpoint has invalid route progress.")
    while history and history[-1]["status"] in _RECOVERABLE_BUDGET:
        history = history[:-1]
    if not include_routes:
        return history
    routes = checkpoint.get("route_attempts", [])
    if (not isinstance(routes, list) or len(routes) > MAX_ROUTE_ATTEMPTS
            or any(not isinstance(row, dict) or not isinstance(row.get("implication_id"), str)
                   or row.get("status") not in {"closure_rejected", "route_refuted"}
                   or (row["status"] == "closure_rejected" and (
                       not isinstance(row.get("obligation_lemma_ids"), list)
                       or len(row["obligation_lemma_ids"]) > 8
                       or any(not isinstance(value, str) for value in row["obligation_lemma_ids"])))
                   for row in routes)):
        raise ValueError("The search checkpoint has invalid route history.")
    return history, routes


def marker(statement):
    return {"version": FORMAT, "statement_sha256": statement["statement_sha256"]}


def model_history(history, sustained):
    fields = _HISTORY_FIELDS if sustained else ("action", "status", "feedback")
    rows = [{key: step[key] for key in fields if key in step} for step in history]
    if sustained:
        for row in rows:
            for key in ("feedback", "reason"):
                if key in row:
                    row[key] = row[key][:MAX_HISTORY_TEXT]
            if "obligations" in row:
                row["obligations"] = [
                    item[:MAX_HISTORY_TEXT] if isinstance(item, str)
                    else item.get("proposition", "")[:MAX_HISTORY_TEXT]
                    for item in row["obligations"][:4]
                ]
            if "focus" in row and "proposition" in row["focus"]:
                row["focus"] = {**row["focus"], "proposition": row["focus"]["proposition"][:MAX_HISTORY_TEXT]}
    return rows


def _stalled(step):
    return (step.get("status") in _STALLED | {"proposal_failed"}
            or step.get("obligation_progress") == "unchanged")


def obligation_progress(before, after):
    """Measure a verified obligation or a verified rejection of an open route."""
    def verified(routes):
        return {obligation_key(item["proposition"]) for route in routes for item in route["obligations"]
                if item["lemma_id"]}

    open_before = {route["implication_id"] for route in before if route["status"] == "awaiting_obligations"}
    refuted = any(route["implication_id"] in open_before and route["status"] == "refuted_obligation" for route in after)
    return bool(refuted or verified(after) - verified(before))


def _obligation_failures(history):
    attempts = {}
    for step in history:
        if not _stalled(step):
            continue
        focus = step.get("focus", {})
        if focus.get("method") == "key_lemma" and focus.get("proposition"):
            key = obligation_key(focus["proposition"])
            attempts[key] = attempts.get(key, 0) + 1
    # The same proposition under this frozen scope is a single obligation,
    # even when more than one independently checked route needs it.
    return attempts


def _comparison(routes, attempts, usage):
    comparison = []
    for route in routes:
        if route["status"] not in {"awaiting_obligations", "ready", "goal_checked", "refuted_obligation",
                                  "closure_rejected"}:
            continue
        pending = [obligation_key(item["proposition"]) for item in route["obligations"] if not item["lemma_id"]]
        total = len(route["obligations"])
        comparison.append({
            "implication_id": route["implication_id"], "status": route["status"],
            "verified_obligations": total - len(pending), "total_obligations": total,
            "pending_obligations": len(pending),
            "untried_obligations": sum(not attempts.get(proposition) for proposition in pending),
            "stalled_attempts": sum(attempts.get(proposition, 0) for proposition in pending),
            "refuted_obligations": len(route.get("refuted_obligations", ())),
            **usage.get(route["implication_id"], {}),
        })
    comparison.sort(key=_route_rank)
    return comparison


def compare_routes(history, routes):
    """Order eligible reductions by checked work, unresolved failures and recorded effort."""
    usage = {}
    for step in history:
        identifier = (step.get("focus") or {}).get("implication_id") or step.get("reduction_id")
        if identifier is None:
            continue
        counts = usage.setdefault(identifier, {"model_rounds": 0})
        counts["model_rounds"] += 1
        for key, value in step.get("usage", {}).items():
            if key == "cost_usd":
                counts[key] = str(Decimal(counts.get(key, "0")) + Decimal(value))
            elif type(value) is int:
                counts[key] = counts.get(key, 0) + value
    return _comparison(routes, _obligation_failures(history), usage)


def _route_rank(route):
    return ({"goal_checked": 0, "ready": 1, "awaiting_obligations": 2,
             "refuted_obligation": 3, "closure_rejected": 4}[route["status"]],
            not route["untried_obligations"], route["pending_obligations"], -route["verified_obligations"],
            route["stalled_attempts"], route.get("model_rounds", 0))


def ordered_routes(history, routes):
    ranks = {item["implication_id"]: rank for rank, item in enumerate(compare_routes(history, routes))}
    return sorted(routes, key=lambda route: ranks.get(route["implication_id"], len(ranks)))


def focus(history, routes):
    attempts = _obligation_failures(history)
    ordered = ordered_routes(history, routes)
    pending = [
        (route["implication_id"], item["proposition"], obligation_key(item["proposition"]))
        for route in ordered if route["status"] == "awaiting_obligations"
        for item in route["obligations"] if not item["lemma_id"]
    ]
    fresh = next(((identifier, proposition) for identifier, proposition, key in pending if key not in attempts),
                 None)
    if fresh is not None:
        identifier, proposition = fresh
        return {"method": "key_lemma", "implication_id": identifier, "proposition": proposition}
    last = history[-1] if history else {}
    old_method = (last.get("focus") or {}).get("method")
    if _stalled(last):
        tried_refutation = any((step.get("focus") or {}).get("method") == "refutation" for step in history)
        if old_method == "alternative_route" and not tried_refutation:
            return {"method": "refutation"}
        if pending and (old_method == "refutation" or old_method == "alternative_route" and tried_refutation):
            identifier, proposition, _ = min(pending, key=lambda entry: attempts.get(entry[2], 0))
            return {"method": "key_lemma", "implication_id": identifier, "proposition": proposition}
        return {"method": "alternative_route"}
    if pending or any(route["status"] in {"refuted_obligation", "closure_rejected"} for route in routes):
        return {"method": "alternative_route"}
    return {"method": "advance_goal"}


def classic_focus(history, routes):
    last = history[-1] if history else {}
    failed_routes = {
        step["focus"]["implication_id"]
        for step in history
        if step["status"] in _STALLED and step.get("focus", {}).get("implication_id")
    }
    pending = [(route, item) for route in routes if route["status"] == "awaiting_obligations"
               for item in route["obligations"] if not item["lemma_id"]]
    available = [(route, item) for route, item in pending if route["implication_id"] not in failed_routes]
    if last.get("status") in _STALLED:
        previous = last.get("focus", {}).get("method")
        if (previous == "alternative_route"
                and not any(step.get("focus", {}).get("method") == "refutation" for step in history)):
            return {"method": "refutation"}
        return {"method": "alternative_route"}
    if available:
        route, item = min(available, key=lambda pair: sum(not item["lemma_id"] for item in pair[0]["obligations"]))
        return {"method": "key_lemma", "implication_id": route["implication_id"],
                "proposition": item["proposition"]}
    if pending:
        return {"method": "alternative_route"}
    return {"method": "advance_goal"}


def lane_history(history, identifier):
    """Expose only the route's own prior proposals and feedback to its author."""
    return [step for step in history if (step.get("focus") or {}).get("implication_id") == identifier]


def _lane_stalls(history, route):
    lane = lane_history(history, route["implication_id"])
    checkpoint = next((step["route_checkpoint"] for step in reversed(lane) if "route_checkpoint" in step), None)
    verified = sum(bool(item["lemma_id"]) for item in route["obligations"])
    return checkpoint["stalled"] if checkpoint and verified <= checkpoint["verified"] else 0


def lane_checkpoint(history, before, after):
    """Measure useful progress in this route after one independently drafted proposal."""
    previous = sum(bool(item["lemma_id"]) for item in before["obligations"])
    verified = sum(bool(item["lemma_id"]) for item in after["obligations"])
    progressed = verified > previous or after["status"] in {"refuted_obligation", "goal_checked"}
    return {"verified": verified, "pending": len(after["obligations"]) - verified,
            "stalled": 0 if progressed else _lane_stalls(history, before) + 1}


def lane_states(history, routes, stall_limit):
    states = compare_routes(history, routes)
    live = {row["implication_id"]: row for row in routes}
    for row in states:
        stalled = _lane_stalls(history, live[row["implication_id"]])
        row["stalled_checkpoints"] = stalled
        row["paused"] = row["status"] == "awaiting_obligations" and stalled >= stall_limit
    return states


def parallel_foci(history, routes, width, stall_limit):
    """Assign one distinct live obligation per route in the next bounded pass."""
    selected = []
    obligations = set()
    live = {row["implication_id"]: row for row in routes}
    for row in lane_states(history, routes, stall_limit):
        if row["status"] != "awaiting_obligations" or row["paused"]:
            continue
        identifier = row["implication_id"]
        tried = _obligation_failures(lane_history(history, identifier))
        choices = [(key, item["proposition"]) for item in live[identifier]["obligations"]
                   if not item["lemma_id"] and (key := obligation_key(item["proposition"])) not in obligations]
        if choices:
            key, proposition = min(choices, key=lambda entry: tried.get(entry[0], 0))
            obligations.add(key)
            selected.append({"method": "key_lemma", "implication_id": identifier, "proposition": proposition})
            if len(selected) == width:
                break
    return selected
