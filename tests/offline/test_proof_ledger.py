from types import SimpleNamespace

import pytest

from research_multiplier.workflows import lean_search, proof_ledger


pytestmark = pytest.mark.offline

_STATEMENT = "01234567-89ab-cdef-0123-456789abcdef"
_ROUTE = "01234567-89ab-cdef-0123-456789abcd01"
_OPEN_ROUTE = "01234567-89ab-cdef-0123-456789abcd02"
_LEMMA = "01234567-89ab-cdef-0123-456789abcd03"
_UNUSED = "01234567-89ab-cdef-0123-456789abcd04"
_ATTEMPT = "01234567-89ab-cdef-0123-456789abcd05"
_REFUTED = "01234567-89ab-cdef-0123-456789abcd06"

# A first round checked two lemmas and a route whose closure Lean then rejected.
_LEMMAS = [
    {"id": _ROUTE, "proposition": "(P ∧ Q) → Goal", "status": "verified", "recorded_at": "2026-11-26T10:00:00",
     "verification_attempt_id": _ATTEMPT},
    {"id": _LEMMA, "proposition": "P", "status": "verified", "recorded_at": "2026-11-26T10:01:00",
     "verification_attempt_id": _ATTEMPT},
    {"id": _UNUSED, "proposition": "R", "status": "verified", "recorded_at": "2026-11-26T10:02:00",
     "verification_attempt_id": _ATTEMPT},
    {"id": "01234567-89ab-cdef-0123-456789abcd07", "proposition": "Stale", "status": "needs_recheck",
     "recorded_at": "2026-11-26T09:00:00", "verification_attempt_id": _ATTEMPT},
]
_ROUTES = [
    {"implication_id": _ROUTE, "status": "closure_rejected", "recorded_at": "2026-11-26T10:00:00",
     "obligations": [{"proposition": "P", "lemma_id": _LEMMA}, {"proposition": "Q", "lemma_id": None}]},
    {"implication_id": _OPEN_ROUTE, "status": "awaiting_obligations", "recorded_at": "2026-11-26T10:03:00",
     "obligations": [{"proposition": "S", "lemma_id": None}]},
]
_ATTEMPTS = [
    {"implication_id": _ROUTE, "obligation_lemma_ids": [_LEMMA], "status": "closure_rejected",
     "reason": "Lean rejected the composed proof: type mismatch", "attempt_id": _ATTEMPT},
    {"implication_id": _REFUTED, "status": "route_refuted", "proposition": "T" * 900, "lemma_id": _UNUSED,
     "attempt_id": _ATTEMPT},
]


def test_ledger_lists_checked_lemmas_and_the_routes_already_tried():
    ledger = proof_ledger.summarize(_LEMMAS, _ROUTES, _ATTEMPTS)
    lemmas = {item["lemma_id"]: item for item in ledger["lemmas"]}
    # Route implications and stale lemmas are not ledger lemmas.
    assert set(lemmas) == {_LEMMA, _UNUSED}
    assert lemmas[_LEMMA]["used_by"] == [_ROUTE] and lemmas[_UNUSED]["used_by"] == []
    assert lemmas[_LEMMA]["verification_attempt_id"] == _ATTEMPT
    tried = {item["implication_id"]: item for item in ledger["tried_routes"]}
    assert set(tried) == {_ROUTE, _REFUTED}
    assert tried[_ROUTE]["status"] == "closure_rejected"
    assert tried[_ROUTE]["reason"] == "Lean rejected the composed proof: type mismatch"
    assert tried[_ROUTE]["open_obligation"] == "Q"
    assert tried[_REFUTED]["status"] == "route_refuted"
    assert len(tried[_REFUTED]["open_obligation"]) == proof_ledger.MAX_PROPOSITION
    assert ledger["counts"] == {"lemmas": 2, "tried_routes": 2}


def test_next_round_reads_the_same_lemmas_and_route_failures():
    known = {item["id"]: item for item in _LEMMAS}
    store = SimpleNamespace(query=lambda *args: [])
    context = {"antecedents": [], "statement": "Goal"}
    request = lean_search._request(
        store, None, {"claim_id": _STATEMENT}, "key", context, 2, [], known, [], _ROUTES,
        focus={"method": "alternative_route"}, ranked=True, route_attempts=_ATTEMPTS,
    )
    ledger = proof_ledger.summarize(_LEMMAS, _ROUTES, _ATTEMPTS)
    offered = {item["id"] for item in request["checked_local_lemmas"]}
    assert {item["lemma_id"] for item in ledger["lemmas"]} <= offered
    # The round and the ledger read route failures through one function.
    assert request["route_failures"] == [proof_ledger.route_failure(row) for row in reversed(_ATTEMPTS)]
    reasons = {row["implication_id"]: row.get("reason") for row in request["route_failures"]}
    assert reasons[_ROUTE] == next(item["reason"] for item in ledger["tried_routes"]
                                   if item["implication_id"] == _ROUTE)


def test_empty_records_make_an_empty_ledger():
    assert proof_ledger.summarize([], [], None) == {
        "lemmas": [], "tried_routes": [], "counts": {"lemmas": 0, "tried_routes": 0}}
