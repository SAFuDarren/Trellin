import unittest

from research_multiplier.proof import lean_search_session


STATEMENT = {"id": "statement", "statement_sha256": "reviewed"}


class SearchCheckpointTests(unittest.TestCase):
    def test_checkpoint_cannot_modify_anchor_or_consume_duplicate_attempts(self):
        class Checkpoint:
            def read_json(self, _):
                return {
                    "statement_id": "statement",
                    "sustained": lean_search_session.marker(STATEMENT),
                    "rounds": [{"round": n, "status": "blocked",
                                "model_attempt_id": "00000000-0000-0000-0000-000000000100"} for n in (1, 2)],
                }

        with self.assertRaisesRegex(ValueError, "confirmed Lean statement"):
            lean_search_session.restore(Checkpoint(), {}, {**STATEMENT, "statement_sha256": "changed"}, 2)
        with self.assertRaisesRegex(ValueError, "repeats a model attempt"):
            lean_search_session.restore(Checkpoint(), {}, STATEMENT, 2)

    def test_restart_retains_route_attempts_alongside_a_lane_checkpoint(self):
        class Checkpoint:
            def read_json(self, _):
                return {
                    "statement_id": "statement",
                    "sustained": lean_search_session.marker(STATEMENT),
                    "rounds": [{
                        "round": 1,
                        "status": "blocked",
                        "model_attempt_id": "00000000-0000-0000-0000-000000000100",
                        "route_checkpoint": {"verified": 0, "pending": 1, "stalled": 1},
                    }],
                    "route_attempts": [{"implication_id": "refuted", "status": "route_refuted"}],
                }

        history, attempts = lean_search_session.restore(Checkpoint(), {}, STATEMENT, 2, include_routes=True)
        self.assertEqual(history[0]["route_checkpoint"], {"verified": 0, "pending": 1, "stalled": 1})
        self.assertEqual(attempts, [{"implication_id": "refuted", "status": "route_refuted"}])

    def test_model_focus_moves_off_blocked_routes_and_uses_checked_alternative(self):
        routes = [
            {"implication_id": identifier, "status": "awaiting_obligations",
             "obligations": [{"proposition": proposition, "lemma_id": None}]}
            for identifier, proposition in (("first", "Original obstacle"), ("second", "New key lemma"))
        ]
        focus = lean_search_session.focus([], routes[:1])
        self.assertEqual(focus, {"method": "key_lemma", "implication_id": "first", "proposition": "Original obstacle"})
        blocked = {"status": "blocked", "focus": focus, "reason": "This route is too strong."}
        self.assertEqual(lean_search_session.focus([blocked], routes[:1]), {"method": "alternative_route"})
        next_focus = lean_search_session.focus([blocked, {"status": "route_checked"}], routes)
        self.assertEqual(next_focus, {"method": "key_lemma", "implication_id": "second", "proposition": "New key lemma"})

    def test_method_failures_allow_a_distinct_refutation_attempt(self):
        stalled = [{"status": "blocked", "focus": {"method": "alternative_route"}}]
        self.assertEqual(lean_search_session.focus(stalled, []), {"method": "refutation"})
        stalled.append({"status": "rejected", "focus": {"method": "refutation"}})
        self.assertEqual(lean_search_session.focus(stalled, []), {"method": "alternative_route"})

    def test_checkpoint_keeps_financed_history_and_retries_only_a_denied_dispatch(self):
        class Checkpoint:
            def read_json(self, _):
                return {
                    "statement_id": "statement",
                    "sustained": lean_search_session.marker(STATEMENT),
                    "rounds": [
                        {"round": 1, "status": "route_checked",
                         "model_attempt_id": "00000000-0000-0000-0000-000000000100"},
                        {"round": 2, "status": "budget_blocked",
                         "model_attempt_id": "00000000-0000-0000-0000-000000000101"},
                    ],
                }

        rounds = lean_search_session.restore(Checkpoint(), {}, STATEMENT, 2)
        self.assertEqual([step["status"] for step in rounds], ["route_checked"])

    def test_renaming_bound_variables_does_not_reset_failed_obligation_usage(self):
        routes = [
            {"implication_id": identifier, "status": "awaiting_obligations", "obligations": [
                {"proposition": f"∀ ({name} : Nat), {name} = {name}", "lemma_id": None},
            ]}
            for identifier, name in (("one", "x"), ("two", "n"))
        ]
        first = lean_search_session.focus([], routes)
        history = [{"focus": first, "status": "blocked", "usage": {
            "execution_ms": 100, "input_tokens": 30, "cost_usd": "0.0025",
        }}]
        self.assertEqual(lean_search_session.focus(history, routes), {"method": "alternative_route"})
        by_route = {row["implication_id"]: row for row in lean_search_session.compare_routes(history, routes)}
        self.assertEqual(by_route["one"]["model_rounds"], 1)
        self.assertEqual(by_route["one"]["execution_ms"], 100)
        self.assertEqual(by_route["one"]["cost_usd"], "0.0025")
        self.assertEqual(by_route["two"]["untried_obligations"], 0)

    def test_checked_unrelated_lemma_preserves_lemma_and_moves_to_another_duty(self):
        route = {"implication_id": "first", "status": "awaiting_obligations", "obligations": [
            {"proposition": "Required equality", "lemma_id": None},
        ]}
        second = {"implication_id": "second", "status": "awaiting_obligations", "obligations": [
            {"proposition": "Alternative bound", "lemma_id": None},
        ]}
        focus = lean_search_session.focus([], [route, second])
        accepted = {"status": "lemma_verified", "lemma_id": "independent-lemma", "focus": focus,
                    "obligation_progress": "unchanged"}
        self.assertFalse(lean_search_session.obligation_progress([route], [route]))
        self.assertEqual(lean_search_session.focus([accepted], [route, second])["implication_id"], "second")
        self.assertEqual(lean_search_session.focus([accepted], [route])["method"], "alternative_route")
        self.assertEqual(lean_search_session.model_history([accepted], True)[0]["lemma_id"], "independent-lemma")
        checked = {**route, "status": "ready", "obligations": [
            {"proposition": "Required equality", "lemma_id": "verified-proof"},
        ]}
        self.assertTrue(lean_search_session.obligation_progress([route], [checked]))
