# SPDX-License-Identifier: Apache-2.0
"""Exercise every current workbench API route with an actual project database."""

from hashlib import sha256
from http.client import HTTPConnection
import json
import os
from threading import Thread
from uuid import uuid4

import pytest

from research_multiplier import general_lean, production
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import formalization, intake
from tests._native_db import disposable_project_logins


pytestmark = pytest.mark.postgres

ACTOR = "Workbench fixture"
REASON = "Reviewed the synthetic fixture."
TITLE = "Restricted workbench fixture"


def _document(value):
    return json.dumps(value).encode()


def _approved_statement():
    target = _document({
        "title": TITLE,
        "domain": "lean-core",
        "sensitivity": "restricted",
        "statement": "Zero is equal to itself.",
        "formal": {"lean": {"proposition": "(0 : Nat) = 0"}},
    })
    starting_basis = _document({"items": [{
        "kind": "hypothesis",
        "natural": "The fixture assumes True.",
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
def workbench(tmp_path, monkeypatch):
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


def _request(server, method, path, *, body=None, status=200, headers=None):
    headers = {"X-RM-Session": server.session, **(headers or {})}
    if method == "POST":
        headers.update({"Origin": server.origin, "Content-Type": "application/json"})
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=20)
    try:
        connection.request(method, path, body=None if body is None else _document(body), headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == status, (method, path, response.status)
        return payload
    finally:
        connection.close()


def test_workbench_api_reads_and_persists_project_records(workbench):
    server, claim_id, statement_id, evidence_id = workbench
    claims = _request(server, "GET", "/api/claims")
    assert claims["claim_id"] == claim_id
    assert any(claim["id"] == claim_id and claim["title"] == TITLE for claim in claims["claims"])

    atlas = _request(server, "GET", "/api/atlas")
    assert any(claim["id"] == claim_id and claim["nodes"] for claim in atlas["claims"])

    doctor = _request(server, "GET", "/api/doctor?fresh=1")
    assert any(check["id"] == "workspace" and check["status"] == "ok" for check in doctor["checks"])

    graph = _request(server, "GET", f"/api/graph?claim={claim_id}")
    assert graph["claim"]["id"] == claim_id and graph["claim"]["statement_id"] == statement_id
    goal = next(node for node in graph["nodes"] if node["kind"] == "goal")
    premise = next(node for node in graph["nodes"] if node["kind"] == "hypothesis")
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
