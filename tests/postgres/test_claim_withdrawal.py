# SPDX-License-Identifier: Apache-2.0
"""A researcher withdraws a frozen claim that cannot proceed, and can restore it (#297)."""

import json
import os

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.migrate import upgrade
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workflows import claim_withdrawal, intake, proof_graph
from tests._native_db import disposable_database, project_logins

pytestmark = pytest.mark.postgres
REASON = "Reviewed the synthetic fixture."


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """Yield the disposable database's administrator DSN with a bound project workspace selected."""
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
                yield dsn


def frozen_claim(store, title, proposition):
    target = {"title": title, "domain": "lean-core", "sensitivity": "restricted",
              "statement": "A synthetic statement.", "formal": {"lean": {"proposition": proposition}}}
    raw = json.dumps(target).encode()
    preview = intake.preview_target(store, raw)
    saved = intake.freeze_target(store, raw, preview["target_sha256"], "Fixture", REASON)
    # Each claim gets its own premise wording, so the library does not reuse an approved item.
    notes = {"items": [{"kind": "hypothesis", "natural": f"The premise of the {title.lower()} is true.",
                        "formal": {"lean": {"proposition": "True"}}}]}
    draft = intake.draft(store, json.dumps(notes).encode(), target_id=saved["id"])
    intake.confirm(store, draft["id"], draft["intake_sha256"],
                   {"i1": {"action": "accept", "reason": REASON}}, "Fixture", REASON)
    return str(intake.target(store, saved["id"])["claim_id"])


def visible(store):
    listed = {str(row["id"]) for row in proof_graph.claims(store)}
    mapped = {str(row["id"]) for row in proof_graph.canvas_claim_index(store)["claims"]}
    assert listed == mapped
    return listed


def decisions(store, claim_id):
    return [row["action"] for row in store.query("human", """
        SELECT action FROM rm.decisions WHERE claim_id = %s ORDER BY sequence_no
    """, claim_id)]


def test_withdrawn_claims_leave_the_map_and_lists_and_can_be_restored(owner):
    with Store() as store:
        old = frozen_claim(store, "Original statement", "True ∧ True")
        corrected = frozen_claim(store, "Corrected statement", "True ∨ False")
        assert visible(store) == {old, corrected}

        with pytest.raises(ValueError, match="reason"):
            claim_withdrawal.withdraw(store, old, "Fixture", " ")
        with pytest.raises(ValueError, match="another current frozen"):
            claim_withdrawal.withdraw(store, old, "Fixture", REASON, superseded_by=old)

        withdrawn = claim_withdrawal.withdraw(store, old, "Fixture", "Replaced by the corrected wording.",
                                              superseded_by=corrected)
        assert withdrawn["status"] == "superseded"
        assert visible(store) == {corrected}
        listed = claim_withdrawal.withdrawn(store)
        assert [(str(row["id"]), str(row["superseded_by"]), row["reason"]) for row in listed] == [
            (old, corrected, "Replaced by the corrected wording."),
        ]
        # A withdrawn claim cannot be withdrawn again, and history keeps the replacement link.
        with pytest.raises(ValueError, match="only an open researcher claim"):
            claim_withdrawal.withdraw(store, old, "Fixture", REASON)
        edges = store.query("human", "SELECT source_id, kind::text AS kind FROM rm.claim_edges WHERE target_id = %s",
                            old)
        assert [(str(row["source_id"]), row["kind"]) for row in edges] == [(corrected, "supersedes")]

        restored = claim_withdrawal.restore(store, old, "Fixture", "The original wording is needed after all.")
        assert restored["status"] == "open"
        assert visible(store) == {old, corrected}
        assert claim_withdrawal.withdrawn(store) == []
        with pytest.raises(ValueError, match="only a withdrawn"):
            claim_withdrawal.restore(store, old, "Fixture", REASON)
        assert decisions(store, old)[-2:] == ["supersede", "restore"]

        # Without a replacement the claim is archived.
        assert claim_withdrawal.withdraw(store, corrected, "Fixture", REASON)["status"] == "archived"
        assert visible(store) == {old}
        assert decisions(store, corrected)[-1] == "archive"


def test_only_a_recorded_restore_returns_a_withdrawn_claim_to_open(owner):
    import psycopg

    with Store() as store:
        subject = frozen_claim(store, "Archived statement", "True ∧ True")
        claim_withdrawal.withdraw(store, subject, "Fixture", REASON)
    # Even the database owner cannot move the claim back without a restore decision.
    with psycopg.connect(owner, autocommit=True) as connection:
        with pytest.raises(psycopg.errors.CheckViolation, match="invalid claim status transition"):
            connection.execute("UPDATE rm.claims SET status = 'open' WHERE id = %s", (subject,))
