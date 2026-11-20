import unittest

from research_multiplier.proof import lean_decomposition, lean_search_session


class LeanDecompositionTests(unittest.TestCase):
    def test_nested_conjunction_reuses_the_same_explicit_telescope(self):
        reduction = lean_decomposition.conjunction(
            "∀ (x y : Nat), ∀ p : Prop, p → ((x = x ∧ p) ∧ (y = y ∧ p))", 1
        )
        head = "∀ (x y : Nat), ∀ p : Prop, (p) → "
        self.assertEqual(reduction["obligations"], [head + f"({leaf})" for leaf in ("x = x", "p", "y = y")])
        proof = reduction["proof"]["intro"]
        self.assertEqual(proof["count"], 8)
        left, right = proof["body"]["and"]
        self.assertEqual(left["and"][1], right["and"][1])
        self.assertEqual(left["and"][0]["apply"], [{"use": index} for index in (1, 4, 5, 6, 7)])

    def test_reject_implicit_and_existential_binders(self):
        for text in ("∀ {x : Nat}, x = x ∧ True", "∃ x : Nat, x = x ∧ True",
                     "∀ x, x = x ∧ True", "∀ x : Nat, x = x"):
            self.assertIsNone(lean_decomposition.conjunction(text, 0), text)

    def test_real_checked_obligations_change_the_preferred_route(self):
        def route(identifier, checked, pending):
            return {
                "implication_id": identifier, "status": "awaiting_obligations",
                "obligations": [{"proposition": proposition, "lemma_id": "current"}
                                for proposition in checked] +
                               [{"proposition": proposition, "lemma_id": None}
                                for proposition in pending],
            }

        short = route("short", [], ["A"])
        worked = route("worked", ["B", "C"], ["D"])
        self.assertEqual(lean_search_session.classic_focus([], [short, worked])["implication_id"], "short")
        self.assertEqual(lean_search_session.focus([], [short, worked])["implication_id"], "worked")
        stalled = [{"status": "blocked", "focus": {"implication_id": "worked", "proposition": "D",
                                                   "method": "key_lemma"}}]
        self.assertEqual(lean_search_session.focus(stalled, [short, worked])["implication_id"], "short")
        changed = route("worked", ["B", "C", "D"], ["E"])
        cards = lean_search_session.compare_routes(stalled, [short, changed])
        self.assertEqual((cards[0]["implication_id"], cards[0]["verified_obligations"],
                          cards[0]["stalled_attempts"]), ("worked", 3, 0))

    def test_shared_failed_obligation_does_not_block_fresh_work_on_same_route(self):
        routes = [
            {"implication_id": "first", "status": "awaiting_obligations", "obligations": [
                {"proposition": "P", "lemma_id": None}, {"proposition": "Q", "lemma_id": None}]},
            {"implication_id": "second", "status": "awaiting_obligations", "obligations": [
                {"proposition": "P", "lemma_id": None}]},
        ]
        history = [{"status": "proposal_failed", "focus": {
            "method": "key_lemma", "implication_id": "first", "proposition": "P"}}]
        self.assertEqual(lean_search_session.focus(history, routes),
                         {"method": "key_lemma", "implication_id": "first", "proposition": "Q"})
        first, second = lean_search_session.compare_routes(history, routes)
        self.assertEqual((first["untried_obligations"], second["untried_obligations"]), (1, 0))
