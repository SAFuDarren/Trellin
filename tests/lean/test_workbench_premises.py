# SPDX-License-Identifier: Apache-2.0
"""Exercise claim-scoped premise review through the authenticated workbench."""

from contextlib import contextmanager
import json
import os
from http.client import HTTPConnection
from threading import Thread
from uuid import uuid4

import pytest

from research_multiplier import general_lean
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import lean_verification, lean_workflow
from tests._native_db import disposable_project_logins


pytestmark = pytest.mark.lean
_PROPOSE = "/api/premises/propose"
_DECIDE = "/api/premises/decide"
_TARGET = "True ∧ True"
_QUESTION = "Would this premise change the intended statement?"
_IMPACT = "Review the premise before relying on it."
_ACTOR = "Fixture"
_REASON = "Reviewed this exact synthetic statement."


@contextmanager
def _workbench(workspace):
    with WorkbenchServer(workspace, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def _request(server, route, document=None, *, authentic=True):
    headers = {"X-RM-Session": server.session} if authentic else {}
    if document is not None:
        headers.update({"Origin": server.origin, "Content-Type": "application/json"})
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=90)
    try:
        connection.request("GET" if document is None else "POST", route, headers=headers,
                           body=None if document is None else json.dumps(document).encode())
        reply = connection.getresponse()
        return reply.status, json.loads(reply.read())
    finally:
        connection.close()


def _add_claim(server, title, proposition):
    status, result = _request(server, "/api/theorems", {
        "kind": "goal", "title": title, "wording": proposition, "lean": proposition,
        "confirmed": True, "actor": _ACTOR, "reason": _REASON,
    })
    assert status == 200, result
    return result


def _check_original(workspace, claim):
    with paths.use_workspace(workspace, strict=True):
        statement = general_lean.propose(claim["packet_id"])
        lean_workflow.confirm(statement["id"], statement["version"], statement["statement_sha256"], _ACTOR, _REASON)
        check = lean_verification.check_goal(
            statement["id"], {"format_version": 1, "proof": {"and": [{"trivial": True}, {"trivial": True}]}},
            paths.runs() / "lean",
        )
        with Store() as store:
            frozen = store.frozen(claim["claim_id"])
    return {**frozen, "statement_id": str(statement["id"]), "evidence_id": str(check["evidence_id"])}


def _graph(server, claim_id):
    status, snapshot = _request(server, f"/api/graph?claim={claim_id}")
    assert status == 200, snapshot
    assert snapshot["claim"]["id"] == claim_id
    return snapshot


def _pending(snapshot):
    return {
        row["id"]: row for row in snapshot["claim"]["premise_review"]["proposals"]
        if row["state"] == "pending"
    }


def _assert_original_graph(snapshot, original):
    nodes = {node["id"]: node for node in snapshot["nodes"]}
    for old in original["nodes"]:
        new = nodes[old["id"]]
        assert new.get("reference") == old.get("reference")
        assert new.get("formalization") == old.get("formalization")
    assert snapshot["claim"]["statement_id"] == original["claim"]["statement_id"]
    assert snapshot["edges"] == original["edges"]


def _proposal(claim_id, frame, wording, proposition):
    return {
        "claim_id": claim_id, "frame_sha256": frame, "wording": wording, "lean": proposition,
        "question": _QUESTION, "impact": _IMPACT,
    }


def _decide_payload(claim_id, frame, proposal, decision):
    return {
        "claim_id": claim_id, "frame_sha256": frame, "id": proposal["id"], "sha256": proposal["sha256"],
        "decision": decision, "reason": _REASON,
    }


