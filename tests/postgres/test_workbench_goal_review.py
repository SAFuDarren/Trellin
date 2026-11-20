# SPDX-License-Identifier: Apache-2.0
"""Exercise open-goal review against an authenticated disposable database."""

import json

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store
from tests.postgres.test_workbench_clarifications import _request, review_server  # noqa: F401


pytestmark = pytest.mark.postgres
ACTOR = "Fixture researcher"
REASON = "The reviewed formal wording fits the question."
TITLE = "Compare two basic consequences"
OBJECTIVE = "Decide which consequence to investigate under the approved basis."
SELECT = "/api/goal-review/select"


def _lean(proposition):
    return {"lean": {"proposition": proposition}}


def _basis(store):
    first = {"kind": "hypothesis", "natural": "The reference proposition holds.", "formal": _lean("True"),
             "source": {"kind": "human_entry", "title": "Synthetic fixture notes", "location": "Section 2"}}
    second = {"kind": "hypothesis", "natural": "Zero equals itself.", "formal": _lean("(0 : Nat) = 0")}
    versions = []
    for logical_key, body in (("fixture:first", first), ("fixture:second", second)):
        saved, = store.query("human", "SELECT * FROM rm.propose_formal_item(%s,%s)", logical_key, store.json(body))
        store.query("human", "SELECT rm.approve_formal_item(%s,%s,%s,%s)",
                    saved["id"], saved["version_sha256"], ACTOR, REASON)
        versions.append({"id": str(saved["id"]), "version": saved["version"], "sha256": saved["version_sha256"],
                         "kind": body["kind"], "wording": body["natural"], "formal": body["formal"],
                         **({"source": body["source"]} if "source" in body else {}), "stale": False})
    return list(reversed(versions))


def _goal(store, basis, *, title=TITLE):
    frame, = store.query("human", "SELECT * FROM rm.create_research_goal(%s)", store.json({
        "title": title, "domain": "lean-core", "objective": OBJECTIVE, "sensitivity": "restricted",
        "basis_version_ids": [entry["id"] for entry in basis],
    }))
    store.query("human", "SELECT rm.confirm_research_goal(%s,%s,%s,%s)",
                frame["id"], frame["frame_sha256"], ACTOR, REASON)
    return frame


def _candidate(store, goal, proposition, relevant):
    saved, = store.query("human", "SELECT * FROM rm.propose_research_conjecture(%s,%s)", goal["id"], store.json({
        "statement": proposition, "formal": _lean(proposition),
        "provenance": {"kind": "human", "basis_version_ids": relevant},
    }))
    return {"id": str(saved["id"]), "sha256": saved["conjecture_sha256"],
            "wording": proposition, "formal": _lean(proposition), "provenance_kind": "human"}


def _discussion(store, goal, basis):
    alternatives = [
        {"wording": "Investigate a conjunction.", "reason": "Both clauses describe the example.",
         "basis_ids": [basis[0]["id"]]},
        {"wording": "Investigate a single consequence.", "reason": "It isolates one consequence.",
         "basis_ids": [basis[1]["id"]]},
    ]
    proposals = [{"statement": item["wording"], "reason": item["reason"], "basis_version_ids": item["basis_ids"]}
                 for item in alternatives]
    objects = Artifacts(paths.object_store(), workspace=False)
    reference = objects.put({
        "goal_id": str(goal["id"]), "frame_sha256": goal["frame_sha256"], "proposals": proposals,
        "model": {"fixture": "restricted metadata"}, "transcript": objects.put({"fixture": "private transcript"}),
    })
    store.query("human", "SELECT rm.remember_goal_discussion(%s,%s,%s,%s)",
                goal["id"], goal["frame_sha256"], reference["sha256"], len(proposals))
    return alternatives


def _read(goal):
    return f"/api/goal-review?goal={goal['id']}"


def _decision(goal, candidate):
    return {"goal_id": str(goal["id"]), "frame_sha256": goal["frame_sha256"],
            "candidate_id": candidate["id"], "candidate_sha256": candidate["sha256"], "reason": REASON}


def _unselected(goal):
    with Store() as store:
        frame, = store.query("human", "SELECT claim_id, conjecture_id FROM rm.research_goal_status WHERE id = %s",
                             goal["id"])
        assert frame == {"claim_id": None, "conjecture_id": None}


