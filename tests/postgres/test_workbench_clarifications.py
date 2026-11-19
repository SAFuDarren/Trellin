# SPDX-License-Identifier: Apache-2.0
"""Check Review's clarification workflow through HTTP and native PostgreSQL."""

from collections import deque
from http.client import HTTPConnection
import json
import os
from threading import Thread
from uuid import uuid4

import pytest

from research_multiplier.model_adapters import ModelRun
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import goal_suggestions, intake, target_clarifications, target_draft
from tests._native_db import disposable_project_logins


pytestmark = pytest.mark.postgres
STATEMENT = "The selected instance is equal to itself."
QUESTION = "Which type is intended?"
ANSWER = "The natural numbers."
REASON = "This matches the stated problem."
FORMAL = {"lean": {"proposition": "n = n", "parameters": [{"name": "n", "type": "Nat"}]}}


@pytest.fixture
def review_server(tmp_path, monkeypatch):
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
            with WorkbenchServer(workspace, port=0) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    yield server
                finally:
                    server.shutdown()
                    thread.join()


def _request(server, path, *, document=None, status=200, session=True):
    headers = {"X-RM-Session": server.session} if session else {}
    method, body = "GET", None
    if document is not None:
        method, body = "POST", json.dumps(document).encode()
        headers.update({"Origin": server.origin, "Content-Type": "application/json"})
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == status, (path, response.status, payload)
        return payload
    finally:
        connection.close()


def _mock_formalizer(monkeypatch, *outputs):
    scopes, requests = [], []
    pending = deque(outputs)

    def configure(role, *, private):
        assert role == "EXPLORE"
        scopes.append(private)
        return object()

    def complete(_config, task, _prompt, data, *_limits):
        assert task == "formalize-target"
        with Store() as store:
            assert not store.query("human", """
                SELECT pid FROM pg_stat_activity WHERE usename = session_user
                  AND state LIKE 'idle in transaction%%'
            """)
        requests.append(data)
        return ModelRun(pending.popleft(), {"model_id": "fixture model"}, {"private": "never sent to HTTP"})

    monkeypatch.setattr(target_draft.ModelConfig, "from_env", configure)
    monkeypatch.setattr(target_draft, "complete_structured", complete)
    return scopes, requests


def _section(server, request_id, *, workspace=None):
    return f"/api/v1/workspaces/{workspace or paths.workspace_id(server.workspace)}/clarifications/{request_id}"


def _replay(server, workspace, cursor):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", f"/api/v1/workspaces/{workspace}/events", headers={
            "X-RM-Session": server.session, "Last-Event-ID": cursor,
        })
        response = connection.getresponse()
        assert response.status == 200 and response.readline() == b": connected\n"
        assert response.readline() == b"\n"
        assert response.readline() == f"id: {int(cursor) + 1}\n".encode()
        assert response.readline() == b"event: workspace.changed\n"
        return json.loads(response.readline().removeprefix(b"data: "))
    finally:
        connection.close()


@pytest.mark.parametrize("sensitivity", ["public", "restricted"])
def test_target_review_survives_refresh_and_only_freezes_the_confirmed_proposal(review_server, monkeypatch, sensitivity):
    server = review_server
    local = sensitivity == "restricted"
    scope = "local" if local else "public"
    scopes, requests = _mock_formalizer(monkeypatch, {"questions": [QUESTION]}, {"formal": {}}, {"formal": FORMAL})
    with Store() as store:
        draft = target_draft.propose_document(store, {"title": "Clarify the type", "domain": "lean-core",
            "sensitivity": sensitivity, "statement": STATEMENT}, confirm_local=local)
    request_id = draft["clarification"]["id"]
    route = _section(server, request_id)
    _request(server, route, status=401, session=False)
    _request(server, _section(server, request_id, workspace=uuid4()), status=404)
    initial = _request(server, route)
    assert initial == {"id": str(request_id), "title": "Clarify the type", "sensitivity": sensitivity,
                       "questions": [QUESTION], "answers": None, "reason": None, "statement": STATEMENT,
                       "kind": "target", "status": "waiting", "proposal": None, "next_id": None, "accepted": None}
    inbox = _request(server, "/api/inbox")
    workspace = inbox["clarification_review"]["workspace_id"]
    before_answer = inbox["workspace_events"]["cursor"]
    assert any(item["item_id"] == str(request_id) for item in inbox["items"])

    invalid = _request(server, route + "/answer", document={"answers": [ANSWER], "reason": REASON, "actor": "a"},
                       status=400)
    assert invalid["error"]["code"] == "clarification.invalid"
    response = _request(server, route + "/answer", document={"answers": [ANSWER], "reason": REASON})
    assert response["status"] == "answered" and response["answers"] == [ANSWER]
    assert _request(server, route) == response and scopes == [local]
    assert _request(server, route + "/answer", document={"answers": [ANSWER], "reason": REASON}) == response
    _request(server, route + "/prepare", document={"scope": "public" if local else "local"}, status=403)
    assert len(requests) == 1
    inbox = _request(server, "/api/inbox")
    assert [(item["id"], item["status"]) for item in inbox["clarification_review"]["followups"]] == [
        (str(request_id), "answered"),
    ]
    with Store() as store:
        events = store.query("human", "SELECT * FROM rm.read_workspace_events(%s,%s,100)", workspace, int(before_answer))
    assert [(row["data"], row["user_id"]) for row in events] == [
        ({"area": "clarification"}, paths.local_user_id(server.workspace)),
    ]

    failed = _request(server, route + "/prepare", document={"scope": scope}, status=409)
    assert failed["error"]["code"] == "clarification.prepare_failed"
    assert _request(server, route) == response
    prepared = _request(server, route + "/prepare", document={"scope": scope})
    assert prepared["status"] == "ready" and prepared["statement"] == STATEMENT
    token = prepared["proposal"]["token"]
    assert prepared["proposal"] == {"token": token, **FORMAL["lean"]}
    assert _request(server, route + "/prepare", document={"scope": scope}) == prepared
    assert requests[-1]["interpretation_answers"] == [{"question": QUESTION, "answer": ANSWER}]
    assert scopes == [local, local, local]
    assert _request(server, "/api/inbox")["clarification_review"]["followups"][0]["status"] == "ready"
    with Store() as store:
        assert not store.query("human", "SELECT id FROM rm.research_target_status")
    _request(server, route + "/confirm", document={"token": "0" * 64, "reason": REASON}, status=409)
    confirmed = _request(server, route + "/confirm", document={"token": token, "reason": REASON})
    assert confirmed["status"] == "confirmed" and confirmed["accepted"]["claim_id"] is None
    assert _request(server, route + "/confirm", document={"token": token, "reason": REASON}) == confirmed
    assert not _request(server, "/api/inbox")["clarification_review"]["followups"]
    with Store() as store:
        saved = intake.target(store, confirmed["accepted"]["id"])
        assert saved["body"]["statement"] == STATEMENT and saved["body"]["formal"] == FORMAL
        assert saved["intake_id"] is None


