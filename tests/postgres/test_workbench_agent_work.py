"""Exercise project work, its consent, and exact handoff through the Workbench server."""

from contextlib import nullcontext
from subprocess import check_output
from threading import Thread
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_multiplier.model_adapters import ModelConfig
from research_multiplier.models.model_errors import ModelFailure
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import workbench_agent_work
from tests.postgres.test_workbench_routes import _request, workbench  # noqa: F401


pytestmark = pytest.mark.postgres
_ROUTE = "/api/agent-work"


def _get(server, identifier):
    return _request(server, "GET", f"{_ROUTE}?task={identifier}")["task"]


def _finished(server, identifier, *, status="delivered"):
    deadline = monotonic() + 10
    while True:
        task = _get(server, identifier)
        if task["status"] not in {"queued", "running"} or monotonic() >= deadline:
            assert task["status"] == status, task["error"]
            return task
        sleep(.02)


@pytest.mark.parametrize("first_receipt_committed", [True, False])
def test_workbench_resumes_the_queue_after_finish_connection_loss(workbench, monkeypatch, first_receipt_committed):
    server, _, _, _ = workbench
    identity = {"id": str(uuid4()), "provider": "codex-cli", "model": "fixture", "fingerprint": "a" * 64}
    config = ModelConfig("codex-cli", "fixture", "openai", "", binary="/usr/bin/true")
    agent = {
        "id": str(uuid4()), "revision": 1, "name": "Writer", "connection_id": identity["id"],
        "instructions": "Follow the approved fixture.",
    }
    monkeypatch.setattr(server.team_agents, "list", lambda: [agent])
    monkeypatch.setattr(server.connections, "resolve", lambda _: (config, identity))
    monkeypatch.setattr(server.connections, "use", lambda _: nullcontext((config, identity)))

    calls = []

    def model_result(config, role, system, user, cap, timeout):
        calls.append(user["title"])
        return SimpleNamespace(output={"summary": "Drafted fixture guidance", "body": "Approved test text."})

    monkeypatch.setattr(workbench_agent_work, "complete_structured", model_result)

    def enqueue(title):
        item = {"task_id": str(uuid4()), "agent_id": agent["id"], "mode": "draft", "title": title,
                "instructions": "Draft a short note.", "criteria": "One actionable sentence."}
        preview = _request(server, "POST", _ROUTE + "/preview", body=item)
        server.write(server.agent_work.start, {**item, "token": preview["token"], "confirm_external": True})
        return item["task_id"]

    first = enqueue("First approved fixture")
    second = enqueue("Second approved fixture")
    query = Store.query
    lost = []

    def lose_first_receipt(store, role, sql, *values):
        if role == "scheduler" and "rm.finish_agent_work" in sql and str(values[0]) == first:
            lost.append(values[1])
            if first_receipt_committed:
                query(store, role, sql, *values)
            raise DatabaseOperationError("scheduler", "08006", retryable=True)
        return query(store, role, sql, *values)

    monkeypatch.setattr(Store, "query", lose_first_receipt)
    server._run_agent_work()

    assert lost == [1]
    assert calls == ["First approved fixture", "Second approved fixture"]
    first_detail = _get(server, first)
    if first_receipt_committed:
        assert first_detail["status"] == "delivered" and first_detail["delivery_sha256"]
    else:
        assert first_detail["status"] == "running" and not first_detail["delivery_sha256"]
    assert _get(server, second)["status"] == "delivered"


