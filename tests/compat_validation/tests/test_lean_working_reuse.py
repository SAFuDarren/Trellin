"""Cover alpha-matching and citation portability before the native regressions."""

import unittest

from research_multiplier.lean_parameters import Hypotheses
from research_multiplier.proof.lean_working_reuse import _premise_arguments, _under_new_scope, match_key


def context(antecedents, theorems=()):
    return {"antecedents": antecedents, "approved_theorems": list(theorems),
            "parameters": list(getattr(antecedents, "parameters", ()))}


class WorkingReuseTests(unittest.TestCase):
    def test_matches_alpha_equivalent_explicit_binders_without_merging_distinct_types(self):
        key = match_key("∀ (u v : Nat), h (g u) = h (g v) → u = v")
        self.assertEqual(key, match_key("∀ x y : Nat, h (g x)=h (g y) → x=y"))
        self.assertNotEqual(key, match_key("∀ x y : Int, h (g x)=h (g y) → x=y"))
        self.assertNotEqual(key, match_key("∀ x y : Nat, h (g x)=h (g y) → x=x"))

    def test_matches_parenthesized_guards_without_merging_different_implications(self):
        primary = match_key("∀ a b c : Nat, a ≤ b → b ≤ c → a = c → b = c")
        self.assertEqual(primary, match_key("∀ i j k : Nat, (i ≤ j) → (j ≤ k) → (i = k) → (j = k)"))
        self.assertNotEqual(primary, match_key("∀ i j k : Nat, (i ≤ j → j ≤ k) → (i = k) → (j = k)"))

    def test_preserves_source_premises_and_moves_nested_binders(self):
        source = ["1 = 2", "True"]
        destination = ["True", "False", "1 = 2"]
        arguments = _premise_arguments(source, context(destination), {})
        proof = {"intro": {"count": 3, "body": {"and": [{"use": 0}, {"use": 2}]}}}
        result = _under_new_scope(proof, source, destination, arguments)
        self.assertEqual(result, {"intro": {"count": 3, "body": {"intro": {
            "count": 1, "body": {"and": [{"use": 2}, {"use": 3}]},
        }}}})
        self.assertIsNone(_premise_arguments(source, context(["True"]), {}))
        self.assertIsNone(_under_new_scope({"trivial": True}, source, destination, arguments))

    def test_shared_parameters_and_premises_have_separate_argument_positions(self):
        parameters = ({"name": "f", "type": "Nat → Nat"},)
        source = Hypotheses(["∀ x : Nat, f x = x"], parameters)
        destination = Hypotheses(["True", "∀ y : Nat, f y = y"], parameters)
        arguments = _premise_arguments(source, context(destination), {})
        self.assertEqual(arguments, [{"use": 0}, {"use": 2}])
        repaired = _under_new_scope(
            {"intro": {"count": 3, "body": {"apply": [{"use": 1}, {"use": 2}]}}},
            source,
            destination,
            arguments,
        )
        self.assertEqual(repaired, {"intro": {"count": 3, "body": {"intro": {
            "count": 1, "body": {"apply": [{"use": 2}, {"use": 3}]},
        }}}})
        self.assertIsNone(_premise_arguments(source, context(["True"]), {}))

    def test_reproved_premises_keep_shared_parameters_and_their_own_binders(self):
        parameters = ({"name": "n", "type": "Nat"},)
        source = Hypotheses(["0 ≤ n", "True → True"], parameters)
        destination = Hypotheses([], parameters)
        arguments = _premise_arguments(source, context(destination, [{
            "id": "approved", "proposition": "∀ k : Nat, 0 ≤ k", "antecedents": [],
        }]), {})
        checked_fact = {"apply": [{"ref": "approved"}, {"use": 0}]}
        self.assertEqual(arguments, [{"use": 0}, checked_fact, {"intro": {"count": 1, "body": {"use": 1}}}])
        repaired = _under_new_scope(
            {"intro": {"count": 4, "body": {"apply": [{"use": 2}, {"use": 1}]}}},
            source, destination, arguments,
        )
        self.assertEqual(repaired, {"intro": {"count": 1, "body": {"intro": {"count": 1, "body": {
            "apply": [{"intro": {"count": 1, "body": {"use": 2}}}, checked_fact],
        }}}}})
