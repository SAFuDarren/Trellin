# SPDX-License-Identifier: Apache-2.0
"""Exercise every current workbench API route with an actual project database."""

from hashlib import sha256
from http.client import HTTPConnection
from importlib.resources import files
import json
import os
from threading import Thread
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
import pytest

from research_multiplier import general_lean, production
from research_multiplier.cli import main
from research_multiplier.models import connections
from research_multiplier.policy import lean_budget
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.lean_progress import hold_lean_pass, pending_lean_passes
from research_multiplier.storage.postgres import DatabaseOperationError, Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import (
    formalization, intake, research_plans, runtime, workbench_project_canvas, workbench_proof_drafts,
    workbench_proof_tasks,
)
from tests._native_db import disposable_project_logins


pytestmark = pytest.mark.postgres

ACTOR = "Workbench fixture"
REASON = "Reviewed the synthetic fixture."
TITLE = "Restricted workbench fixture"


def _document(value):
    return json.dumps(value).encode()


def _approved_statement(*, title=TITLE, sensitivity="restricted"):
    target = _document({
        "title": title,
        "domain": "lean-core",
        "sensitivity": sensitivity,
        "statement": "Zero is equal to itself." if sensitivity == "restricted" else "One is equal to itself.",
        "formal": {"lean": {"proposition": "(0 : Nat) = 0" if sensitivity == "restricted" else "(1 : Nat) = 1"}},
    })
    starting_basis = _document({"items": [{
        "kind": "hypothesis",
        "natural": "The fixture assumes True." if sensitivity == "restricted" else "The public fixture uses True.",
        "formal": {"lean": {"proposition": "True"}},
    }]})
    with Store() as store:
        preview = intake.preview_target(store, target)
        frozen = intake.freeze_target(store, target, preview["target_sha256"], ACTOR, REASON)
        draft = intake.draft(store, starting_basis, target_id=frozen["id"])
        intake.confirm(store, draft["id"], draft["intake_sha256"],
                       {"i1": {"action": "accept", "reason": REASON}}, ACTOR, REASON)

        packet_id = intake.target(store, frozen["id"])["packet_id"]
        packet = formalization.read(store, "id", packet_id)
        contract = store.frozen(packet["claim_id"])
        goal, hypotheses = general_lean._target(json.loads(contract["contract_bytes"]))
        source = general_lean._type_source(goal, hypotheses).encode()
        # The statement has synthetic runtime hashes and receives no Lean receipt.
        fixture_hash = sha256(b"workbench fixture environment").hexdigest()
        body = {
            "theorem": general_lean.THEOREM,
            "candidate_theorem": general_lean.CANDIDATE,
            "checker": general_lean.CHECKER,
            "theorem_type": goal,
            "trusted_source": Artifacts(paths.object_store(), workspace=False).put_bytes(source),
            "environment": {
                "toolchain": general_lean.TOOLCHAIN,
                "sha256": fixture_hash,
                "inputs": {
                    "driver": fixture_hash,
                    "compiler": fixture_hash,
                    "native_runtime_policy": "native-lean-v1",
                    "native_runtime_sha256": fixture_hash,
                },
            },
            "scope": {
                "principal_packet_sha256": packet["packet_sha256"],
                "claim_contract_sha256": contract["contract_sha256"],
                "lean_hypotheses": hypotheses,
            },
        }
        statement = store.query("human", "SELECT * FROM rm.propose_lean_core_statement(%s,%s)",
                                packet_id, store.json(body))[0]
        store.query("human", "SELECT rm.confirm_lean_statement(%s,%s,%s,%s,%s)",
                    statement["id"], statement["version"], statement["statement_sha256"], ACTOR, REASON)
    return str(packet["claim_id"]), str(statement["id"])


def _pending_evidence(workspace):
    packet = formalization.draft(
        {"vertices": ["a", "b"], "edges": [["a", "b"]], "colors": 2},
        "Synthetic two-colourable graph",
        domain="graph-coloring",
    )
    formalization.confirm(packet["id"], packet["version"], packet["packet_sha256"], [], ACTOR, REASON)
    directory, _ = production.run_formalized(packet["id"], workspace / "runs" / "claims", strategy="program")
    return json.loads((directory / "run.json").read_text())["evidence_id"]


@pytest.fixture
def workbench(tmp_path, monkeypatch, request):
    with disposable_project_logins(revision=getattr(request, "param", "head")) as logins:
        for name in tuple(os.environ):
            if name.startswith("RM_"):
                monkeypatch.delenv(name)
        for name, value in logins.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        workspace = paths.initialize_workspace(tmp_path / "project")
        with paths.use_workspace(workspace, strict=True):
            bind_workspace(workspace)
            claim_id, statement_id = _approved_statement()
            evidence_id = _pending_evidence(workspace)
        with WorkbenchServer(workspace, claim_id=claim_id, port=0) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server, claim_id, statement_id, evidence_id
            finally:
                server.shutdown()
                thread.join()


