"""Select only plausible witnesses inside the reviewed scope."""

import unittest

from research_multiplier.proof.lean_counterexamples import candidate
from research_multiplier.local_lemma_kernel import _LocalTerm


def suggest(target, *hypotheses):
    return candidate({"antecedents": list(hypotheses), "consequent": target})


class FiniteLeanCounterexampleTests(unittest.TestCase):
    def test_witness_satisfies_outer_and_inner_hypotheses(self):
        sampled = suggest("∀ n : Nat, n ≤ 2 → n + 1 ≤ 2", "(1 : Nat) = 1")
        self.assertEqual(sampled["witness"], {
            "kind": "finite_assignment", "frozen_hypotheses": 1, "guard_count": 1,
            "assignments": [{"variable": "n", "type": "Nat", "value": 2}],
        })
        application = sampled["proof"]["intro"]["body"]["decide_false"]["apply"]
        self.assertEqual(application, [{"use": 0}, {"decide": True}, {"nat_literal": 2}, {"decide": True}])
        self.assertIsNone(suggest("∀ n : Nat, n < 2 → n + 1 ≤ 2", "False"))
        self.assertIsNone(suggest("∀ n : Nat, n < 2 → n + 1 ≤ 2"))
        self.assertIsNone(suggest("∀ n : Nat, n ≤ 2 → n + 1 ≤ 2", "Unknown.researchPremise"))

    def test_closed_boolean_and_nontrivial_literal_scope(self):
        self.assertEqual(suggest("(2 : Nat) ≤ 1", "True")["proof"], {"decide": True})
        self.assertIsNone(suggest("(2 : Nat) ≤ 1", "(2 : Nat) ≤ 1"))
        self.assertIsNone(suggest("∀ n : Nat, 16 < n → n < 17"))
        self.assertIsNone(suggest("(2 : Int) ≤ 1"))
        self.assertIsNone(suggest("(0 : Nat) + 1 - 2 = 0"))
        mixed = suggest("∀ (n : Nat) (b : Bool), n = 2 → b = true")
        self.assertEqual(mixed["witness"]["assignments"], [
            {"variable": "n", "type": "Nat", "value": 2},
            {"variable": "b", "type": "Bool", "value": False},
        ])

    def test_witness_term_does_not_accept_raw_lean_text(self):
        with self.assertRaisesRegex(ValueError, "invalid proof operator"):
            _LocalTerm([], (), {}).generate({"format_version": 1, "proof": {"nat_literal": "0; exact True.intro"}})

    def test_predicate_relation_and_interleaved_guards(self):
        formula = ("∀ (R : Bool → Bool → Prop), (∀ a b : Bool, R a b → R b a) → "
                   "∀ a b c : Bool, R a b → R b c → R a c")
        result = suggest(formula)
        self.assertEqual(result["witness"]["assignments"][0],
                         {"variable": "R", "type": "Bool → Bool → Prop", "value": [False, True, True, False]})
        self.assertEqual(result["witness"]["guard_count"], 3)
        apply = result["proof"]["intro"]["body"]["decide_false"]["apply"]
        self.assertEqual(apply, [
            {"use": 0}, {"bool_relation_literal": [False, True, True, False]}, {"decide": True},
            {"bool_literal": False}, {"bool_literal": True}, {"bool_literal": False},
            {"decide": True}, {"decide": True},
        ])
        self.assertIsNone(suggest(formula, "∀ b : Bool, b = true"))
        self.assertIsNone(suggest("∀ n : Nat, n < 2 → (∃ m : Nat, m = n)"))
        self.assertIsNotNone(suggest("∀ (P : Bool → Prop), (∃ b : Bool, P b) → ∀ b : Bool, P b"))
        with self.assertRaisesRegex(ValueError, "invalid proof operator"):
            _LocalTerm([], (), {}).generate({"format_version": 1, "proof": {"bool_relation_literal": [0, 1, 1, 0]}})
