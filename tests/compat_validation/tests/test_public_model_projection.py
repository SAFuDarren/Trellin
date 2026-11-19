"""Reject disclosure outside authorized Lean model request fields."""

from unittest import TestCase

from research_multiplier.policy.lean_model_context import PublicSearchView


class PublicRequestProjectionTests(TestCase):
    def test_archived_context_keeps_only_current_provenance_and_machine_history(self):
        scope = {"consequent": "Public goal", "antecedents": []}
        lemmas = {"checked": {"proposition": "Public lemma"}}
        routes = {"route": {"obligations": [{"proposition": "Public obligation", "lemma_id": None}]}}
        view = PublicSearchView(scope, lemmas, routes, frozenset({"checked-shape"}))
        unsafe = {
            **scope, "round": 2, "search_focus": {"method": "key_lemma", "implication_id": "route",
                                                   "proposition": "Public obligation"},
            "history": [{"status": "blocked", "action": "blocked", "reason": "local only",
                         "feedback": "local only", "obligations": ["local only"]},
                        {"status": "rejected", "feedback": "Try another checked route before stopping."}],
            "open_routes": [{"implication_id": "route", "status": "awaiting_obligations",
                             "obligations": [{"proposition": "Public obligation", "lemma_id": "private"}]}],
            "checked_route_keys": ["checked-shape", "unchecked-hash"],
            "route_failures": [{"implication_id": "route", "status": "closure_rejected", "reason": "local only"}],
            "route_comparison": [{"implication_id": "route", "status": "awaiting_obligations",
                                  "model_rounds": 1, "untrusted": "local only"}],
            "checked_local_lemmas": [
                {"id": "checked", "proposition": "Public lemma", "antecedents": []},
                {"id": "private", "proposition": "local only", "antecedents": []},
            ],
        }
        public = view.project(unsafe)
        with self.assertRaisesRegex(ValueError, "no longer authorized"):
            view.require(unsafe)
        view.require(public)
        self.assertEqual(public["history"], [
            {"status": "blocked", "action": "blocked"},
            {"status": "rejected", "feedback": "Try another checked route before stopping."},
        ])
        self.assertEqual(public["search_focus"], unsafe["search_focus"])
        self.assertEqual(public["checked_route_keys"], ["checked-shape"])
        self.assertEqual(public["checked_local_lemmas"], unsafe["checked_local_lemmas"][:1])
        self.assertIsNone(public["open_routes"][0]["obligations"][0]["lemma_id"])
        self.assertNotIn("local only", str(public))
        with self.assertRaisesRegex(ValueError, "no longer authorized"):
            PublicSearchView(scope, {}, {}, frozenset()).require(public)
