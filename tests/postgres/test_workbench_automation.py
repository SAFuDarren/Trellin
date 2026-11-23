# SPDX-License-Identifier: Apache-2.0
"""Automation rules are proposed, approved and stopped from the workbench with the existing rule engine (#94)."""

import json
import os

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.migrate import upgrade
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workflows import intake, workbench_automation as automation
from tests._native_db import disposable_database, project_logins

pytestmark = pytest.mark.postgres
REASON = "Reviewed the synthetic fixture."


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    with disposable_database() as dsn:
        upgrade(dsn)
        with project_logins(dsn) as logins:
            for name in tuple(os.environ):
                if name.startswith("RM_") and name != "RM_TEST_DSN":
                    monkeypatch.delenv(name)
            for name, value in logins.items():
                monkeypatch.setenv(name, value)
            monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
            project = paths.initialize_workspace(tmp_path / "project")
            with paths.use_workspace(project, strict=True):
                bind_workspace(project)
                yield project


def frozen_claim(store):
    target = {"title": "Automation fixture", "domain": "lean-core", "sensitivity": "restricted",
              "statement": "A synthetic statement.", "formal": {"lean": {"proposition": "True ∧ True"}}}
    raw = json.dumps(target).encode()
    saved = intake.freeze_target(store, raw, intake.preview_target(store, raw)["target_sha256"], "Fixture", REASON)
    notes = {"items": [{"kind": "hypothesis", "natural": "The automation premise is true.",
                        "formal": {"lean": {"proposition": "True"}}}]}
    draft = intake.draft(store, json.dumps(notes).encode(), target_id=saved["id"])
    intake.confirm(store, draft["id"], draft["intake_sha256"],
                   {"i1": {"action": "accept", "reason": REASON}}, "Fixture", REASON)
    return str(intake.target(store, saved["id"])["claim_id"])


def test_a_template_is_proposed_approved_exactly_and_stopped(workspace):
    with Store() as store:
        claim = frozen_claim(store)
        offered = {entry["id"]: entry["claims"] for entry in automation.templates(store)["templates"]}
        # Without a confirmed Lean statement only observation is available, and the reason is named.
        assert offered["lean-reuse"] == [{"claim_id": claim, "title": "Automation fixture", "missing": "lean_statement"}]
        assert offered["observe"] == [{"claim_id": claim, "title": "Automation fixture", "missing": None}]
        with pytest.raises(automation.AutomationError, match="unavailable_lean_statement"):
            store.transaction("human", lambda: automation.propose(store, {"claim_id": claim, "template": "lean-reuse"}))

        proposal = store.transaction("human", lambda: automation.propose(store, {"claim_id": claim, "template": "observe"}))
        assert proposal["claim_id"] == claim and proposal["template"] == "observe"
        assert not proposal["approved"] and proposal["actions"] == []
        # Proposing the same rule again returns the same proposal.
        again = store.transaction("human", lambda: automation.propose(store, {"claim_id": claim, "template": "observe"}))
        assert again["policy_id"] == proposal["policy_id"]

        with pytest.raises(automation.AutomationError, match="stale"):
            automation.approve(store, {"policy_id": proposal["policy_id"], "fingerprint": "0" * 64, "reason": REASON})
        approved = store.transaction("human", lambda: automation.approve(
            store, {"policy_id": proposal["policy_id"], "fingerprint": proposal["fingerprint"], "reason": REASON}))
        assert approved["approved"] and not approved["stopped"]

        stopped = store.transaction("human", lambda: automation.stop(
            store, {"policy_id": proposal["policy_id"], "reason": "No longer needed."}))
        assert stopped["stopped"]
        # The stopped rule keeps its record and cannot be approved again.
        with pytest.raises(automation.AutomationError, match="stopped"):
            automation.approve(store, {"policy_id": proposal["policy_id"], "fingerprint": proposal["fingerprint"],
                                       "reason": REASON})
