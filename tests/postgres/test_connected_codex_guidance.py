# SPDX-License-Identifier: Apache-2.0
"""Exercise the proof guidance release, dispatch and receipt in PostgreSQL."""

from contextlib import nullcontext
import json
import os
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest

from research_multiplier import composed_lean
from research_multiplier.model_adapters import ModelConfig
from research_multiplier.models import cli_executor, connections
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.migrate import upgrade
from research_multiplier.storage.postgres import DatabaseOperationError, Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import (
    lean_search, research_progress, runtime, workbench_guidance, workbench_proof_tasks,
)
from tests._native_db import disposable_database, project_logins
from tests.postgres.test_workbench_routes import ACTOR, REASON, _approved_statement, _request, workbench  # noqa: F401


pytestmark = pytest.mark.postgres


def _approval(server, claim, identity):
    scope = server.proof_task(UUID(claim))["scope"]
    return {"claim_id": claim, "token": scope["token"], "model_rounds": 1, "reason": REASON,
            "public_model": {key: identity[key] for key in ("id", "fingerprint")}, "confirm_external": True}


def _note(claim, statement, text="Prefer the map through the original premise."):
    return {"claim_id": claim, "statement_id": statement, "node_id": "goal:" + claim,
            "text": text, "request_id": str(uuid4()), "audience": "codex", "confirm_release": True}


def _connection(monkeypatch, provider="codex-cli"):
    identity = {"id": str(uuid4()), "provider": provider, "model": "fixture-exact", "fingerprint": "a" * 64}
    config = ModelConfig(provider, identity["model"], "openai" if provider == "codex-cli" else "claude", "",
                         binary="/usr/bin/true")

    class FixtureRegistry:
        def identity(self, identifier):
            assert identifier == identity["id"]
            return identity

        def resolve(self, identifier):
            return config, self.identity(identifier)

        def use(self, identifier):
            return nullcontext(self.resolve(identifier))

    monkeypatch.setattr(connections, "Registry", FixtureRegistry)
    return identity


