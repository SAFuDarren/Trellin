# SPDX-License-Identifier: Apache-2.0
"""Verify that Library revisions preserve the original statement and proof lineage."""

from concurrent.futures import ThreadPoolExecutor
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


REASON = "Reviewed the wording and Lean proposition."
NATURAL = "True implies that both conjuncts are true."
LEAN = "True ∧ True"
BASIS_WORDING = "The premise is true. " + "The fixture retains this original wording. " * 54


@pytest.fixture
def library_server(tmp_path, monkeypatch):
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


def request(server, route, document=None):
    headers = {"X-RM-Session": server.session}
    if document is not None:
        headers.update({"Origin": server.origin, "Content-Type": "application/json"})
    with_connection = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
    try:
        with_connection.request("GET" if document is None else "POST", route, headers=headers,
                                body=None if document is None else json.dumps(document).encode())
        response = with_connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        with_connection.close()


def sample(*, sensitivity="restricted"):
    target = {
        "title": "Frozen target revision fixture", "domain": "lean-core", "sensitivity": sensitivity,
        "statement": NATURAL, "formal": {"lean": {"proposition": LEAN}},
    }
    notes = {
        "items": [{"kind": "hypothesis", "natural": BASIS_WORDING, "formal": {"lean": {"proposition": "True"}}}],
    }
    with Store() as store:
        raw = json.dumps(target).encode()
        preview = intake.preview_target(store, raw)
        initial = intake.freeze_target(store, raw, preview["target_sha256"], "Fixture", REASON)
        draft = intake.draft(store, json.dumps(notes).encode(), target_id=initial["id"])
        intake.confirm(store, draft["id"], draft["intake_sha256"],
                       {"i1": {"action": "accept", "reason": REASON}}, "Fixture", REASON)
        frozen = intake.target(store, initial["id"])
        basis = store.query("human", """
            SELECT * FROM rm.proof_graph_frozen_basis(%s)
        """, frozen["packet_id"])
    items = {row["item_label"]: str(row["version_id"]) for row in basis}
    return frozen, items


def revision(server, identifier, natural, proposition):
    status, item = request(server, f"/api/library/item?version={identifier}")
    assert status == 200 and item["available"], item
    return {"id": identifier, "sha256": item["sha256"], "natural": natural, "lean": proposition}, item


def check_current_goal(frozen, workspace):
    prepared = general_lean.propose(frozen["packet_id"])
    lean_workflow.confirm(prepared["id"], prepared["version"], prepared["statement_sha256"], "Fixture", REASON)
    submission = {"format_version": 1, "proof": {"intro": {"count": 1, "body": {"and": [{"use": 0}, {"use": 0}]}}}}
    checked = general_lean.check(prepared["id"], submission, workspace / "runs")
    assert checked["accepted"]
    return checked["evidence_id"]


@pytest.mark.postgres
def test_revision_uses_exact_source_and_confirmation_fences_concurrent_windows(library_server):
    server = library_server
    frozen, identifiers = sample()
    changes, original_basis = revision(server, identifiers["H1"], "The premise is the trivial proposition.", "True")
    assert original_basis["natural"] == BASIS_WORDING and len(BASIS_WORDING) > 2000
    competing = {**changes, "natural": "The premise is always true."}
    status, first_preview = request(server, "/api/library/preview", changes)
    assert status == 200, first_preview
    status, other_preview = request(server, "/api/library/preview", competing)
    assert status == 200, other_preview
    assert first_preview["affected"]["claims"] == 1
    assert request(server, "/api/library/item?version=" + identifiers["H1"])[1] == original_basis
    invalid = {**changes, "token": "0" * 64, "reason": REASON}
    assert request(server, "/api/library/revise", invalid) == (409, {"error": "libraryEdit.stale"})
    assert len(request(server, "/api/library")[1]["items"]) == 2

    submissions = [
        {**changes, "token": first_preview["token"], "reason": REASON},
        {**competing, "token": other_preview["token"], "reason": REASON},
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda data: request(server, "/api/library/revise", data), submissions))
    assert sorted(status for status, _ in outcomes) == [200, 409], outcomes
    saved = next(payload for status, payload in outcomes if status == 200)
    assert saved["version"] == 2 and saved["id"] != identifiers["H1"]
    rows = {row["id"]: row for row in request(server, "/api/library")[1]["items"]}
    assert rows[identifiers["H1"]]["retired"] is True
    assert rows[saved["id"]]["admitted"] is True
    with Store() as store:
        assert not store.query("human", "SELECT basis_current FROM rm.research_goal_status WHERE id = %s",
                               frozen["goal_id"])[0]["basis_current"]


