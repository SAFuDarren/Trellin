# SPDX-License-Identifier: Apache-2.0
"""Exercise confirm_intake review and confirmation against an authenticated disposable database."""

import json
from uuid import uuid4

import pytest

from research_multiplier.storage.postgres import Store
from research_multiplier.workflows import intake
from tests.postgres.test_workbench_clarifications import _request, review_server  # noqa: F401


pytestmark = pytest.mark.postgres
ACTOR = "Fixture researcher"
REASON = "The reviewed basis items match the intended research scope."
DETAIL = "/api/intake-review"
CONFIRM = "/api/intake-review/confirm"


def _lean(proposition):
    return {"lean": {"proposition": proposition}}


def _approve_library_item(store, logical_key, kind, natural, proposition):
    body = {"kind": kind, "natural": natural, "formal": _lean(proposition)}
    saved, = store.query("human", "SELECT * FROM rm.propose_formal_item(%s,%s)", logical_key, store.json(body))
    store.query("human", "SELECT rm.approve_formal_item(%s,%s,%s,%s)",
                saved["id"], saved["version_sha256"], ACTOR, REASON)
    return saved


def _notes_bytes(document):
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def test_notes_intake_review_detail_and_confirm_clear_inbox(review_server):
    with Store() as store:
        local_user = store.local_user()
        assert local_user is not None
        reused = _approve_library_item(
            store, "fixture:zero-refl", "definition", "Zero equals zero.", "(0 : Nat) = 0",
        )
        draft = intake.draft(store, _notes_bytes({
            "title": "Synthetic intake review",
            "domain": "lean-core",
            "objective": "Confirm starting items from Review.",
            "sensitivity": "restricted",
            "items": [
                {"kind": "definition", "natural": "Zero equals zero.", "formal": _lean("(0 : Nat) = 0")},
                {"kind": "hypothesis", "natural": "One equals one.", "formal": None},
                {"kind": "theorem", "natural": "Two equals two.", "formal": _lean("(2 : Nat) = 2")},
            ],
        }))

    inbox = _request(review_server, "/api/inbox")
    assert any(item["kind"] == "confirm_intake" and item["item_id"] == str(draft["id"]) for item in inbox["items"])

    detail = _request(review_server, f"{DETAIL}?intake={draft['id']}")
    assert detail["id"] == str(draft["id"])
    assert detail["sha256"] == draft["intake_sha256"]
    assert detail["status"] == "pending"
    assert detail["confirmed"] is False
    assert detail["target"] is None
    assert detail["reused_count"] == 1
    assert detail["outstanding_count"] == 2
    assert "notes" not in detail

    by_id = {item["id"]: item for item in detail["items"]}
    assert by_id["i1"]["status"] == "reuse"
    assert by_id["i1"]["requires_decision"] is False
    assert by_id["i1"]["matched_item"]["id"] == str(reused["id"])
    assert by_id["i1"]["matched_item"]["version"] == reused["version"]
    assert by_id["i1"]["matched_item"]["stale"] is False
    assert by_id["i2"]["status"] == "formalization_required"
    assert by_id["i2"]["requires_decision"] is True
    assert by_id["i3"]["status"] == "confirm_meaning"
    assert by_id["i3"]["requires_decision"] is True

    # Invalid inputs and stale digest are rejected before mutating state.
    _request(review_server, f"{DETAIL}?intake={uuid4()}", status=404)
    _request(review_server, CONFIRM, document={
        "id": str(draft["id"]), "sha256": "0" * 64, "reason": REASON, "decisions": {},
    }, status=409)
    _request(review_server, CONFIRM, document={
        "id": str(draft["id"]), "sha256": draft["intake_sha256"], "reason": REASON,
        "decisions": {
            "i2": {"action": "accept", "reason": "Bad Lean syntax", "formal": _lean("∀ n : ℕ, n = n")},
            "i3": {"action": "omit", "reason": "Skip"},
        },
    }, status=400)

    confirm_payload = {
        "id": str(draft["id"]),
        "sha256": draft["intake_sha256"],
        "actor": "Forged Client Name",
        "reason": REASON,
        "decisions": {
            "i2": {"action": "accept", "reason": "Formalized during review", "formal": _lean("(1 : Nat) = 1")},
            "i3": {"action": "omit", "reason": "Not needed for the starting basis"},
        },
    }
    confirmed = _request(review_server, CONFIRM, document=confirm_payload)
    assert confirmed["id"] == str(draft["id"])
    assert confirmed["confirmed"] is True
    assert confirmed["goal_id"]
    assert confirmed["claim_id"] is None

    # Exact replay is idempotent; conflicting replay is rejected.
    assert _request(review_server, CONFIRM, document=confirm_payload) == confirmed
    _request(review_server, CONFIRM, document={**confirm_payload, "reason": "Different reason"}, status=409)

    after_inbox = _request(review_server, "/api/inbox")
    assert not any(item["kind"] == "confirm_intake" and item["item_id"] == str(draft["id"])
                   for item in after_inbox["items"])

    after_detail = _request(review_server, f"{DETAIL}?intake={draft['id']}")
    assert after_detail["status"] == "confirmed"
    assert after_detail["confirmed"] is True
    assert after_detail["goal_id"] == confirmed["goal_id"]
    assert after_detail["confirmation"]["actor"] == local_user["display_name"]
    assert after_detail["confirmation"]["reason"] == REASON