def test_goal_review_reads_exact_basis_and_freezes_only_the_confirmed_conjecture(review_server):
    with Store() as store:
        basis = _basis(store)
        goal = _goal(store, basis)
        first = _candidate(store, goal, "True", [basis[1]["id"]])
        second = _candidate(store, goal, "True ∧ True", [basis[0]["id"]])
        alternatives = _discussion(store, goal, basis)
        other = _goal(store, basis, title="Different research question")
        foreign = _candidate(store, other, "(1 : Nat) = 1", [basis[0]["id"]])

    route = _read(goal)
    decision = _decision(goal, second)
    _request(review_server, route, status=403, session=False)
    _request(review_server, SELECT, document=decision, status=403, session=False)
    inbox = _request(review_server, "/api/inbox")
    assert any(item["kind"] == "choose_conjecture" and item["item_id"] == str(goal["id"]) for item in inbox["items"])

    ready = _request(review_server, route)
    assert {key: ready[key] for key in ("goal_id", "title", "objective", "domain", "sensitivity", "frame_sha256",
                                       "status", "claim_id", "selected_candidate_id")} == {
        "goal_id": str(goal["id"]), "title": TITLE, "objective": OBJECTIVE, "domain": "lean-core",
        "sensitivity": "restricted", "frame_sha256": goal["frame_sha256"],
        "status": "ready", "claim_id": None, "selected_candidate_id": None,
    }
    assert ready["basis"] == basis
    assert {item["id"]: item for item in ready["candidates"]} == {item["id"]: item for item in (first, second)}
    assert ready["discussion"] == {"state": "available", "alternatives": alternatives}
    _unselected(goal)

    _request(review_server, SELECT, document={**decision, "reason": " "}, status=400)
    for changes in (
        {"frame_sha256": "0" * 64},
        {"candidate_sha256": first["sha256"]},
        {"candidate_id": foreign["id"], "candidate_sha256": foreign["sha256"]},
        {"frame_sha256": second["sha256"], "candidate_sha256": goal["frame_sha256"]},
    ):
        _request(review_server, SELECT, document={**decision, **changes}, status=409)
    assert _request(review_server, route) == ready
    _unselected(goal)
    _unselected(other)

    selection = _request(review_server, SELECT, document=decision)
    claim_id = selection["claim_id"]
    assert _request(review_server, SELECT, document=decision) == selection
    _request(review_server, SELECT, document=_decision(goal, first), status=409)
    selected = _request(review_server, route)
    assert (selected["status"], selected["claim_id"], selected["selected_candidate_id"]) == (
        "selected", claim_id, second["id"],
    )
    assert selected["basis"] == basis
    inbox = _request(review_server, "/api/inbox")
    assert {item["item_id"] for item in inbox["items"] if item["kind"] == "choose_conjecture"} == {str(other["id"])}

    with Store() as store:
        contract = json.loads(store.frozen(claim_id)["contract_bytes"])
        assert contract["formal_skeleton"] == {
            "format": "rm-goal-v1", "goal_id": str(goal["id"]), "goal_sha256": goal["frame_sha256"],
            "candidate_id": second["id"], "candidate_sha256": second["sha256"],
            "basis_version_ids": [entry["id"] for entry in basis], "target": second["formal"],
        }
        assert [item["formal"] for item in contract["hypotheses"]] == [entry["formal"] for entry in basis]
        row, = store.query("human", "SELECT packet_id FROM rm.research_goal_status WHERE id = %s", goal["id"])
        versions = store.query("human", """
            SELECT item_version_id FROM rm.get_packet_basis(%s)
            WHERE packet_label IN ('H1', 'H2') ORDER BY packet_label
        """, row["packet_id"])
        assert [str(item["item_version_id"]) for item in versions] == [entry["id"] for entry in basis]


def test_goal_review_rejects_a_retired_basis_read_from_an_older_view(review_server):
    with Store() as store:
        basis = _basis(store)
        goal = _goal(store, basis)
        candidate = _candidate(store, goal, "True ∧ True", [basis[1]["id"]])
        # A recorded but missing archive remains available for recovery, without
        # being treated as an absent discussion or changing the prepared choices.
        store.query("human", "SELECT rm.remember_goal_discussion(%s,%s,%s,1)",
                    goal["id"], goal["frame_sha256"], "1" * 64)
    ready = _request(review_server, _read(goal))
    assert ready["status"] == "ready"
    assert ready["discussion"] == {"state": "unavailable", "alternatives": []}
    with Store() as store:
        store.query("human", "SELECT rm.retire_formal_item(%s,%s)", basis[0]["id"], "Withdraw the fixture premise.")
    stale = _request(review_server, _read(goal))
    assert stale["status"] == "stale" and stale["claim_id"] is None
    assert stale["basis"] == [{**basis[0], "stale": True}, basis[1]]
    _request(review_server, SELECT, document=_decision(goal, candidate), status=409)
    _unselected(goal)


def test_goal_review_resumes_after_a_real_interpretation_decision(review_server):
    with Store() as store:
        basis = _basis(store)
        goal = _goal(store, basis)
        candidate = _candidate(store, goal, "True ∧ True", [basis[0]["id"]])
        question, = store.query("human", "SELECT id FROM rm.request_research_condition(%s,%s)",
                                 goal["id"], store.json({
                                     "kind": "interpretation", "statement": basis[0]["wording"],
                                     "formal": basis[0]["formal"], "question": "Is this the intended premise?",
                                     "impact": "The choice depends on a confirmed interpretation.",
                                 }))

    blocked = _request(review_server, _read(goal))
    assert blocked["status"] == "blocked" and blocked["claim_id"] is None
    _request(review_server, SELECT, document=_decision(goal, candidate), status=409)
    _unselected(goal)

    with Store() as store:
        store.query("human", "SELECT rm.decide_research_condition(%s,%s,%s,%s,%s)",
                    question["id"], "reaffirm", ACTOR, REASON, basis[0]["id"])
    ready = _request(review_server, _read(goal))
    assert ready["status"] == "ready" and ready["claim_id"] is None
    assert _request(review_server, SELECT, document=_decision(goal, candidate))["claim_id"]
