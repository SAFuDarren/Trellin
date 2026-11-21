# SPDX-License-Identifier: Apache-2.0
"""Consent and durable conversation regressions against real project bindings."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_multiplier.model_adapters import ModelConfig
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workflows import workbench_agents, workbench_conversations
from tests.postgres.test_workbench_routes import _request, workbench  # noqa: F401


pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def isolated_team_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench_agents, "_settings_file", lambda: tmp_path / "team-agents.sqlite3")


def test_workbench_conversation_consent_and_archive(workbench, monkeypatch):
    server, _, _, _ = workbench
    identity = {"id": str(uuid4()), "provider": "codex-cli", "model": "gpt-fixture", "fingerprint": "a" * 64}
    config = ModelConfig("codex-cli", identity["model"], "openai", "", binary="/usr/bin/true")
    monkeypatch.setattr(server.discussions.registry, "resolve", lambda _: (config, identity))
    monkeypatch.setattr(server.discussions.registry, "use", lambda _: nullcontext((config, identity)))
    sent = []

    def reply(selected, service, system, user, cap, timeout):
        sent.append((selected.model, service, system, user, cap, timeout))
        metadata = {"provider": selected.provider, "id": "gpt-fixture-actual",
                    "parameters": {"requested_model": selected.model, "model_id_reported": True}}
        return SimpleNamespace(output={"reply": "The premise remains unchanged."}, metadata=metadata)

    monkeypatch.setattr(workbench_conversations, "complete_structured", reply)
    base = "/api/conversations"
    thread = _request(server, "POST", base + "/create", body={"title": "A free discussion"})["thread"]
    assert thread["claim_id"] is None and thread["agent_id"] is None and thread["messages"] == []
    assert _request(server, "GET", base)["threads"][0]["latest_turn"] is None
    request = {"thread_id": thread["id"], "connection_id": identity["id"],
               "request_id": str(uuid4()), "text": "What is the next question?"}
    preview = _request(server, "POST", base + "/preview", body=request)
    assert preview["external"] and preview["model"] == identity["model"]
    agreement = {**request, "token": preview["token"], "confirm_external": True}
    for changed, reason in (
        ({"text": "Different text"}, "stale"),
        ({"request_id": str(uuid4())}, "stale"),
        ({"confirm_external": False}, "confirmation"),
    ):
        assert _request(server, "POST", base + "/send", body={**agreement, **changed}, status=409) == {
            "error": "conversation." + reason
        }
    assert sent == []
    _request(server, "POST", base + "/send", body=agreement)
    deadline = monotonic() + 5
    while monotonic() < deadline:
        result = _request(server, "GET", base + "/thread?thread_id=" + thread["id"])["thread"]
        if not result["running"]:
            break
        sleep(.02)
    assert not result["running"]
    assert len(sent) == 1
    model, service, system, user, cap, timeout = sent[0]
    assert model == identity["model"] and service == "conversation"
    assert {"system": system, "user": user} == json.loads(preview["outgoing"])
    assert cap == preview["limits"]["max_output_tokens"]
    assert timeout <= preview["limits"]["timeout_seconds"]
    assert result["messages"][-1]["model"] == "gpt-fixture-actual"
    assert result["messages"][-1]["requested_model"] == identity["model"]
    assert result["messages"][-1]["text"] == "The premise remains unchanged."
    _request(server, "POST", base + "/send", body=agreement)
    assert len(sent) == 1

    threads = _request(server, "GET", base)["threads"]
    assert [item["id"] for item in threads] == [thread["id"]]
    latest = threads[0]["latest_turn"]
    assert threads[0]["running"] is False
    assert latest == {
        "provider": identity["provider"], "requested_model": identity["model"], "model": "gpt-fixture-actual",
        "preview": "The premise remains unchanged.", "status": "answered", "at": latest["at"],
    }
    assert latest["at"].startswith(result["messages"][-1]["at"][:19])
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        with pytest.raises(DatabaseOperationError) as denied:
            store.query("agent", "SELECT * FROM rm.visible_conversation_threads")
        assert denied.value.state == "42501"
    _request(server, "POST", base + "/archive", body={"thread_id": thread["id"]})
    assert _request(server, "GET", base)["threads"] == []


def test_workbench_conversation_checks_scope_before_external_preview(workbench, monkeypatch):
    server, claim, statement_id, _ = workbench
    identity = {"id": str(uuid4()), "provider": "claude-code", "model": "sonnet", "fingerprint": "a" * 64}
    config = ModelConfig("claude-code", identity["model"], "claude", "", binary="/usr/bin/true")
    monkeypatch.setattr(server.discussions.registry, "resolve", lambda _: (config, identity))
    graph = _request(server, "GET", "/api/graph?claim=" + claim)
    node = next(item for item in graph["nodes"] if item["kind"] == "goal")
    base = "/api/conversations"
    item = {"title": "Node discussion", "claim_id": claim, "statement_id": statement_id, "node_id": node["id"]}
    assert _request(server, "POST", base + "/create", body={**item, "node_id": "missing"}, status=409) == {
        "error": "conversation.stale"
    }
    thread = _request(server, "POST", base + "/create", body=item)["thread"]
    body = {"thread_id": thread["id"], "connection_id": identity["id"],
            "request_id": str(uuid4()), "text": "Use this node for the discussion."}
    preview = _request(server, "POST", base + "/preview", body=body)
    assert json.loads(preview["outgoing"])["user"]["selected_node"]["formalization"] == node["formalization"]
    # A changed transport fingerprint invalidates prior consent, even when the exact model ID is unchanged.
    identity = {**identity, "fingerprint": "b" * 64}
    assert _request(server, "POST", base + "/send",
                    body={**body, "token": preview["token"], "confirm_external": True}, status=409) == {
        "error": "conversation.stale"
    }
    assert _request(server, "GET", base + "/thread?thread_id=" + thread["id"])["thread"]["messages"] == []


def test_named_conversation_binds_employee_and_exact_consent(workbench, monkeypatch):
    server, _, _, _ = workbench
    identity = {"id": str(uuid4()), "provider": "codex-cli", "model": "gpt-fixture", "fingerprint": "a" * 64}
    config = ModelConfig("codex-cli", identity["model"], "openai", "", binary="/usr/bin/true")
    other_connection = {**identity, "id": str(uuid4())}
    monkeypatch.setattr(server.connections, "list", lambda: [
        {**connection, "status": "connected"} for connection in (identity, other_connection)
    ])
    monkeypatch.setattr(server.discussions.registry, "resolve", lambda _: (config, identity))
    monkeypatch.setattr(server.discussions.registry, "use", lambda _: nullcontext((config, identity)))
    sent = []

    def reply(selected, service, system, user, cap, timeout):
        sent.append({"system": system, "user": user})
        metadata = {"provider": selected.provider, "id": identity["model"],
                    "parameters": {"requested_model": selected.model, "model_id_reported": True}}
        return SimpleNamespace(output={"reply": "An answer for this task."}, metadata=metadata)

    monkeypatch.setattr(workbench_conversations, "complete_structured", reply)
    agents_path = "/api/team-agents/save"
    profile = {"id": str(uuid4()), "revision": 0, "connection_id": identity["id"], "name": "Reviewer",
               "description": "", "avatar": "book", "instructions": "Device-only configuration text.",
               "starters": []}
    first = _request(server, "POST", agents_path, body=profile)["agent"]
    second = _request(server, "POST", agents_path, body={**profile, "id": str(uuid4()), "name": "Writer"})["agent"]
    base = "/api/conversations"
    draft = {"title": "Check the argument", "agent_id": first["id"], "request_id": str(uuid4())}

    with ThreadPoolExecutor(max_workers=2) as requests:
        left, right = requests.map(lambda _: _request(server, "POST", base + "/create", body=draft)["thread"],
                                   range(2))
    assert left == right
    assert left["agent_id"] == first["id"] and left["messages"] == []
    assert _request(server, "POST", base + "/create",
                    body={**draft, "agent_id": second["id"]}, status=409) == {"error": "conversation.stale"}
    other = _request(server, "POST", base + "/create", body={
        **draft, "agent_id": second["id"], "request_id": str(uuid4()),
    })["thread"]
    assert other["id"] != left["id"] and other["agent_id"] == second["id"]
    listed = _request(server, "GET", base)["threads"]
    assert {item["id"]: item["agent_id"] for item in listed} == {left["id"]: first["id"], other["id"]: second["id"]}
    assert sent == []

    request = {"thread_id": left["id"], "connection_id": identity["id"],
               "request_id": str(uuid4()), "text": "What is the missing step?"}
    assert _request(server, "POST", base + "/preview",
                    body={**request, "connection_id": other_connection["id"]}, status=409) == {
        "error": "conversation.stale",
    }
    preview = _request(server, "POST", base + "/preview", body=request)
    assert "Device-only configuration text." not in preview["outgoing"]
    agreement = {**request, "token": preview["token"], "confirm_external": True}
    assert _request(server, "POST", base + "/send",
                    body={**agreement, "thread_id": other["id"]}, status=409) == {"error": "conversation.stale"}
    updated = _request(server, "POST", agents_path, body={**first, "description": "Revised role"})["agent"]
    assert _request(server, "POST", base + "/send", body=agreement, status=409) == {"error": "conversation.stale"}
    assert sent == []

    preview = _request(server, "POST", base + "/preview", body=request)
    agreement = {**agreement, "token": preview["token"]}
    _request(server, "POST", base + "/send", body=agreement)
    deadline = monotonic() + 5
    while monotonic() < deadline:
        thread = _request(server, "GET", base + "/thread?thread_id=" + left["id"])["thread"]
        if not thread["running"]:
            break
        sleep(.02)
    assert thread["agent_id"] == first["id"]
    assert not thread["running"]
    assert thread["messages"][-1]["text"] == "An answer for this task."
    assert sent == [json.loads(preview["outgoing"])]
    assert _request(server, "GET", base + "/thread?thread_id=" + other["id"])["thread"]["messages"] == []

    _request(server, "POST", agents_path, body={**updated, "connection_id": other_connection["id"]})
    _request(server, "POST", base + "/send", body=agreement)
    assert sent == [json.loads(preview["outgoing"])]
    assert _request(server, "POST", base + "/preview",
                    body={**request, "request_id": str(uuid4())}, status=409) == {"error": "conversation.stale"}
    _request(server, "POST", base + "/archive", body={"thread_id": left["id"]})
    assert _request(server, "POST", base + "/create", body=draft, status=409) == {"error": "conversation.stale"}


def test_named_conversation_create_rollback_keeps_request_reserved(workbench, monkeypatch):
    server, _, _, _ = workbench
    connection = {"id": str(uuid4()), "status": "connected", "fingerprint": "fixture"}
    monkeypatch.setattr(server.connections, "list", lambda: [connection])
    agent = _request(server, "POST", "/api/team-agents/save", body={
        "id": str(uuid4()), "revision": 0, "connection_id": connection["id"], "name": "Reader",
        "description": "", "avatar": "book", "instructions": "", "starters": [],
    })["agent"]
    draft = {"title": "Inspect the assumptions", "agent_id": agent["id"], "request_id": str(uuid4())}

    def rollback(store):
        workbench_conversations.create(store, server.team_agents, draft)
        raise RuntimeError("Simulate rollback after staging binding")

    with pytest.raises(RuntimeError, match="Simulate rollback"):
        server.write(rollback)
    base = "/api/conversations"
    assert _request(server, "GET", base) == {"threads": []}
    assert _request(server, "POST", base + "/create", body=draft, status=409) == {"error": "conversation.stale"}
    fresh = _request(server, "POST", base + "/create", body={**draft, "request_id": str(uuid4())})["thread"]
    assert fresh["agent_id"] == agent["id"]


def test_workbench_conversation_omits_history_from_the_signed_dispatch(workbench, monkeypatch):
    server, claim, statement_id, _ = workbench
    identity = {"id": str(uuid4()), "provider": "codex-cli", "model": "gpt-fixture", "fingerprint": "b" * 64}
    config = ModelConfig("codex-cli", identity["model"], "openai", "", binary="/usr/bin/true")
    monkeypatch.setattr(server.discussions.registry, "resolve", lambda _: (config, identity))
    monkeypatch.setattr(server.discussions.registry, "use", lambda _: nullcontext((config, identity)))
    sent = []

    def reply(selected, service, system, user, cap, timeout):
        sent.append({"system": system, "user": user})
        metadata = {"provider": selected.provider, "id": selected.model,
                    "parameters": {"requested_model": selected.model, "model_id_reported": True}}
        return SimpleNamespace(output={"reply": "Fixture reply."}, metadata=metadata)

    monkeypatch.setattr(workbench_conversations, "complete_structured", reply)
    graph = _request(server, "GET", "/api/graph?claim=" + claim)
    node = next(item for item in graph["nodes"] if item["kind"] == "goal")
    base = "/api/conversations"
    thread = _request(server, "POST", base + "/create", body={
        "title": "Scoped history", "claim_id": claim, "statement_id": statement_id, "node_id": node["id"],
    })["thread"]
    thread_url = base + "/thread?thread_id=" + thread["id"]

    def settled():
        until = monotonic() + 5
        while monotonic() < until:
            result = _request(server, "GET", thread_url)["thread"]
            if not result["running"]:
                return result
            sleep(.02)
        pytest.fail("Fixture conversation did not finish")

    first = {"thread_id": thread["id"], "connection_id": identity["id"], "request_id": str(uuid4()),
             "text": "Earlier fixture message."}
    preview = _request(server, "POST", base + "/preview", body=first)
    _request(server, "POST", base + "/send", body={**first, "token": preview["token"], "confirm_external": True})
    assert settled()["messages"][-1]["text"] == "Fixture reply."

    next_request = {**first, "request_id": str(uuid4()), "text": "Only the next question."}
    legacy = _request(server, "POST", base + "/preview", body=next_request)
    assert json.loads(legacy["outgoing"])["user"]["previous_turns"] == [
        {"text": first["text"], "reply": "Fixture reply."}
    ]
    narrowed = {**next_request, "include_history": False}
    assert _request(server, "POST", base + "/preview", body={**narrowed, "include_history": 0}, status=400) == {
        "error": "conversation.invalid"
    }
    assert _request(server, "POST", base + "/send",
                    body={**narrowed, "token": legacy["token"], "confirm_external": True}, status=409) == {
        "error": "conversation.stale"
    }
    current = _request(server, "POST", base + "/preview", body=narrowed)
    outgoing = json.loads(current["outgoing"])
    assert "previous_turns" not in outgoing["user"]
    assert outgoing["user"]["selected_node"]["formalization"] == node["formalization"]
    accepted = {**narrowed, "token": current["token"], "confirm_external": True}
    _request(server, "POST", base + "/send", body=accepted)
    assert [message["text"] for message in settled()["messages"]] == [
        first["text"], "Fixture reply.", next_request["text"], "Fixture reply.",
    ]
    assert sent[-1] == outgoing
    _request(server, "POST", base + "/send", body=accepted)
    assert len(sent) == 2
    assert _request(server, "POST", base + "/send", body={**accepted, "include_history": True}, status=409) == {
        "error": "conversation.stale"
    }
