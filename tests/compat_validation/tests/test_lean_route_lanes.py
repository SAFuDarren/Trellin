"""Check route scheduling and isolate raw model feedback across routes."""

from unittest import TestCase

from research_multiplier.workflows import lean_search
from research_multiplier.proof import lean_search_session


def route(identifier, *propositions):
    return {"implication_id": identifier, "status": "awaiting_obligations", "obligations": [
        {"proposition": proposition, "lemma_id": None} for proposition in propositions
    ]}


def blocked(identifier, proposition, reason, stalled):
    return {"status": "blocked", "reason": reason,
            "focus": {"method": "key_lemma", "implication_id": identifier, "proposition": proposition},
            "route_checkpoint": {"verified": 0, "pending": 2, "stalled": stalled}}


class ParallelRouteSelectionTests(TestCase):
    def test_live_proof_can_resume_a_paused_lane(self):
        first, second = route("first", "A", "B"), route("second", "C", "B")
        history = [blocked("first", "A", "Old route stalled.", 2)]
        [focus] = lean_search_session.parallel_foci(history, [first, second], 2, 2)
        self.assertEqual(focus["implication_id"], "second")
        first["obligations"][0]["lemma_id"] = "verified"
        reopened = lean_search_session.parallel_foci(history, [first, second], 2, 2)
        self.assertEqual({item["implication_id"] for item in reopened}, {"first", "second"})
        self.assertEqual({item["proposition"] for item in reopened}, {"B", "C"})

    def test_author_sees_only_own_failed_work_and_checked_lemma(self):
        class NoPriorJob:
            def query(self, *_):
                return []

        first, second = route("first", "A"), route("second", "C")
        history = [blocked("first", "A", "History in first route.", 1),
                   blocked("second", "C", "History in second route.", 1)]
        known = {"public-lemma": {"status": "verified", "proposition": "Shared checked proposition"},
                 "unverified-draft": {"status": "waiting", "proposition": "Remove from author package"}}
        scope = {"antecedents": [], "consequent": "Original"}
        request = lean_search._request(NoPriorJob(), None, {"claim_id": "claim"}, "test", scope, 3,
                                       history, known, [], [first, second], focus=history[0]["focus"],
                                       independent=True, ranked=True)
        self.assertEqual([entry["reason"] for entry in request["history"]], ["History in first route."])
        self.assertEqual(request["open_routes"], [first])
        self.assertEqual([entry["id"] for entry in request["checked_local_lemmas"]], ["public-lemma"])
        self.assertEqual(request["antecedents"], [])