@pytest.mark.lean
def test_frozen_target_revision_keeps_evidence_and_revised_basis_marks_downstream(library_server):
    server = library_server
    frozen, identifiers = sample(sensitivity="public")
    evidence_id = check_current_goal(frozen, server.workspace)
    with Store() as store:
        store.review(evidence_id, "approve", "Fixture", REASON)
        store.promote(evidence_id, REASON)
        derived = store.query("verifier", "SELECT * FROM rm.publish_lean_core_theorem(%s)", evidence_id)[0]
    derived_changes, original_derived = revision(server, str(derived["id"]), "Two trivial conjuncts hold.", LEAN)
    assert original_derived["mode"] == "successor"
    assert original_derived["scope"]["antecedents"] == ["True"]
    status, preview = request(server, "/api/library/preview", derived_changes)
    assert status == 200 and preview["affected"]["checks"] == 0, preview
    status, successor = request(server, "/api/library/revise", {
        **derived_changes, "token": preview["token"], "reason": REASON,
    })
    assert status == 200, successor
    with Store() as store:
        row = store.query("human", "SELECT origin, body, lean_check_id FROM rm.formal_item_status WHERE id = %s",
                          successor["id"])[0]
        assert row["origin"] == "human" and row["lean_check_id"] is None
        assert row["body"]["source"]["version_id"] == str(derived["id"])
        assert row["body"]["formal"]["lean"]["antecedents"] == ["True"]
    changes, original = revision(server, identifiers["goal"], "Either trivial proposition is true.", "True ∨ True")
    assert original["lean"] == LEAN and original["natural"] == NATURAL
    status, preview = request(server, "/api/library/preview", changes)
    assert status == 200 and preview["affected"]["checks"] == preview["affected"]["receipts"] == 1, preview
    status, new_target = request(server, "/api/library/revise", {**changes, "token": preview["token"], "reason": REASON})
    assert status == 200, new_target
    assert request(server, "/api/library/revise", {**changes, "token": preview["token"], "reason": REASON})[0] == 409
    assert new_target["claim_id"] != str(frozen["claim_id"])
    assert new_target["version"] == 2
    library = {row["id"]: row for row in request(server, "/api/library")[1]["items"]}
    assert library[new_target["id"]]["statement_revision"] == 2
    assert library[new_target["id"]]["lean"] == "True ∨ True"
    assert library[identifiers["goal"]]["lean"] == LEAN and not library[identifiers["goal"]]["retired"]
    with Store() as store:
        claims = store.query("human", "SELECT source_id, target_id FROM rm.claim_edges WHERE kind = 'supersedes'")
        assert [(str(row["source_id"]), row["target_id"]) for row in claims] == [
            (new_target["claim_id"], frozen["claim_id"]),
        ]
        old_proof = store.query("human", "SELECT claim_id, needs_recheck FROM rm.evidence_foundation_status WHERE id = %s",
                                evidence_id)[0]
        assert old_proof == {"claim_id": frozen["claim_id"], "needs_recheck": False}
        revisions = store.query("human", """
            SELECT claim_id, body -> 'basis_version_ids' AS basis
            FROM rm.research_goal_status WHERE claim_id IN (%s, %s)
        """, frozen["claim_id"], new_target["claim_id"])
        assert [row["basis"] for row in revisions] == [[identifiers["H1"]], [identifiers["H1"]]]

    basis_changes, _ = revision(server, identifiers["H1"], "The premise has a new formalization.", "0 = 0")
    status, preview = request(server, "/api/library/preview", basis_changes)
    assert status == 200 and preview["affected"]["checks"] == preview["affected"]["receipts"] == 1, preview
    assert request(server, "/api/library/revise", {
        **basis_changes, "token": preview["token"], "reason": REASON,
    })[0] == 200
    with Store() as store:
        assert store.query("human", "SELECT needs_recheck FROM rm.evidence_foundation_status WHERE id = %s",
                           evidence_id)[0]["needs_recheck"]