def test_follow_up_ambiguity_preserves_answers_and_refreshes_cli_changes(review_server, monkeypatch):
    server = review_server
    followup = "Is the named value quantified?"
    scopes, requests = _mock_formalizer(monkeypatch, {"questions": [QUESTION]}, {"questions": [followup]},
                                       {"formal": FORMAL})
    with Store() as store:
        draft = target_draft.propose_document(store, {"title": "Follow-up", "domain": "lean-core",
            "sensitivity": "public", "statement": STATEMENT}, confirm_local=False)
        first = draft["clarification"]["id"]
        workspace = paths.workspace_id(server.workspace)
        cursor = str(store.query("human", "SELECT rm.workspace_event_cursor(%s) AS head", workspace)[0]["head"])
        target_clarifications.answer(store, first, [ANSWER], "CLI example", REASON)
    event = _replay(server, workspace, cursor)
    assert event["data"] == {"area": "clarification"}
    assert event["actor"] == {"kind": "user", "id": str(paths.local_user_id(server.workspace))}
    current = _request(server, _section(server, first))
    assert current["answers"] == [ANSWER] and current["status"] == "answered"
    parent = _request(server, _section(server, first) + "/prepare", document={"scope": "public"})
    assert parent["status"] == "continued" and parent["proposal"] is None
    route = _section(server, parent["next_id"])
    assert _request(server, route)["questions"] == [followup]
    _request(server, route + "/answer", document={"answers": ["Yes, all natural numbers."], "reason": REASON})
    assert _request(server, route + "/prepare", document={"scope": "public"})["status"] == "ready"
    assert requests[-1]["interpretation_answers"] == [
        {"question": QUESTION, "answer": ANSWER}, {"question": followup, "answer": "Yes, all natural numbers."},
    ]
    assert scopes == [False] * 3


@pytest.mark.parametrize("retired", [False, True])
def test_conjecture_confirmation_respects_the_active_frame(review_server, monkeypatch, retired):
    server = review_server
    _mock_formalizer(monkeypatch, {"questions": [QUESTION]}, {"formal": {"lean": {"proposition": "True"}}})
    notes = {"title": "Clarify conjecture", "domain": "lean-core", "objective": "Clarify a public example",
             "sensitivity": "public", "items": [{"kind": "hypothesis", "natural": "Truth is in scope.",
                                                "formal": {"lean": {"proposition": "True"}}}]}
    with Store() as store:
        draft = intake.draft(store, json.dumps(notes).encode())
        frame = intake.confirm(store, draft["id"], draft["intake_sha256"],
                               {"i1": {"action": "accept", "reason": REASON}}, "CLI example", REASON)
        version_id, = frame["body"]["basis_version_ids"]
        discussions = {"proposals": [{"statement": STATEMENT, "reason": "Use the premise.",
                                      "basis_version_ids": [version_id]}]}
        monkeypatch.setattr(goal_suggestions, "complete_structured", lambda *_: ModelRun(discussions, {}, {}))
        proposal = goal_suggestions.discuss(store, frame["id"], {"intuition": "Try this statement."})
        draft = goal_suggestions.formalize_discussion(store, frame["id"], proposal["record"]["sha256"], 1)
    route = _section(server, draft["clarification"]["id"])
    _request(server, route + "/answer", document={"answers": [ANSWER], "reason": REASON})
    prepared = _request(server, route + "/prepare", document={"scope": "public"})
    assert prepared["kind"] == "conjecture" and prepared["status"] == "ready"
    with Store() as store:
        assert not store.query("human", "SELECT id FROM rm.claims")
        if retired:
            store.query("human", "SELECT rm.retire_formal_item(%s,%s)", version_id, REASON)
    assert _request(server, route)["status"] == ("stale" if retired else "ready")
    decision = _request(server, route + "/confirm",
                        document={"token": prepared["proposal"]["token"], "reason": REASON},
                        status=409 if retired else 200)
    if retired:
        assert decision["error"]["code"] == "clarification.stale"
    else:
        assert decision["status"] == "confirmed" and decision["accepted"]["claim_id"]
        with Store() as store:
            frozen = json.loads(store.frozen(decision["accepted"]["claim_id"])["contract_bytes"])
        assert frozen["statement"] == STATEMENT
        assert frozen["formal_skeleton"]["basis_version_ids"] == [version_id]
    assert not _request(server, "/api/inbox")["clarification_review"]["followups"]
