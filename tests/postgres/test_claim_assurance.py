# SPDX-License-Identifier: Apache-2.0
"""The project map reports how each claim was established and which claims it relies on (#342)."""

from http.client import HTTPConnection
import json
import os
from threading import Thread

import pytest

from research_multiplier import general_lean
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import intake, lean_workflow
from tests._native_db import disposable_project_logins

# Proving the origin claim needs the pinned Lean toolchain, so this runs in the Lean lane with PostgreSQL.
pytestmark = pytest.mark.lean
REASON = "Reviewed the synthetic fixture."


@pytest.fixture
def server(tmp_path, monkeypatch):
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
            with WorkbenchServer(workspace, port=0) as running:
                thread = Thread(target=running.serve_forever, daemon=True)
                thread.start()
                try:
                    yield running
                finally:
                    running.shutdown()
                    thread.join()


def project_claims(server):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
    try:
        connection.request("GET", "/api/project-canvas/claims", headers={"X-RM-Session": server.session})
        response = connection.getresponse()
        assert response.status == 200
        return {row["id"]: row for row in json.loads(response.read())["claims"]}
    finally:
        connection.close()


def frozen(title, proposition, sensitivity):
    target = {"title": title, "domain": "lean-core", "sensitivity": sensitivity,
              "statement": f"The {title.lower()} holds.", "formal": {"lean": {"proposition": proposition}}}
    # Each claim has its own wording, so neither the library nor the frozen-target guard confuses the two.
    notes = {"items": [{"kind": "hypothesis", "natural": f"The premise of the {title.lower()} is true.",
                        "formal": {"lean": {"proposition": "True"}}}]}
    with Store() as store:
        raw = json.dumps(target).encode()
        preview = intake.preview_target(store, raw)
        saved = intake.freeze_target(store, raw, preview["target_sha256"], "Fixture", REASON)
        draft = intake.draft(store, json.dumps(notes).encode(), target_id=saved["id"])
        intake.confirm(store, draft["id"], draft["intake_sha256"],
                       {"i1": {"action": "accept", "reason": REASON}}, "Fixture", REASON)
        return intake.target(store, saved["id"])


def approved_statement(packet_id):
    prepared = general_lean.propose(packet_id)
    lean_workflow.confirm(prepared["id"], prepared["version"], prepared["statement_sha256"], "Fixture", REASON)
    return prepared


def test_project_claims_report_assurance_and_the_claims_a_formalization_uses(server):
    origin = frozen("Origin statement", "True ∧ True", "public")
    destination = frozen("Destination statement", "True ∨ True", "restricted")
    origin_claim, destination_claim = str(origin["claim_id"]), str(destination["claim_id"])

    listed = project_claims(server)
    for claim in (origin_claim, destination_claim):
        assert listed[claim]["assurance"] == {"kind": None, "review": None, "supporting": []}
        assert listed[claim]["uses"] == []

    prepared = approved_statement(origin["packet_id"])
    submission = {"format_version": 1, "proof": {"intro": {"count": 1, "body": {"and": [{"use": 0}, {"use": 0}]}}}}
    checked = general_lean.check(prepared["id"], submission, server.workspace / "runs")
    assert checked["accepted"]
    evidence_id = checked["evidence_id"]
    # A verified proof that has not been promoted is supporting evidence, not how the claim was established.
    assert project_claims(server)[origin_claim]["assurance"] == {"kind": None, "review": None, "supporting": ["lean"]}

    with Store() as store:
        store.review(evidence_id, "approve", "Fixture", REASON)
        store.promote(evidence_id, REASON)
        theorem = store.query("verifier", "SELECT * FROM rm.publish_lean_core_theorem(%s)", evidence_id)[0]
    approved_statement(destination["packet_id"])
    with Store() as store:
        store.query("verifier", "SELECT * FROM rm.admit_lean_core_theorem(%s,%s)",
                    destination["packet_id"], theorem["id"])

    listed = project_claims(server)
    assert listed[origin_claim]["status"] == "proved"
    assert listed[origin_claim]["assurance"] == {"kind": "lean", "review": "approved", "supporting": []}
    # The origin's own published theorem is not a dependency of the origin.
    assert listed[origin_claim]["uses"] == []
    assert listed[destination_claim]["assurance"] == {"kind": None, "review": None, "supporting": []}
    assert listed[destination_claim]["uses"] == [origin_claim]