def test_target_first_intake_review_binds_claim_and_supersedes_competing_draft(review_server):
    target_doc = {
        "title": "Synthetic target intake",
        "domain": "lean-core",
        "sensitivity": "restricted",
        "statement": "Every natural number equals itself.",
        "formal": _lean("∀ n : Nat, n = n"),
    }
    raw_target = _notes_bytes(target_doc)
    with Store() as store:
        preview = intake.preview_target(store, raw_target)
        frozen = intake.freeze_target(store, raw_target, preview["target_sha256"], ACTOR, REASON)
        first = intake.draft(store, _notes_bytes({
            "objective": target_doc["statement"],
            "items": [
                {"kind": "theorem", "natural": target_doc["statement"], "formal": target_doc["formal"]},
                {"kind": "hypothesis", "natural": "Zero equals zero.", "formal": _lean("(0 : Nat) = 0")},
            ],
        }), target_id=frozen["id"])
        competing = intake.draft(store, _notes_bytes({
            "objective": target_doc["statement"],
            "items": [
                {"kind": "hypothesis", "natural": "One equals one.", "formal": _lean("(1 : Nat) = 1")},
            ],
        }), target_id=frozen["id"])

    detail = _request(review_server, f"{DETAIL}?intake={first['id']}")
    assert detail["status"] == "pending"
    assert detail["target"]["id"] == str(frozen["id"])
    assert detail["target"]["statement"] == target_doc["statement"]
    assert detail["items"][0]["status"] == "omit_frozen_target"
    assert detail["items"][1]["status"] == "confirm_meaning"

    # Accepting the frozen target as a basis item is refused by the kernel guard.
    _request(review_server, CONFIRM, document={
        "id": str(first["id"]),
        "sha256": first["intake_sha256"],
        "reason": REASON,
        "decisions": {
            "i1": {"action": "accept", "reason": "Try accepting frozen target"},
            "i2": {"action": "accept", "reason": "Valid basis"},
        },
    }, status=409)

    confirmed = _request(review_server, CONFIRM, document={
        "id": str(first["id"]),
        "sha256": first["intake_sha256"],
        "reason": REASON,
        "decisions": {
            "i1": {"action": "omit", "reason": "Target cannot be a premise"},
            "i2": {"action": "accept", "reason": "Valid basis"},
        },
    })
    assert confirmed["confirmed"] is True
    assert confirmed["goal_id"]
    assert confirmed["claim_id"]

    first_after = _request(review_server, f"{DETAIL}?intake={first['id']}")
    assert first_after["status"] == "confirmed"
    assert first_after["claim_id"] == confirmed["claim_id"]

    competing_after = _request(review_server, f"{DETAIL}?intake={competing['id']}")
    assert competing_after["status"] == "superseded"
    assert competing_after["claim_id"] == confirmed["claim_id"]

    _request(review_server, CONFIRM, document={
        "id": str(competing["id"]),
        "sha256": competing["intake_sha256"],
        "reason": REASON,
        "decisions": {"i1": {"action": "omit", "reason": "Late confirmation"}},
    }, status=409)
