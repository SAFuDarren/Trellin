# SPDX-License-Identifier: Apache-2.0
"""Consent and durable conversation regressions against real project bindings."""

from contextlib import nullcontext
import json
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

from research_multiplier.model_adapters import ModelConfig
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workflows import workbench_conversations
from tests.postgres.test_workbench_routes import _request, workbench  # noqa: F401


pytestmark = pytest.mark.postgres


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
    assert thread["claim_id"] is None and thread["messages"] == []
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
