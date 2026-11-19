"""Manual theorem entry and review reuse the human research workflow."""

import json
from uuid import UUID

import pytest

from research_multiplier.workflows import workbench_theorems as theorems


pytestmark = pytest.mark.offline
VERSION = UUID("6e3e4cb2-ed9d-445d-830a-c68653728a29")
DIGEST = "a" * 64
ENTRY = {
    "kind": "library",
    "title": "A simple proposition",
    "wording": "All natural numbers equal themselves.",
    "lean": "∀ n : Nat,\n  n = n",
    "source": "Research notes, p. 5",
}
REVIEW = {"actor": "Researcher", "reason": "Checked the statement and its exact scope."}


class LibraryStore:
    def __init__(self):
        self.row = None
        self.proposals = []
        self.approvals = []

    def json(self, document):
        return document

    def query(self, role, sql, *values):
        assert role == "human"
        if "to_regprocedure" in sql:
            return [{"installed": False}]
        if "propose_formal_item" in sql:
            self.proposals.append(values)
            if self.row is None:
                self.row = {"id": VERSION, "version_sha256": DIGEST, "body": values[1],
                            "admitted": False, "needs_recheck": False}
        elif "approve_formal_item" in sql:
            self.approvals.append(values)
            self.row["admitted"] = True
        else:
            assert "formal_item_status" in sql
            if "AND NOT admitted" in sql and self.row and self.row["admitted"]:
                return []
        return [self.row] if self.row else []


def test_library_draft_keeps_exact_text_and_confirmation_is_separate():
    store = LibraryStore()

    first = theorems.add(store, ENTRY)
    retry = theorems.add(store, ENTRY)
    assert first == retry == {"kind": "library", "id": str(VERSION), "status": "draft", "sha256": DIGEST}
    assert store.proposals[0] == store.proposals[1]
    _, body = store.proposals[0]
    assert body["kind"] == "theorem"
    assert body["formal"] == {"lean": {"proposition": ENTRY["lean"]}}
    assert not store.approvals
    assert theorems.recent(store) == {"drafts": [{
        "id": str(VERSION), "sha256": DIGEST,
        **{key: ENTRY[key] for key in ("title", "wording", "lean", "source")},
    }]}

    result = theorems.confirm(store, {"id": str(VERSION), "sha256": DIGEST, **REVIEW})
    assert result == {**first, "status": "confirmed"}
    assert store.approvals == [(VERSION, DIGEST, REVIEW["actor"], REVIEW["reason"])]
    assert theorems.recent(store) == {"drafts": []}


def test_confirmation_rejects_missing_or_changed_draft():
    store = LibraryStore()
    review = {"id": str(VERSION), "sha256": DIGEST, **REVIEW}
    with pytest.raises(theorems.TheoremError, match="project.theorem.missing"):
        theorems.confirm(store, review)
    theorems.add(store, ENTRY)
    with pytest.raises(theorems.TheoremError, match="project.theorem.confirmation"):
        theorems.confirm(store, {**review, "sha256": "b" * 64})
    assert not store.approvals


@pytest.mark.parametrize(("patch", "code"), [
    ({"lean": "  "}, "invalid"),
    ({"wording": "\x00"}, "invalid"),
    ({"lean": "\ud800"}, "invalid"),
    ({"domain": "other"}, "invalid"),
    ({"proved": True}, "invalid"),
    ({"kind": "goal"}, "confirmation"),
    ({"kind": "goal", "confirmed": 1, **REVIEW}, "confirmation"),
    ({"kind": "goal", "confirmed": True}, "confirmation"),
])
def test_invalid_entry_is_rejected_before_database_write(patch, code):
    with pytest.raises(theorems.TheoremError, match=f"project.theorem.{code}"):
        theorems.add(None, {**ENTRY, **patch})


def test_goal_uses_exact_restricted_target_and_only_explicit_empty_basis(monkeypatch):
    info = {"id": "target", "claim_id": None, "packet_id": None}
    actions = []

    class GoalStore:
        def query(self, role, sql, digest):
            assert role == "human" and "pg_advisory_xact_lock" in sql and digest == DIGEST
            actions.append("lock")

    def preview(store, raw):
        actions.append("preview")
        assert json.loads(raw) == {"title": ENTRY["title"], "statement": ENTRY["wording"],
                                   "domain": "lean-core", "sensitivity": "restricted",
                                   "formal": {"lean": {"proposition": ENTRY["lean"]}}}
        return {"target_sha256": DIGEST}

    def freeze(store, raw, digest, actor, reason):
        actions.append("freeze")
        assert digest == DIGEST and [actor, reason] == [REVIEW["actor"], REVIEW["reason"]]
        return info.copy()

    def draft(store, raw, *, target_id):
        actions.append("draft")
        assert target_id == "target" and json.loads(raw)["items"] == []
        return {"id": "intake", "intake_sha256": DIGEST}

    def confirm(store, identifier, digest, decisions, actor, reason):
        actions.append("confirm")
        assert identifier == "intake" and decisions == {}
        info.update(claim_id="claim", packet_id="packet")

    monkeypatch.setattr(theorems.intake, "preview_target", preview)
    monkeypatch.setattr(theorems.intake, "freeze_target", freeze)
    monkeypatch.setattr(theorems.intake, "draft", draft)
    monkeypatch.setattr(theorems.intake, "confirm", confirm)
    monkeypatch.setattr(theorems.intake, "target", lambda store, identifier: info.copy())
    goal = {**ENTRY, **REVIEW, "kind": "goal", "confirmed": True}
    expected = {"kind": "goal", "status": "open", "id": "claim", "claim_id": "claim",
                "target_id": "target", "packet_id": "packet"}
    assert theorems.add(GoalStore(), goal) == expected
    assert actions == ["preview", "lock", "freeze", "draft", "confirm"]

    actions.clear()
    assert theorems.add(GoalStore(), goal) == expected
    assert actions == ["preview", "lock", "freeze"]