@pytest.mark.parametrize("provider", ["codex-cli", "claude-code"])
def test_only_connected_codex_reads_the_released_note_and_records_its_receipt(workbench, monkeypatch, provider):
    server, private_claim, private_statement, _ = workbench
    with paths.use_workspace(server.workspace, strict=True):
        claim, statement = _approved_statement(title="Public proof guidance fixture", sensitivity="public")
    identity = _connection(monkeypatch, provider)
    codex = provider == "codex-cli"
    received = []

    def answer(runtime_provider, binary, model, messages, timeout, **controls):
        assert (runtime_provider, model) == (provider, identity["model"])
        received.append(json.loads(messages[-1]["content"]))
        result = {"action": "blocked", "reason": "This fixture stops after one round."}
        if codex:
            result["workbench_reply"] = "I received the suggestion for this exact node."
        text = json.dumps(result)
        return {"model": model, "model_id_reported": True, "id": "fixture-call",
                "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 34, "completion_tokens": 14}}

    monkeypatch.setattr(cli_executor, "complete", answer)
    # This test isolates dispatch and its audit receipt. The model fixture stops
    # without proposing a mathematical proof, and no compiler result is faked.
    monkeypatch.setattr(lean_search, "_program_progress", lambda *a, **k: ({}, None, []))
    monkeypatch.setattr(lean_search, "_finite_decision", lambda *a, **k: None)
    monkeypatch.setattr(composed_lean, "select_runtime", lambda *a, **k: object())

    activity_path = "/api/activity?claim=" + claim
    assert _request(server, "GET", activity_path)["guidance_channels"] == ["codex"]
    queued = _note(claim, statement)
    _request(server, "POST", "/api/guidance", body={**queued, "confirm_release": False}, status=409)
    _request(server, "POST", "/api/guidance", body={**queued, "statement_id": str(uuid4())}, status=409)
    private = _note(private_claim, private_statement, "This local fixture note is not releasable.")
    _request(server, "POST", "/api/guidance", body=private, status=409)
    _request(server, "POST", "/api/guidance", body={**private, "audience": "local", "confirm_release": False})
    receipt = _request(server, "POST", "/api/guidance", body=queued)
    repeated = _request(server, "POST", "/api/guidance", body=queued)
    assert {key: repeated[key] for key in receipt} == receipt
    [pending] = _request(server, "GET", activity_path)["guidance"]
    assert pending["id"] == queued["request_id"] and pending["submitted_at"] is None
    assert not received
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        assert workbench_guidance.pending(store, statement, audience="codex") == []
        assert workbench_guidance.released(store, statement, [queued["request_id"]]) == []

    approval = _approval(server, claim, identity)
    # Approve independently of launching to exercise forged jobs at the normal
    # scheduling guard. Only the valid workbench action launches the worker.
    policy_id = server.write(workbench_proof_tasks.start, approval)["policy_id"]
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        [scope] = store.query("human", "SELECT * FROM rm.lean_statement_status WHERE id = %s", statement)
        subject = {"claim_id": claim, "contract_sha256": scope["contract_sha256"]}
        payload = {"lean_author": True, "lean_search": True, "formalization_id": str(scope["packet_id"]),
                   "formalization_sha256": scope["body"]["scope"]["principal_packet_sha256"],
                   "author_statement_id": statement, "author_statement_sha256": scope["statement_sha256"],
                   "lean_progress_policy_id": policy_id, "public_model": identity,
                   "input": Artifacts(paths.object_store(), workspace=False).put({"fixture": True}),
                   "workbench_guidance_ids": [queued["request_id"]], "workbench_guidance_model": identity["model"],
                   "workbench_focus_node_id": queued["node_id"]}
        denied_payloads = (
            {"workbench_guidance_ids": [private["request_id"]]},
            {"workbench_guidance_ids": [str(uuid4())]},
            {"workbench_guidance_ids": [queued["request_id"]] * 2},
            {"workbench_guidance_ids": queued["request_id"]},
            {"workbench_guidance_model": "another-model"},
            {"workbench_focus_node_id": private["node_id"]},
            {"author_statement_id": private_statement},
        )
        # Even otherwise valid Codex guidance is refused when the signed
        # connection belongs to a different provider.
        for change in denied_payloads if codex else ({},):
            with pytest.raises(DatabaseOperationError) as denied:
                runtime.schedule(store, subject, "prove", {**payload, **change}, 120, str(uuid4()), model_calls=1)
            assert denied.value.state == "23514"

    _request(server, "POST", "/api/proof-task/start", body=approval)
    deadline = monotonic() + 10
    while monotonic() < deadline and server._local_proof_worker()[0]:
        sleep(0.03)
    assert server._local_proof_worker() == (None, None)
    [request] = received
    assert private["text"] not in json.dumps(request)
    [sent] = _request(server, "GET", activity_path)["guidance"]
    assert sent["id"] == receipt["id"]
    if codex:
        assert request["workbench_guidance"] == [
            {"id": queued["request_id"], "node_id": queued["node_id"], "text": queued["text"]},
        ]
        assert sent["submitted_at"] and sent["attempt_id"]
        with paths.use_workspace(server.workspace, strict=True), Store() as store:
            assert workbench_guidance.pending(store, statement, audience="codex") == []
            workbench_guidance.submitted(store, statement, [queued["request_id"]], sent["attempt_id"])
            assert workbench_guidance.activity(store, claim)["guidance"][0]["attempt_id"] == UUID(sent["attempt_id"])
    else:
        assert "workbench_guidance" not in request and queued["text"] not in json.dumps(request)
        assert sent["submitted_at"] is None and sent["attempt_id"] is None
    server.write(workbench_proof_tasks.stop, {"claim_id": claim, "policy_id": policy_id, "reason": REASON})
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        assert workbench_guidance.released(store, statement, [queued["request_id"]]) == []
    assert len(received) == 1


def test_upgrade_preserves_notes_and_enables_waiting_connected_task(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        upgrade(dsn, "0080")
        with project_logins(dsn) as logins, monkeypatch.context() as environment:
            for name in tuple(os.environ):
                if name.startswith("RM_"):
                    environment.delenv(name)
            for name, value in logins.items():
                environment.setenv(name, value)
            environment.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            workspace = paths.initialize_workspace(tmp_path / "project")
            with paths.use_workspace(workspace, strict=True):
                bind_workspace(workspace)
                private_claim, private_statement = _approved_statement()
                claim, statement = _approved_statement(title="Old schema connected guidance", sensitivity="public")

            with WorkbenchServer(workspace, claim_id=claim, port=0) as server:
                activity = server.read(workbench_guidance.activity, UUID(claim))
                assert activity["guidance_blocker"] == "connected_schema"
                assert activity["guidance_channels"] == []
                identity = _connection(environment)
                server.write(workbench_proof_tasks.start, _approval(server, claim, identity))
                activity = server.read(workbench_guidance.activity, UUID(claim))
                assert activity["guidance_channels"] == []
                assert activity["guidance_blocker"] == "connected_schema"
                opinion = "Check the original premise before the next route."
                with pytest.raises(workbench_guidance.GuidanceError, match="Update this workspace"):
                    server.add_guidance(UUID(claim), UUID(statement), uuid4(), "goal:" + claim, opinion, "codex", True)
                with paths.use_workspace(workspace, strict=True), Store() as store:
                    [legacy] = store.query("human", "SELECT * FROM rm.propose_lean_progress(%s,%s,%s)", statement, 1, 0)
                    research_progress.approve(legacy["id"], legacy["policy_sha256"], ACTOR, REASON, store=store)
                    assert workbench_guidance.activity(store, UUID(claim))["guidance_channels"] == ["codex"]
                    research_progress.stop(legacy["id"], REASON, store=store)
                assert server.read(workbench_guidance.activity, UUID(claim))["guidance_blocker"] == "connected_schema"
                local_id = uuid4()
                server.add_guidance(UUID(private_claim), UUID(private_statement), local_id, "goal:" + private_claim,
                                    opinion, "local", False)
                upgrade(dsn, "0081")
                [retained] = server.read(workbench_guidance.activity, UUID(private_claim))["guidance"]
                assert retained["id"] == local_id and retained["text"] == opinion
                assert server.read(workbench_guidance.activity, UUID(claim))["guidance_channels"] == ["codex"]
                saved = server.add_guidance(UUID(claim), UUID(statement), uuid4(), "goal:" + claim, opinion, "codex", True)
                assert saved["audience"] == "codex"