def _request(server, method, path, *, body=None, status=200, headers=None, response_headers=None):
    headers = {"X-RM-Session": server.session, **(headers or {})}
    if method == "POST":
        headers.update({"Origin": server.origin, "Content-Type": "application/json"})
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=20)
    try:
        connection.request(method, path, body=None if body is None else _document(body), headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == status, (method, path, response.status)
        if response_headers is not None:
            response_headers.update(response.getheaders())
        return payload
    finally:
        connection.close()


def test_workbench_canvas_links_leave_recorded_proof_dependencies_intact(workbench, tmp_path, monkeypatch):
    server, claim, *_ = workbench
    monkeypatch.setattr(server, "project_canvas",
                        workbench_project_canvas.ProjectCanvas(server.workspace, settings_file=tmp_path / "canvas.sqlite3"))
    with paths.use_workspace(server.workspace, strict=True):
        other, _ = _approved_statement(title="Second canvas fixture", sensitivity="public")
        with Store() as store:
            recent = store.query("human", """
                SELECT rm.create_claim(
                    c.contract_snapshot || jsonb_build_object(
                        'claim_id', gen_random_uuid(), 'title', 'Newer canvas fixture ' || numbered.number
                    )
                ) AS id
                FROM rm.claims c CROSS JOIN generate_series(1, 100) numbered(number)
                WHERE c.id = %s
            """, claim)
            store.query("human", """
                SELECT rm.freeze_claim(id, %s, %s) FROM unnest(%s::uuid[]) AS copies(id)
            """, ACTOR, REASON, [row["id"] for row in recent])
    endpoint = "/api/project-canvas"
    recent_claims = _request(server, "GET", "/api/claims")["claims"]
    project_claims = _request(server, "GET", endpoint + "/claims")["claims"]
    assert len(recent_claims) == 100
    assert claim not in {item["id"] for item in recent_claims}
    assert {item["id"] for item in project_claims} >= {claim, other}
    assert [{key: value for key, value in item.items() if key != "obligations"}
            for item in project_claims[:100]] == recent_claims
    assert all(item["obligations"] == {"verified": 0, "total": 0} for item in project_claims)
    before = _request(server, "GET", f"/api/graph?claim={claim}")
    absent = {"action": "link", "source": claim, "target": str(uuid4())}
    assert _request(server, "POST", endpoint, body=absent, status=409) == {"error": "project_canvas.missing"}
    source, target = sorted((claim, other))
    assert _request(server, "POST", endpoint, body={**absent, "target": other})["links"] == [
        {"source": source, "target": target},
    ]
    after = _request(server, "GET", f"/api/graph?claim={claim}")
    assert after["nodes"] == before["nodes"]
    assert after["edges"] == before["edges"]


@pytest.mark.parametrize(("provider", "family"), [("codex-cli", "openai"), ("claude-code", "anthropic")])
def test_connected_proof_policy_checks_model_scope_and_budget(workbench, monkeypatch, provider, family):
    server, restricted_claim, *_ = workbench
    with paths.use_workspace(server.workspace, strict=True):
        claim, statement_id = _approved_statement(title="Public connected proof fixture", sensitivity="public")
    identity = {"id": str(uuid4()), "provider": provider, "model": "native-default", "fingerprint": "a" * 64}
    monkeypatch.setattr(workbench_proof_tasks, "_public_model_identity",
                        lambda identifier: identity if identifier == identity["id"] else None)
    selection = {key: identity[key] for key in ("id", "fingerprint")}
    view = server.proof_task(UUID(claim))
    assert view["external_model_allowed"] is True
    document = {"claim_id": claim, "token": view["scope"]["token"], "model_rounds": 1, "reason": REASON,
                "public_model": selection, "confirm_external": True}

    for unexpected, error in (
        ({"token": "b" * 64}, "stale"),
        ({"confirm_external": False}, "invalid"),
        ({"public_model": {**selection, "fingerprint": "b" * 64}}, "external_unavailable"),
    ):
        with pytest.raises(workbench_proof_tasks.ProofTaskError, match=rf"proof_task\.{error}"):
            server.write(workbench_proof_tasks.start, {**document, **unexpected})
    restricted = server.proof_task(UUID(restricted_claim))
    legacy = {"claim_id": restricted_claim, "token": restricted["scope"]["token"], "reason": REASON}
    with pytest.raises(workbench_proof_tasks.ProofTaskError, match="proof_task.external_unavailable"):
        server.write(workbench_proof_tasks.start, {**document, **legacy})
    legacy_id = UUID(server.write(workbench_proof_tasks.start, {**legacy, "model_rounds": 0})["policy_id"])
    policy_id = UUID(server.write(workbench_proof_tasks.start, document)["policy_id"])
    assert server.proof_task(UUID(claim))["policy"]["public_model"] == identity

    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        assert [item.policy_id for item in pending_lean_passes(store, 8, [policy_id, legacy_id])] == [legacy_id]
        assert pending_lean_passes(store, 8, None, public_model_policies=True) == []
        [selected] = pending_lean_passes(store, 8, [policy_id, legacy_id], public_model_policies=True)
        assert selected.policy_id == policy_id and selected.statement_id == UUID(statement_id)
        assert store.query("scheduler", "SELECT rm.lock_lean_progress(%s,%s) AS acquired",
                           policy_id, selected.inputs_sha256) == [{"acquired": False}]
        with hold_lean_pass(store, selected) as held:
            assert held is not None and held.settings().public_model == identity

        [statement] = store.query("human", "SELECT * FROM rm.lean_statement_status WHERE id = %s", statement_id)
        artifacts = Artifacts(paths.object_store(), workspace=False)
        proof = artifacts.put({"action": "proof", "proof": {"rfl": True}})
        body = statement["body"]
        subject = {"claim_id": claim, "contract_sha256": statement["contract_sha256"]}
        payload = {"lean_author": True, "lean_search": True, "formalization_id": str(statement["packet_id"]),
                   "formalization_sha256": body["scope"]["principal_packet_sha256"],
                   "author_statement_id": statement_id, "author_statement_sha256": statement["statement_sha256"],
                   "lean_progress_policy_id": str(policy_id), "public_model": identity, "input": proof}
        for unexpected in ({"public_model": {**identity, "provider": "anthropic"}},
                           {"author_statement_sha256": "b" * 64},
                           {"lean_progress_policy_id": str(legacy_id)}):
            with pytest.raises(DatabaseOperationError) as blocked:
                runtime.schedule(store, subject, "prove", {**payload, **unexpected}, 120, str(uuid4()), model_calls=1)
            assert blocked.value.state == "23514"

        job = runtime.schedule(store, subject, "prove", payload, 120, str(uuid4()), model_calls=1)
        [lease] = lean_budget.claim(store, "connected-fixture", 120, job["id"], retry=False)
        generation = lease["lease_generation"]
        model = {"provider": provider, "id": "reported-fixture", "family": family, "prompt_sha256": "c" * 64,
                 "parameters": {"requested_model": "native-default", "model_id_reported": True},
                 "input_tokens": 20, "output_tokens": 8}
        proposal = {"job_id": str(job["id"]), "subject": subject, "generation": generation, "role": "prove",
                    "context_sha256": proof["sha256"], "harness_version": runtime.HARNESS, "route_key": "public-v0a",
                    "finding": "numeric_observation", "artifact": proof, "transcript": proof}
        for unexpected in ({"provider": "anthropic"}, {"parameters": {"requested_model": "another-model"}}):
            with pytest.raises(DatabaseOperationError) as blocked:
                store.submit("prove", job["id"], "connected-fixture", generation,
                             {**proposal, "model": {**model, **unexpected}})
            assert blocked.value.state == "23514"
        store.submit("prove", job["id"], "connected-fixture", generation, {**proposal, "model": model})
        result = store.latest_attempt(job["id"])
        assert result["model_provider"] == provider and result["model_id"] == "reported-fixture"
        assert result["finding"] == "numeric_observation"

        second = runtime.schedule(store, subject, "prove", payload, 120, str(uuid4()), model_calls=1)
        with pytest.raises(lean_budget.Exhausted):
            lean_budget.claim(store, "connected-fixture", 120, second["id"], retry=False)
        server.write(workbench_proof_tasks.stop, {"claim_id": claim, "policy_id": str(policy_id), "reason": REASON})
        with pytest.raises(DatabaseOperationError) as blocked:
            lean_budget.claim(store, "connected-fixture", 120, second["id"], retry=False)
        assert blocked.value.state == "23514"
        assert pending_lean_passes(store, 1, [policy_id], public_model_policies=True) == []


def test_removing_a_connection_keeps_queued_proof_bound_to_its_original_model(workbench, monkeypatch, tmp_path):
    server, *_ = workbench
    monkeypatch.setattr(connections, "_settings_file", lambda: tmp_path / "models.sqlite")
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    executable.chmod(0o700)
    registry = connections.Registry()
    original = registry.add({"provider": "codex-cli", "model": "fixture-original", "binary": str(executable)})
    original_identity = registry.identity(original["id"])
    with paths.use_workspace(server.workspace, strict=True):
        claim, _ = _approved_statement(title="Queued connection replacement fixture", sensitivity="public")

    view = server.proof_task(UUID(claim))
    document = {"claim_id": claim, "token": view["scope"]["token"], "model_rounds": 1, "reason": REASON,
                "public_model": {key: original_identity[key] for key in ("id", "fingerprint")},
                "confirm_external": True}
    policy = server.write(workbench_proof_tasks.start, document)["policy_id"]

    replacement = registry.add({"provider": "codex-cli", "model": "fixture-replacement", "binary": str(executable)})
    replacement_identity = registry.identity(replacement["id"])
    registry.remove(original["id"])
    waiting = server.proof_task(UUID(claim))
    assert waiting["state"] == "waiting"
    assert waiting["blocker"] == "proof_task.external_unavailable"
    assert waiting["policy"]["id"] == policy and waiting["policy"]["public_model"] == original_identity

    new_document = {**document, "public_model": {key: replacement_identity[key] for key in ("id", "fingerprint")}}
    with pytest.raises(workbench_proof_tasks.ProofTaskError, match=r"proof_task\.external_unavailable"):
        server.write(workbench_proof_tasks.start, new_document)
    server.write(workbench_proof_tasks.stop, {"claim_id": claim, "policy_id": policy, "reason": REASON})
    new_policy = server.write(workbench_proof_tasks.start, new_document)["policy_id"]
    assert new_policy != policy
    assert server.proof_task(UUID(claim))["policy"]["public_model"] == replacement_identity


def test_proof_draft_reopens_and_is_consumed_only_by_exact_queue_request(workbench):
    server, claim, statement, _ = workbench
    node = "goal:" + claim
    path = f"/api/proof-drafts?claim_id={claim}&node_id={node}"
    activity = "/api/activity?claim=" + claim
    assert _request(server, "GET", activity)["proof_draft_available"]
    assert _request(server, "GET", path) == {"draft": None}

    text = "  Check the fixture assumption first.  "
    note = {"claim_id": claim, "node_id": node}
    record = _request(server, "POST", "/api/proof-drafts",
                      body={**note, "expected_revision": None, "text": text})["draft"]
    assert record["text"] == text
    with WorkbenchServer(server.workspace, port=0) as reopened:
        saved = reopened.read(workbench_proof_drafts.read, UUID(claim), node)["draft"]
        assert saved["revision"] == UUID(record["revision"])
        assert saved["text"] == text
    assert not _request(server, "GET", activity)["guidance"]

    queue = {**note, "statement_id": statement, "request_id": str(uuid4()),
             "text": text, "draft_revision": record["revision"]}
    _request(server, "POST", "/api/guidance", body={**queue, "statement_id": str(uuid4())}, status=409)
    assert _request(server, "GET", path) == {"draft": record}
    receipt = _request(server, "POST", "/api/guidance", body=queue)
    assert receipt["draft_consumed"] and receipt["text"] == text.strip()
    assert _request(server, "GET", path) == {"draft": None}

    updated = _request(server, "POST", "/api/proof-drafts",
                       body={**note, "expected_revision": None, "text": text})["draft"]
    repeated = _request(server, "POST", "/api/guidance", body=queue)
    assert repeated["id"] == receipt["id"] and repeated["draft_consumed"] is False
    assert _request(server, "GET", path) == {"draft": updated}
    response = _request(server, "POST", "/api/proof-drafts/discard",
                        body={**note, "expected_revision": record["revision"]}, status=409)
    assert response["error"] == "proof_draft_conflict"
    assert _request(server, "POST", "/api/proof-drafts/discard",
                    body={**note, "expected_revision": updated["revision"]}) == {"draft": None}


def test_proof_draft_can_be_saved_before_lean_statement_confirmation(workbench):
    server, *_ = workbench
    target = _document({
        "title": "Unconfirmed proof draft fixture", "domain": "lean-core", "sensitivity": "public",
        "statement": "Every natural number equals itself.",
        "formal": {"lean": {"proposition": "∀ n : Nat, n = n"}},
    })
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        preview = intake.preview_target(store, target)
        frozen = intake.freeze_target(store, target, preview["target_sha256"], ACTOR, REASON)
        draft = intake.draft(store, b'{"items": []}', target_id=frozen["id"])
        intake.confirm(store, draft["id"], draft["intake_sha256"], {}, ACTOR, REASON)
        claim = str(intake.target(store, frozen["id"])["claim_id"])

    path = "/api/activity?claim=" + claim
    assert _request(server, "GET", path)["guidance_blocker"] == "scope"
    node = "goal:" + claim
    body = {"claim_id": claim, "node_id": node, "expected_revision": None, "text": "Check reflexivity."}
    _request(server, "POST", "/api/proof-drafts", body={**body, "node_id": "other-node"}, status=404)
    record = _request(server, "POST", "/api/proof-drafts", body=body)
    assert record["draft"]["text"] == body["text"]
    assert _request(server, "GET", f"/api/proof-drafts?claim_id={claim}&node_id={node}") == record
    assert _request(server, "GET", path)["guidance"] == []


@pytest.mark.parametrize("workbench", ["0081"], indirect=True)
def test_old_workbench_detects_unavailable_draft_storage(workbench):
    server, claim, _, _ = workbench
    assert not _request(server, "GET", "/api/activity?claim=" + claim)["proof_draft_available"]
    reply = _request(server, "GET", f"/api/proof-drafts?claim_id={claim}&node_id=goal:{claim}", status=503)
    assert reply == {"error": "proof_draft_unavailable"}


def test_handoff_previews_selected_scope_and_rejects_changed_notes(workbench, capsys, tmp_path):
    server, claim, statement, _ = workbench
    command = ["--workspace", str(server.workspace), "research", "handoff", "--claim", claim]
    assert main(command) == 0
    preview = json.loads(capsys.readouterr().out)
    [selected] = preview["snapshot"]["claims"]
    assert selected["graph"]["claim"]["id"] == claim
    assert "The fixture assumes True." in json.dumps(selected["scope"]["hypotheses"])
    assert "postgresql://" not in json.dumps(preview)

    server.add_guidance(UUID(claim), UUID(statement), uuid4(), "goal:" + claim, "Check this next.", "local", False)
    output = tmp_path / "handoff"
    assert main([*command, "--out", str(output), "--confirm", preview["snapshot_sha256"]]) == 2
    assert not output.exists()
    capsys.readouterr()

    assert main(command) == 0
    revised = json.loads(capsys.readouterr().out)
    assert revised["snapshot_sha256"] != preview["snapshot_sha256"]
    assert main([*command, "--out", str(output), "--confirm", revised["snapshot_sha256"]]) == 0
    for name in ("handoff.html", "handoff.md"):
        report = (output / name).read_text()
        assert "Check this next." in report
        assert "Synthetic two-colourable graph" not in report


def test_versioned_claims_matches_the_contract_and_legacy_snapshot(workbench, monkeypatch, capsys):
    server, claim_id, *_ = workbench
    workspace_id = paths.workspace_id(server.workspace)
    path = f"/api/v1/workspaces/{workspace_id}/claims"
    schemas = files("research_multiplier").joinpath("schemas")
    openapi = json.loads(schemas.joinpath("workbench.openapi.json").read_text(encoding="utf-8"))
    contract = openapi["paths"]["/api/v1/workspaces/{workspace_id}/claims"]["get"]
    success_schema = contract["responses"]["200"]["content"]["application/json"]["schema"]
    snapshot = Draft202012Validator({**success_schema, "components": openapi["components"]},
                                    format_checker=FormatChecker())
    error_contract = Draft202012Validator(json.loads(schemas.joinpath("error.schema.json").read_text(encoding="utf-8")))

    legacy = _request(server, "GET", "/api/claims")
    headers = {}
    current = _request(server, "GET", path, response_headers=headers)
    snapshot.validate(current)
    assert current == {
        "items": legacy["claims"], "next_cursor": None, "claim_id": legacy["claim_id"], "claim_codes": True,
    }
    assert legacy["claim_id"] == claim_id and legacy["claim_codes"] is True
    assert any(item["id"] == claim_id and item["title"] == TITLE for item in current["items"])
    assert UUID(headers["X-Request-Id"])
    entry = json.loads(capsys.readouterr().err)
    assert entry.pop("duration_ms") >= 0
    assert entry == {"route": "/api/v1/workspaces/{workspace_id}/claims", "status": 200,
                     "request_id": headers["X-Request-Id"]}

    for target, status, code, request_headers in (
        (path.replace(str(workspace_id), str(uuid4())), 404, "claims.not_found", {}),
        (path, 401, "request.unauthenticated", {"X-RM-Session": "wrong"}),
        (path, 403, "request.forbidden", {"Origin": "http://foreign.example"}),
        (path + "?cursor=unexpected", 400, "claims.invalid_query", {}),
    ):
        response_headers = {}
        failure = _request(server, "GET", target, status=status, headers=request_headers,
                           response_headers=response_headers)
        error_contract.validate(failure)
        assert str(status) in contract["responses"]
        assert failure["error"]["code"] == code
        assert failure["error"]["request_id"] == response_headers["X-Request-Id"]
        assert TITLE not in json.dumps(failure)

    monkeypatch.setattr(server, "claim_id", None)
    unselected = _request(server, "GET", path)
    snapshot.validate(unselected)
    assert unselected == {**current, "claim_id": None}


def test_workbench_api_reads_and_persists_project_records(workbench):
    server, claim_id, statement_id, evidence_id = workbench
    claims = _request(server, "GET", "/api/claims")
    assert claims["claim_id"] == claim_id
    assert any(claim["id"] == claim_id and claim["title"] == TITLE for claim in claims["claims"])

    atlas = _request(server, "GET", "/api/atlas")
    atlas_claim = next(claim for claim in atlas["claims"] if claim["id"] == claim_id)
    atlas_nodes = {node["id"]: node for node in atlas_claim["nodes"]}

    doctor = _request(server, "GET", "/api/doctor?fresh=1")
    assert any(check["id"] == "workspace" and check["status"] == "ok" for check in doctor["checks"])

    graph = _request(server, "GET", f"/api/graph?claim={claim_id}")
    assert graph["claim"]["id"] == claim_id and graph["claim"]["statement_id"] == statement_id
    goal = next(node for node in graph["nodes"] if node["kind"] == "goal")
    premise = next(node for node in graph["nodes"] if node["kind"] == "hypothesis")
    for node in (goal, premise):
        assert atlas_nodes[node["id"]]["title"] == node["title"]
        assert set(atlas_nodes[node["id"]]) == {"id", "kind", "status", "title"}
    premise_id = premise["reference"]["basis_version_id"]

    assert claims["claim_codes"] is True
    initial_code = goal["code"]
    assert _request(server, "GET", f"/api/claim-code?code={initial_code}") == {
        "claim_id": claim_id, "node_id": goal["id"],
    }
    assert _request(server, "POST", "/api/claim-code", body={"claim_id": claim_id, "code": "REF35"}) == {
        "code": "REF35",
    }
    renamed = _request(server, "GET", f"/api/graph?claim={claim_id}")
    assert renamed["claim"]["code"] == "REF35"
    rebuilt = {node["id"]: node["code"] for node in renamed["nodes"]}
    assert rebuilt == {node["id"]: f"REF35-{node['code'].split('-')[1]}" for node in graph["nodes"]}
    for lookup_code in (initial_code, rebuilt[goal["id"]]):
        assert _request(server, "GET", f"/api/claim-code?code={lookup_code}") == {
            "claim_id": claim_id, "node_id": goal["id"],
        }

    impact = _request(server, "GET", f"/api/impact?claim={claim_id}&basis={premise_id}")
    assert impact["basis_version_id"] == premise_id and impact["includes_frozen_scope"]
    assert any(node["id"] == goal["id"] for node in impact["scope"])

    activity_path = f"/api/activity?claim={claim_id}"
    activity = _request(server, "GET", activity_path)
    assert activity["guidance_channels"] == ["local"] and not activity["guidance"]

    inbox = _request(server, "GET", "/api/inbox")
    assert any(item["kind"] == "review_evidence" and item["item_id"] == evidence_id for item in inbox["items"])

    library = _request(server, "GET", "/api/library")
    assert any(item["id"] == premise_id and item["kind"] == "hypothesis" for item in library["items"])

    team = _request(server, "GET", "/api/team")
    assert any(job["kind"] == "verify" for job in team["jobs"])
    assert not team["agents"]
    assert _request(server, "GET", "/api/progress")["policies"] == []
    assert _request(server, "GET", "/api/threads")["threads"] == []

    note = {
        "claim_id": claim_id, "statement_id": statement_id, "request_id": str(uuid4()),
        "node_id": goal["id"], "text": "Read this local test note.", "audience": "local",
    }
    # An unrelated statement must not authorize a write for this claim.
    _request(server, "POST", "/api/guidance", body={**note, "statement_id": str(uuid4())}, status=409)
    saved = _request(server, "POST", "/api/guidance", body=note)
    repeated = _request(server, "POST", "/api/guidance", body=note)
    assert saved["id"] == repeated["id"] == note["request_id"]
    _request(server, "POST", "/api/guidance", body={**note, "text": "Different content."}, status=409)
    messages = _request(server, "GET", activity_path)["guidance"]
    assert [(item["id"], item["text"]) for item in messages] == [(note["request_id"], note["text"])]
    threads = _request(server, "GET", "/api/threads")["threads"]
    thread = next(item for item in threads if item["claim_id"] == claim_id and item["node_id"] == goal["id"])
    assert any(event["kind"] == "guidance" and event["text"] == note["text"] for event in thread["events"])

    decision = _request(server, "POST", "/api/review", body={
        "evidence_id": evidence_id, "action": "approve", "actor": ACTOR, "reason": REASON,
    })
    assert decision["id"]
    after_review = _request(server, "GET", "/api/inbox")
    assert not any(item["kind"] == "review_evidence" and item["item_id"] == evidence_id
                   for item in after_review["items"])
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        persisted = store.query("human", "SELECT review_status FROM rm.evidence_foundation_status WHERE id = %s",
                                evidence_id)
    assert persisted == [{"review_status": "approved"}]


def test_guidance_and_clarification_emit_once_for_each_committed_change(workbench):
    server, claim_id, statement_id, evidence_id = workbench
    workspace_id = paths.workspace_id(server.workspace)
    local_user = paths.local_user_id(server.workspace)
    assert local_user is not None

    class Aborted(Exception):
        pass

    with paths.use_workspace(server.workspace, strict=True), Store() as store, Store() as reader:
        initial = reader.query("human", "SELECT rm.workspace_event_cursor(%s) AS head", workspace_id)[0]["head"]

        def events_after(cursor):
            return reader.query("human", "SELECT * FROM rm.read_workspace_events(%s,%s,100)", workspace_id, cursor)

        def write_guidance(request_id):
            return store.query("human", "SELECT rm.add_workbench_guidance(%s,%s,%s,%s,%s)", request_id,
                               claim_id, statement_id, f"goal:{claim_id}", "Synthetic guidance text.")

        def aborted_write():
            write_guidance(uuid4())
            assert not events_after(initial)
            raise Aborted

        with pytest.raises(Aborted):
            store.transaction("human", aborted_write)
        assert not events_after(initial)

        first = uuid4()

        def committed_writes():
            write_guidance(first)
            write_guidance(uuid4())
            assert not events_after(initial)

        store.transaction("human", committed_writes)
        write_guidance(first)
        guidance, = events_after(initial)
        assert (guidance["id"], guidance["type"], guidance["user_id"]) == (
            initial + 1, "workspace.changed", local_user,
        )
        assert (guidance["subject_kind"], guidance["subject_id"], guidance["data"]) == (
            "workspace", workspace_id, {"area": "activity", "claim_id": claim_id},
        )

        # The target-first Lean claim is frozen against legacy clarifications.
        # Use the other fixture claim that follows the original review workflow.
        evidence, = store.query("human", "SELECT claim_id FROM rm.evidence_foundation_status WHERE id = %s", evidence_id)
        packet = formalization.read(store, "claim_id", evidence["claim_id"])
        item_id = packet["packet"]["items"][0]["id"]
        request_id = formalization.request(store, packet["id"], item_id,
                                          "Synthetic research question.", "Synthetic research impact.")
        assert formalization.resolve(request_id, ACTOR, REASON) == request_id
        requested, resolved = events_after(guidance["id"])
        assert [row["id"] for row in (requested, resolved)] == [initial + 2, initial + 3]
        assert [row["type"] for row in (requested, resolved)] == ["workspace.changed"] * 2
        assert [row["user_id"] for row in (requested, resolved)] == [None, local_user]
        expected_data = {"area": "clarification", "claim_id": str(evidence["claim_id"])}
        assert [row["data"] for row in (requested, resolved)] == [expected_data, expected_data]


def test_versioned_review_records_the_bound_local_user_and_is_idempotent(workbench):
    server, _, _, evidence_id = workbench
    workspace_id = paths.workspace_id(server.workspace)
    local_user = paths.local_user_id(server.workspace)
    assert local_user is not None
    inbox = _request(server, "GET", "/api/inbox")
    reviewed_claim = next(item["claim_id"] for item in inbox["items"] if item["item_id"] == evidence_id)
    starting_cursor = inbox["workspace_events"]["cursor"]
    assert inbox["review_identity"] == {"workspace_id": str(workspace_id)}
    endpoint = f"/api/v1/workspaces/{workspace_id}/evidence/{evidence_id}/reviews"
    headers = {"Idempotency-Key": str(uuid4())}
    review = {"decision": "approve", "reason": REASON}
    bad = _request(server, "POST", endpoint, headers=headers, body={**review, "actor": "Not the user"}, status=400)
    assert bad["error"]["code"] == "request.invalid"
    created = _request(server, "POST", endpoint, headers=headers, body=review, status=201)
    assert created["user_id"] == str(local_user)
    assert _request(server, "POST", endpoint, headers=headers, body=review, status=201) == created
    different = _request(server, "POST", endpoint, headers=headers, body={**review, "decision": "return"}, status=409)
    assert different["error"]["code"] == "idempotency.conflict"
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        rows = store.query("human", """
            SELECT id::text, action, user_id, actor_ref FROM rm.decisions WHERE evidence_id = %s
        """, evidence_id)
        starting_events = store.query("human", "SELECT * FROM rm.read_workspace_events(%s,0,100)", workspace_id)
    assert rows == [{"id": created["id"], "action": "approve", "user_id": local_user,
                     "actor_ref": "Local researcher"}]

    setup_types = {row["type"] for row in starting_events if row["id"] <= int(starting_cursor)}
    assert {"claim.frozen", "basis.confirmed", "lean_statement.confirmed", "job.status_changed",
            "evidence.recorded"} <= setup_types
    event_id = str(int(starting_cursor) + 1)
    after_review = _request(server, "GET", "/api/inbox")
    assert after_review["workspace_events"] == {
        "workspace_id": str(workspace_id), "cursor": event_id, "coverage": "workspace-writes-v1",
    }
    reader = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        reader.request("GET", f"/api/v1/workspaces/{workspace_id}/events",
                       headers={"X-RM-Session": server.session, "Last-Event-ID": starting_cursor})
        response = reader.getresponse()
        assert response.status == 200 and response.getheader("Content-Type").startswith("text/event-stream")
        assert response.readline() == b": connected\n"
        assert response.readline() == b"\n"
        assert response.readline() == f"id: {event_id}\n".encode("ascii")
        assert response.readline() == b"event: evidence.reviewed\n"
        record = json.loads(response.readline().removeprefix(b"data: "))
        assert record == {
            "id": event_id, "type": "evidence.reviewed", "schema_version": 1, "workspace_id": str(workspace_id),
            "occurred_at": record["occurred_at"], "actor": {"kind": "user", "id": str(local_user)},
            "subject": {"kind": "evidence", "id": evidence_id},
            "data": {"decision_id": created["id"], "claim_id": reviewed_claim, "action": "approve"},
        }
        assert record["occurred_at"].endswith("Z")
    finally:
        reader.close()


def test_review_links_to_closed_or_other_project_evidence_cannot_record_a_decision(workbench):
    server, _, _, evidence = workbench
    workspace = paths.workspace_id(server.workspace)
    reason = {"decision": "approve", "reason": REASON}
    pending = _request(server, "GET", "/api/inbox")["items"]
    assert any(item["item_id"] == evidence for item in pending)

    def review(path, status):
        return _request(server, "POST", path, headers={"Idempotency-Key": str(uuid4())}, body=reason, status=status)

    wrong_workspace = review(f"/api/v1/workspaces/{uuid4()}/evidence/{evidence}/reviews", 404)
    missing_evidence = review(f"/api/v1/workspaces/{workspace}/evidence/{uuid4()}/reviews", 404)
    assert wrong_workspace["error"]["code"] == missing_evidence["error"]["code"] == "review.not_found"
    assert _request(server, "GET", "/api/inbox")["items"] == pending

    route = f"/api/v1/workspaces/{workspace}/evidence/{evidence}/reviews"
    created = review(route, 201)
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        store.promote(evidence, "Promote the accepted fixture result.")
    assert all(item["item_id"] != evidence for item in _request(server, "GET", "/api/inbox")["items"])
    assert review(route, 409)["error"]["code"] == "review.not_pending"
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        recorded = store.query("human", """
            SELECT id::text FROM rm.decisions WHERE evidence_id = %s AND action IN ('approve', 'return')
        """, evidence)
    assert recorded == [{"id": created["id"]}]


@pytest.mark.parametrize("include_legacy_name", [False, True])
def test_legacy_review_records_the_session_identity(workbench, include_legacy_name, monkeypatch):
    server, _, _, evidence_id = workbench
    body = {"evidence_id": evidence_id, "action": "approve", "reason": REASON}
    if include_legacy_name:
        body["actor"] = "Caller-selected name"

    with monkeypatch.context() as mismatched:
        different_user = uuid4()
        mismatched.setattr(paths, "local_user_id", lambda workspace: different_user)
        _request(server, "POST", "/api/review", body=body, status=403)
    created = _request(server, "POST", "/api/review", body=body)

    local_user = paths.local_user_id(server.workspace)
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        records = store.query("human", "SELECT id::text, actor_ref, user_id FROM rm.decisions WHERE evidence_id=%s",
                              evidence_id)
    assert records == [{"id": created["id"], "actor_ref": "Local researcher", "user_id": local_user}]


@pytest.mark.parametrize("workbench", ["0072"], indirect=True)
def test_legacy_review_keeps_the_original_contract_before_identity(workbench):
    server, _, _, evidence_id = workbench
    inbox = _request(server, "GET", "/api/inbox")
    assert "review_identity" not in inbox
    assert any(item["item_id"] == evidence_id for item in inbox["items"])
    body = {"evidence_id": evidence_id, "action": "approve", "reason": REASON}
    _request(server, "POST", "/api/review", body=body, status=400)
    created = _request(server, "POST", "/api/review", body={**body, "actor": ACTOR})
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        records = store.query("human", "SELECT id::text, actor_ref FROM rm.decisions WHERE evidence_id=%s", evidence_id)
    assert records == [{"id": created["id"], "actor_ref": ACTOR}]


def test_plan_records_creation_and_cancellation_identity(workbench):
    server, claim_id, _, _ = workbench
    user = paths.local_user_id(server.workspace)
    assert user is not None
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        packet = formalization.read(store, "claim_id", claim_id)
        plan = research_plans.create({
            "title": "Store draft attribution",
            "max_parallel": 1,
            "budget_seconds": 60,
            "steps": [{"key": "snapshot", "action": "snapshot", "packet_id": str(packet["id"])}],
        })
        assert plan["user_id"] == user and plan["cancelled_user_id"] is None
        assert research_plans.cancel(plan["id"], REASON)["id"] == plan["id"]
        queried = research_plans.show(plan["id"])
        assert queried["plan"]["user_id"] == queried["plan"]["cancelled_user_id"] == user
        assert queried["plan"]["body_sha256"] == plan["body_sha256"]
        assert queried["steps"][0]["status"] == "cancelled"


def test_opted_in_stream_notifies_lease_expiry_without_advancing_the_cursor(workbench):
    from research_multiplier.workflows import runtime, workspace_events

    server, claim_id, _, _ = workbench
    workspace_id = paths.workspace_id(server.workspace)
    with paths.use_workspace(server.workspace, strict=True), Store() as store:
        frozen = store.frozen(claim_id)
        subject = {"claim_id": claim_id, "contract_sha256": frozen["contract_sha256"]}
        job = runtime.schedule(store, subject, "small_case", {}, 20, "workbench-clock", model_calls=0)
        leased, = store.claim_job("small_case", "clock-fixture", 2, job["id"])
        assert leased["status"] == "running"
        head = workspace_events.capability(store)["workspace_events"]["cursor"]
    clock = HTTPConnection("127.0.0.1", server.server_port, timeout=6)
    try:
        clock.request("GET", f"/api/v1/workspaces/{workspace_id}/events", headers={
            "X-RM-Session": server.session,
            "Last-Event-ID": head,
            "X-RM-Workbench-Updates": "workspace-writes-v1",
        })
        response = clock.getresponse()
        assert response.status == 200
        assert response.readline() == b": connected\n"
        assert response.readline() == b"\n"
        assert response.readline() == b": workbench-runtime-changed\n"
        assert response.readline() == b"\n"
        assert response.readline() == b": workbench-runtime-changed\n"
        team = _request(server, "GET", "/api/team")
        job_state = next(item["state"] for item in team["jobs"] if item["id"] == str(job["id"]))
        assert job_state == "lease_expired"
        assert _request(server, "GET", "/api/inbox")["workspace_events"]["cursor"] == head
    finally:
        clock.close()