def _propose_and_review(server, original, other_claim):
    claim_id = original["claim"]["id"]
    review = original["claim"]["premise_review"]
    frame = review["frame_sha256"]
    assert review["available"]
    assert review["target"] == {"wording": _TARGET, "lean": _TARGET}
    assert not review["basis"] and not review["proposals"]

    first = _proposal(claim_id, frame, "One equals itself.", "1 = 1")
    second = _proposal(claim_id, frame, "Two equals itself.", "2 = 2")
    assert _request(server, _PROPOSE, first, authentic=False)[0] == 403
    assert _request(server, _PROPOSE, {**first, "wording": " "})[0] == 400
    for invalid in (
        {**first, "claim_id": str(uuid4())},
        {**first, "claim_id": other_claim},
        {**first, "frame_sha256": "0" * 64},
        {**first, "lean": _TARGET},
    ):
        assert _request(server, _PROPOSE, invalid)[0] == 409
    status, one = _request(server, _PROPOSE, first)
    assert status == 200, one
    assert _request(server, _PROPOSE, first) == (200, one)
    status, two = _request(server, _PROPOSE, second)
    assert status == 200, two

    held = _graph(server, claim_id)
    _assert_original_graph(held, original)
    assert held["claim"]["status"] == "awaiting_interpretation_review"
    assert len([node for node in held["nodes"] if node["kind"] == "proposed_premise"]) == 2
    proposals = _pending(held)
    assert set(proposals) == {one["id"], two["id"]}
    assert proposals[one["id"]]["wording"] == first["wording"]
    assert proposals[two["id"]]["lean"] == second["lean"]
    assert not _pending(_graph(server, other_claim))

    reject = _decide_payload(claim_id, frame, proposals[one["id"]], "reject")
    revise = _decide_payload(claim_id, frame, proposals[two["id"]], "revise")
    assert _request(server, _DECIDE, {**reject, "sha256": "0" * 64})[0] == 409
    assert _request(server, _DECIDE, {**reject, "frame_sha256": "0" * 64})[0] == 409
    assert _request(server, _DECIDE, revise)[0] == 409
    assert _request(server, _DECIDE, reject) == (200, one)
    assert _request(server, _DECIDE, reject) == (200, one)
    assert _request(server, _DECIDE, {**reject, "decision": "revise"})[0] == 409

    remaining = _graph(server, claim_id)
    _assert_original_graph(remaining, original)
    assert remaining["claim"]["status"] == "awaiting_interpretation_review"
    assert set(_pending(remaining)) == {two["id"]}
    assert not remaining["claim"]["premise_review"]["basis"]

    status, reviewed = _request(server, _DECIDE, revise)
    assert status == 200, reviewed
    assert reviewed["id"] == two["id"]
    assert _request(server, _DECIDE, revise) == (200, reviewed)
    return reviewed["revised_claim_id"], second


def _assert_recorded_revision(workspace, claim_id, revised_id, before, premise):
    with paths.use_workspace(workspace, strict=True), Store() as store:
        old = store.frozen(claim_id)
        new = store.frozen(revised_id)
        assert (old["contract_sha256"], old["contract_bytes"]) == (before["contract_sha256"], before["contract_bytes"])
        claim = json.loads(new["contract_bytes"])
        assert claim["formal_skeleton"]["target"]["lean"]["proposition"] == _TARGET
        version_id, = claim["formal_skeleton"]["basis_version_ids"]
        admitted, = store.query("human", """
            SELECT admitted, body FROM rm.formal_item_status WHERE id = %s
        """, version_id)
        assert admitted["admitted"]
        assert admitted["body"]["kind"] == "hypothesis"
        assert admitted["body"]["natural"] == premise["wording"]
        assert admitted["body"]["formal"]["lean"]["proposition"] == premise["lean"]
        assert store.query("human", """
            SELECT kind FROM rm.claim_edges WHERE source_id = %s AND target_id = %s
        """, revised_id, claim_id) == [{"kind": "supersedes"}]


def test_premise_review_preserves_frozen_graph_and_records_explicit_revision(tmp_path, monkeypatch):
    with disposable_project_logins() as logins:
        for name in tuple(os.environ):
            if name.startswith("RM_"):
                monkeypatch.delenv(name)
        for name, value in logins.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        workspace = paths.initialize_workspace(tmp_path / "project")
        with paths.use_workspace(workspace, strict=True):
            bind_workspace(workspace)

        with _workbench(workspace) as server:
            claim = _add_claim(server, "Selected independent theorem", _TARGET)
            other = _add_claim(server, "Another theorem on the project canvas", "True")
            claim_id, other_id = claim["claim_id"], other["claim_id"]
            before = _check_original(workspace, claim)
            original = _graph(server, claim_id)
            assert original["claim"]["statement_id"] == before["statement_id"]
            evidence, = [node for node in original["nodes"] if node["kind"] == "evidence"]
            assert evidence["reference"]["evidence_id"] == before["evidence_id"]
            assert any(edge["source"] == evidence["id"] and edge["kind"] == "actual" for edge in original["edges"])
            other_original = _graph(server, other_id)
            revised_id, premise = _propose_and_review(server, original, other_id)
            assert revised_id not in {claim_id, other_id}

        with _workbench(workspace) as reopened:
            recorded = _graph(reopened, claim_id)
            _assert_original_graph(recorded, original)
            assert not _pending(recorded)
            history = {item["lean"]: item for item in recorded["claim"]["premise_review"]["proposals"]}
            assert history["1 = 1"]["state"] == "rejected"
            assert history[premise["lean"]]["state"] == "revised"
            assert history[premise["lean"]]["revised_claim_id"] == revised_id
            assert _graph(reopened, other_id) == other_original
            revised = _graph(reopened, revised_id)
            assert revised["claim"]["statement_id"] is None
            assert not any(node["kind"] == "evidence" for node in revised["nodes"])
            assert len([node for node in revised["nodes"] if node["kind"] == "hypothesis"]) == 1
        _assert_recorded_revision(workspace, claim_id, revised_id, before, premise)
