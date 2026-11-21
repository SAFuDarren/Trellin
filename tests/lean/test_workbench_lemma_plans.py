# SPDX-License-Identifier: Apache-2.0
"""Check the authenticated local lemma plan through Lean and the proof graph."""

import json
import os
from http.client import HTTPConnection
from threading import Thread
from uuid import uuid4

import pytest

from research_multiplier import general_lean, local_lemma_kernel, local_lemmas
from research_multiplier.proof import lean_reductions
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import intake, lean_workflow
from tests._native_db import disposable_project_logins

pytestmark = pytest.mark.lean


def _confirmed_statement():
    target = json.dumps({
        "title": "Reusing one reviewed premise", "domain": "lean-core", "sensitivity": "restricted",
        "statement": "Two copies of True hold.", "formal": {"lean": {"proposition": "True ∧ True"}},
    }).encode()
    basis = json.dumps({"items": [{
        "kind": "hypothesis", "natural": "Assume True.", "formal": {"lean": {"proposition": "True"}},
    }]}).encode()
    actor, reason = "Fixture", "Reviewed the exact synthetic premise and goal."
    with Store() as store:
        preview = intake.preview_target(store, target)
        frozen = intake.freeze_target(store, target, preview["target_sha256"], actor, reason)
        draft = intake.draft(store, basis, target_id=frozen["id"])
        intake.confirm(store, draft["id"], draft["intake_sha256"],
                       {"i1": {"action": "accept", "reason": reason}}, actor, reason)
        packet = intake.target(store, frozen["id"])
    statement = general_lean.propose(packet["packet_id"])
    lean_workflow.confirm(statement["id"], statement["version"], statement["statement_sha256"], actor, reason)
    return {
        "claim_id": str(statement["claim_id"]), "statement_id": str(statement["id"]),
        "statement_sha256": statement["statement_sha256"],
    }


def _request(server, route, document=None):
    headers = {"X-RM-Session": server.session}
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


@pytest.fixture
def scoped_workbench(tmp_path, monkeypatch):
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
            scope = _confirmed_statement()
        with WorkbenchServer(workspace, port=0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server, scope
            finally:
                server.shutdown()
                thread.join()


def test_lemma_plan_verifies_only_intermediate_nodes_in_current_scope(scoped_workbench, monkeypatch):
    server, scope = scoped_workbench
    route = "/api/lemma-plans"
    graph = f"/api/graph?claim={scope['claim_id']}"
    proof = {"intro": {"count": 1, "body": {"use": 0}}}
    first = {"name": "First", "proposition": "True", "proof": proof}
    plan = {"format_version": 1, "nodes": [first]}
    document = {**scope, "plan": plan}

    for data in (
        {**document, "plan": {**plan, "goal_proof": proof}},
        {**document, "plan": {**plan, "nodes": [first] * 17}},
        {**document, "plan": {**plan, "nodes": [{**first, "source_path": "/tmp/example.lean"}]}},
        {**document, "output": "/tmp/example.lean"},
    ):
        assert _request(server, route, data) == (400, {"error": "lemmaPlan.invalid"})
    for data in (
        {**document, "claim_id": str(uuid4())},
        {**document, "statement_id": str(uuid4())},
        {**document, "statement_sha256": "0" * 64},
    ):
        assert _request(server, route, data) == (409, {"error": "lemmaPlan.stale"})

    def unavailable(*args, **kwargs):
        raise OSError("The local verifier is temporarily unavailable.")

    with monkeypatch.context() as fault:
        fault.setattr(local_lemma_kernel, "check", unavailable)
        assert _request(server, route, document) == (422, {"error": "lemmaPlan.checkFailed"})

    bad = {"name": "Unproved", "proposition": "False", "proof": proof}
    incomplete = {**document, "plan": {**plan, "nodes": [first, bad]}}
    assert _request(server, route, incomplete) == (422, {"error": "lemmaPlan.checkFailed"})

    status, snapshot = _request(server, graph)
    assert status == 200
    verified, = [node for node in snapshot["nodes"] if node["kind"] == "local_lemma"]
    assert verified["proposition"] == "True" and verified["verification_status"] == "verified"

    assert _request(server, route, document) == (200, {"verified": 1})
    status, snapshot = _request(server, graph)
    assert status == 200
    assert [node for node in snapshot["nodes"] if node["kind"] == "local_lemma"] == [verified]
    assert snapshot["claim"]["status"] == "open"
    assert snapshot["claim"]["lemma_plan_ready"] is True


def test_canvas_counts_each_current_reduction_obligation_and_refreshes_after_retirement(scoped_workbench):
    server, scope = scoped_workbench
    statement_id = scope["statement_id"]

    def counts():
        status, reply = _request(server, "/api/project-canvas/claims")
        assert status == 200, reply
        claim, = [item for item in reply["claims"] if item["id"] == scope["claim_id"]]
        return claim["obligations"]

    assert counts() == {"verified": 0, "total": 0}
    with paths.use_workspace(server.workspace, strict=True):
        for obligations, proof in (
            (["True"], {"and": [{"use": 0}, {"use": 1}]}),
            (["True", "True ∧ True"], {"use": 2}),
        ):
            lean_reductions.reductions(statement_id, document={
                "format_version": 1, "obligations": obligations,
                "proof": {"intro": {"count": 1 + len(obligations), "body": proof}},
            })
    assert counts() == {"verified": 0, "total": 3}

    with paths.use_workspace(server.workspace, strict=True):
        first, = local_lemmas.derive(statement_id, {"format_version": 1, "nodes": [{
            "name": "Truth", "proposition": "True", "proof": {"intro": {"count": 1, "body": {"use": 0}}},
        }]})["lemmas"]
    # These are two recorded obligations, even though they discharge from the same verified lemma.
    assert counts() == {"verified": 2, "total": 3}

    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        store.query("human", "SELECT rm.retire_verification_input(%s,%s,%s)",
                    "source", first["body"]["candidate"]["sha256"], "Exercise stale obligation progress.")
    assert counts() == {"verified": 0, "total": 3}
