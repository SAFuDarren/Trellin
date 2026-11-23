# SPDX-License-Identifier: Apache-2.0
"""Lean syntax the statement audit rejects is stopped before anything is frozen (#296)."""

import json

import pytest

from research_multiplier.proof.lean_statement_syntax import check, explain, require_supported
from research_multiplier.workflows import intake, lean_workflow

pytestmark = pytest.mark.offline


def _target(proposition, **lean):
    return {"title": "Synthetic target", "domain": "lean-core", "sensitivity": "public",
            "statement": "A synthetic statement.", "formal": {"lean": {"proposition": proposition, **lean}}}


def test_preview_target_rejects_an_instance_binder_with_its_rewrite():
    raw = json.dumps(_target("∀ (X : Type) [Fintype X], 0 ≤ Fintype.card X")).encode()
    # The check runs before the database is touched, so no store is needed.
    with pytest.raises(ValueError) as error:
        intake.preview_target(None, raw)
    message = str(error.value)
    assert "instBinder" in message and "(name : Fintype X)" in message
    assert "formal.lean.proposition" in message and "docs/lean-core.md" in message


@pytest.mark.parametrize("proposition, kind, rewrite", [
    ("∀ (X : Type) [inst : Fintype X], True", "instBinder", "(inst : Fintype X)"),
    ("∀ n : ℕ, n = n", "termℕ", "Nat"),
    ("∀ a b : Int, |a - b| ≥ 0", "«term|___|»", "abs (a - b)"),
    ("∀ (M : Finset Nat), ∑ C ∈ M, C ≥ 0", "BigOperators.bigsum", "Finset.sum"),
    ("∀ (M : Finset Nat), ∃ C ∈ M, C = 0", "binderTerm∈_", "Membership.mem"),
    ("∀ (S : Finset Nat), S = ∅", "term∅", "EmptyCollection.emptyCollection"),
    ("∀ (x : Nat), x ≠ ⊥", "term⊥", "Bot.bot"),
    ("∀ {α : Type*} (x : α), x = x", "universe placeholder", "`Type`"),
    ("∀ (f : Nat → Nat), f = fun n => n", "`fun` term", "definition"),
    ('"text" = "text"', "string literal", "string literals"),
])
def test_each_common_mathlib_construct_is_named_with_an_accepted_rewrite(proposition, kind, rewrite):
    findings = [str(finding) for finding in check(proposition)]
    assert any(kind in text and rewrite in text for text in findings), findings


@pytest.mark.parametrize("proposition", [
    "True ∧ True",
    "(0 : Nat) = 0",
    "∀ n : Nat, n + 0 = n",
    "∀ {α : Type} (x : α), x = x",
    "∃ k, k ≥ 2 ∧ k ≠ 3",
    "∀ a b : Int, a - b < 1 ∨ ¬ (a = b) → a % 2 = 0 ↔ a ^ 2 ≤ b",
    "∀ (s : Finset Nat) (f : Nat → Nat), Finset.sum s f = Finset.sum s f",
    "∀ a b : Int, abs (a - b) ≤ abs a + abs b",
    "∀ (X : Type) (iX : Fintype X), 0 ≤ Fintype.card X",
    "Nat.succ_le_of_lt = Nat.succ_le_of_lt",
])
def test_accepted_core_syntax_passes(proposition):
    assert check(proposition) == []
    require_supported(_target(proposition))
    assert explain([proposition]) is None


def test_shared_parameter_types_and_every_item_are_checked():
    with pytest.raises(ValueError, match=r"formal\.lean\.parameters\[0\]\.type.*termℕ"):
        require_supported(_target("True", parameters=[{"name": "s", "type": "Finset ℕ"}]))
    items = {"items": [
        {"id": "i1", "kind": "hypothesis", "formal": {"lean": {"proposition": "True"}}},
        {"id": "i2", "kind": "theorem", "formal": {"lean": {"declaration": "Finset.card_le", "proposition": "∀ s : Finset ℕ, True"}}},
    ]}
    with pytest.raises(ValueError, match=r"items\[1\]\.formal\.lean\.proposition"):
        intake._require_checkable(items, "lean-core")
    # Other domains do not use the Lean audit.
    intake._require_checkable(items, "general")


def test_draft_theory_failures_keep_other_errors_and_name_rejected_syntax():
    other = ValueError("Confirm the research packet before proposing its Lean statement.")
    assert lean_workflow.explain_statement_syntax("packet", other) == str(other)
    assert "termℕ" in explain(["∀ n : ℕ, n = n"]) and "write `Nat`" in explain(["∀ n : ℕ, n = n"])