def test_workbench_pins_code_handoff_and_requires_a_human_decision(workbench, monkeypatch):
    server, _, _, _ = workbench
    identity = {"id": str(uuid4()), "provider": "codex-cli", "model": "fixture", "fingerprint": "a" * 64}
    config = ModelConfig("codex-cli", "fixture", "openai", "", binary="/usr/bin/true")
    employees = [{
        "id": str(uuid4()), "revision": 1, "name": name, "connection_id": identity["id"], "instructions": instructions,
    } for name, instructions in (("Implementer", "Make only the approved change."), ("Reviewer", "Report findings."))]
    monkeypatch.setattr(server.team_agents, "list", lambda: employees)
    monkeypatch.setattr(server.connections, "resolve", lambda _: (config, identity))
    monkeypatch.setattr(server.connections, "use", lambda _: nullcontext((config, identity)))

    requests = []

    def model_result(config, role, system, user, cap, timeout):
        requests.append({"system": system, "user": user})
        if "predecessor" in user["inputs"]:
            return SimpleNamespace(output={"summary": "Reviewed the pinned delivery", "body": "The scope matches."})
        if not user["inputs"]:
            if sum(not request["user"]["inputs"] for request in requests) == 1:
                raise ModelFailure("transport", "Synthetic transport failure.")
            return SimpleNamespace(output={"summary": "Drafted fixture guidance", "body": "Use the original version."})
        return SimpleNamespace(output={"summary": "Edit selected file",
                                       "changes": [{"path": "example.txt", "content": "After\n"}]})

    monkeypatch.setattr(workbench_agent_work, "complete_structured", model_result)
    checkout = server.workspace / "inputs" / "source"
    checkout.mkdir()

    def git(*arguments):
        return check_output(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
             "-c", "core.hooksPath=/dev/null", *arguments], cwd=checkout, text=True,
        ).strip()

    git("init", "--quiet", "--template=")
    source = checkout / "example.txt"
    source.write_text("Before\n", encoding="utf-8")
    git("add", "example.txt")
    git("commit", "--quiet", "--no-gpg-sign", "-m", "Fixture")
    revision = git("rev-parse", "HEAD")
    source.write_text("Unsaved edit\n", encoding="utf-8")

    item = {
        "task_id": str(uuid4()), "agent_id": employees[0]["id"], "mode": "implement", "title": "Change the fixture",
        "instructions": "Replace the pinned line.", "criteria": "The patch applies to the original version.",
        "checkout": "inputs/source", "revision": revision, "files": ["example.txt"],
    }
    preview = _request(server, "POST", _ROUTE + "/preview", body=item)
    assert preview["external"] and preview["base_revision"] == revision
    assert len(requests) == 0
    approval = {**item, "token": preview["token"], "confirm_external": True}
    stale = {**approval, "criteria": "Changed after preview"}
    assert _request(server, "POST", _ROUTE + "/start", body=stale, status=409)["error"] == "agentWork.stale"
    _request(server, "POST", _ROUTE + "/start", body=approval)
    _request(server, "POST", _ROUTE + "/start", body=approval)
    first = _finished(server, item["task_id"])
    assert requests[0]["user"]["inputs"] == {"revision": revision, "files": {"example.txt": "Before\n"}}
    assert first["delivery"]["checks"] == ["patch_applies"]
    assert source.read_text(encoding="utf-8") == "Unsaved edit\n"

    review = {
        **{key: item[key] for key in ("title", "criteria")},
        "task_id": str(uuid4()), "agent_id": employees[1]["id"], "mode": "review",
        "instructions": "Review the recorded patch.", "predecessor_id": first["id"],
    }
    prepared = _request(server, "POST", _ROUTE + "/preview", body=review)
    assert prepared["predecessor_sha256"] == first["delivery_sha256"]
    assert prepared["base_revision"] == revision
    _request(server, "POST", _ROUTE + "/start",
             body={**review, "token": prepared["token"], "confirm_external": True})
    second = _finished(server, review["task_id"])
    assert len(requests) == 2
    assert requests[1]["user"]["inputs"]["predecessor"]["delivery"] == first["delivery"]
    assert second["agent_name"] == "Reviewer"
    assert second["agent_id"] == employees[1]["id"]
    legacy = _request(server, "GET", _ROUTE)
    workspace_id = paths.workspace_id(server.workspace)
    versioned = _request(server, "GET", f"/api/v1/workspaces/{workspace_id}/agent-work")
    assert versioned == {"items": legacy["tasks"], "next_cursor": None, "available": True}
    assert {task["id"]: task["agent_id"] for task in versioned["items"]} == {
        first["id"]: employees[0]["id"], second["id"]: employees[1]["id"],
    }
    assert _get(server, first["id"])["status"] == "delivered"
    decision = {
        "task_id": second["id"], "action_id": str(uuid4()), "delivery_sha256": second["delivery_sha256"],
        "decision": "accept", "reason": "Checked the review.",
    }
    wrong = {**decision, "delivery_sha256": "0" * 64}
    assert _request(server, "POST", _ROUTE + "/review", body=wrong, status=409)["error"] == "agentWork.stale"
    accepted = _request(server, "POST", _ROUTE + "/review", body=decision)["task"]
    assert _request(server, "POST", _ROUTE + "/review", body=decision)["task"] == accepted
    assert accepted["status"] == "accepted"
    assert _get(server, second["id"])["review"]["reason"] == decision["reason"]

    draft = {
        "task_id": str(uuid4()), "agent_id": employees[0]["id"], "mode": "draft", "title": "Write a handover",
        "instructions": "Draft a short note.", "criteria": "One actionable sentence.",
    }
    preview = _request(server, "POST", _ROUTE + "/preview", body=draft)
    _request(server, "POST", _ROUTE + "/start", body={**draft, "token": preview["token"], "confirm_external": True})
    _finished(server, draft["task_id"], status="failed")
    retry = {"task_id": draft["task_id"], "action_id": str(uuid4())}
    resumed = _request(server, "POST", _ROUTE + "/retry-preview", body=retry)
    assert resumed["outgoing"] == preview["outgoing"] and resumed["generation"] == 1
    authorization = {**retry, "generation": resumed["generation"], "token": resumed["token"], "confirm_external": True}
    assert _request(server, "POST", _ROUTE + "/retry",
                    body={**authorization, "confirm_external": False}, status=409)["error"] == "agentWork.confirmation"
    _request(server, "POST", _ROUTE + "/retry", body=authorization)
    _request(server, "POST", _ROUTE + "/retry", body=authorization)
    written = _finished(server, draft["task_id"])
    assert len(requests) == 4
    assert written["delivery"]["kind"] == "draft"
    editing = {
        **review, "task_id": str(uuid4()), "title": "Edit the handover",
        "instructions": "Edit the recorded note.", "predecessor_id": written["id"],
    }
    preview = _request(server, "POST", _ROUTE + "/preview", body=editing)
    _request(server, "POST", _ROUTE + "/start", body={**editing, "token": preview["token"], "confirm_external": True})
    edited = _finished(server, editing["task_id"])
    assert requests[4]["user"]["inputs"]["predecessor"]["delivery"] == written["delivery"]
    returned = _request(server, "POST", _ROUTE + "/review", body={
        "task_id": edited["id"], "action_id": str(uuid4()), "delivery_sha256": edited["delivery_sha256"],
        "decision": "return", "reason": "Clarify who should follow up.",
    })["task"]
    assert returned["status"] == "returned"

    with WorkbenchServer(server.workspace, claim_id=None, port=0) as restarted:
        browser = Thread(target=restarted.serve_forever, daemon=True)
        browser.start()
        try:
            assert _get(restarted, written["id"]) == written
            assert _get(restarted, second["id"]) == accepted
            assert _get(restarted, edited["id"]) == returned
        finally:
            restarted.shutdown()
            browser.join()
