"""Verify route condition discharge against the exact local Lean scope."""

import os
import unittest
from unittest.mock import patch

import test_local_lemmas as fixtures
from test_lean_flow import DSN

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LocalRouteConditionTests(unittest.TestCase):
    def test_discharge_retirement_and_replacement_keep_route_local(self):
        fixture = fixtures.LocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        anchor, _ = fixture.anchor()
        with patch.dict(os.environ, fixture.fixture.env), Store() as store:
            goal, = store.query("human", "SELECT id FROM rm.research_goal_status WHERE claim_id = %s", anchor["claim_id"])
        goal_id = str(goal["id"])
        proposition = "2 = 2 ∧ 2 = 2"

        def condition(name, requested, kind="route_condition", expected_goal=goal_id):
            return cli("goal", "condition", expected_goal, fixture.document(name, {
                "kind": kind, "statement": requested, "formal": {"lean": {"proposition": requested}},
                "question": f"Does this next step follow from the original scope? {name}",
                "impact": "Required only on this route.",
            }))["id"]

        route = condition("next-step", proposition)
        mismatch = condition("different-condition", "0 = 0")
        self.assertTrue(cli("goal", "show", goal_id)["frame"]["proof_ready"])
        self.assertEqual(cli("review", "inbox", "--claim", anchor["claim_id"]), [])

        def prove(name, first_term):
            proof = {"intro": {"count": 1, "body": {"and": [first_term, {"use": 0}]}}}
            plan = fixture.document(name, {"format_version": 1, "nodes": [
                {"name": name, "proposition": proposition, "proof": proof},
            ]})
            return cli("lean", "derive", anchor["id"], plan)["lemmas"][0]

        lemma = prove("first", {"use": 0})
        with patch.dict(os.environ, fixture.fixture.env), Store() as store:
            command = "SELECT * FROM rm.record_route_condition_local_lemma(%s,%s)"
            for role in ("human", "scheduler", "agent"):
                with self.assertRaisesRegex(ValueError, "permission denied"):
                    store.query(role, command, route, lemma["id"])

        other = cli("goal", "draft", fixture.document("other-frame", {
            "title": "Separate restricted goal", "domain": "lean-core", "sensitivity": "restricted",
            "objective": "Keep another target distinct", "basis_version_ids": [],
        }))
        cli("goal", "confirm", other["id"], "--hash", other["frame_sha256"],
            "--actor", "Researcher", "--reason", "Check separate scope")
        different_goal = cli("goal", "propose", other["id"], fixture.document("other-target", {
            "statement": "True", "formal": {"lean": {"proposition": "True"}}, "provenance": {"kind": "human"},
        }))
        cli("goal", "select", different_goal["id"], "--hash", different_goal["conjecture_sha256"],
            "--actor", "Researcher", "--reason", "Freeze separate target")
        foreign = condition("same-form-different-scope", proposition, expected_goal=other["id"])
        interpretation = condition("meaning-review", proposition, kind="interpretation")
        cli("goal", "decide-condition", interpretation, "reject", "--actor", "Researcher",
            "--reason", "Keep the frozen meaning")
        for wrong in (mismatch, foreign, interpretation):
            self.assertIn("same frozen scope",
                          cli("goal", "record-local-derivation", wrong, lemma["id"], expect=2))

        human = fixture.fixture.env.pop("RM_HUMAN_DSN")
        try:
            first = cli("goal", "record-local-derivation", route, lemma["id"])
            self.assertEqual(cli("goal", "record-local-derivation", route, lemma["id"]), first)
        finally:
            fixture.fixture.env["RM_HUMAN_DSN"] = human

        def status():
            state = cli("goal", "show", goal_id)
            self.assertTrue(state["frame"]["proof_ready"])
            current, = [item for item in state["conditions"] if item["id"] == route]
            return current

        current = status()
        self.assertEqual(current["action"], "derived")
        self.assertTrue(current["derivation_current"])
        self.assertEqual(current["local_verification_attempt_id"], lemma["verification_attempt_id"])
        cli("research", "retire-input", "source", lemma["body"]["candidate"]["sha256"],
            "--reason", "Retire the checked local lemma only")
        pending = status()
        self.assertIsNone(pending["action"])
        self.assertFalse(pending["derivation_current"])
        self.assertIn("needs rechecking", pending["reason"])
        self.assertEqual(pending["local_derivation_id"], first["id"])
        self.assertIn("current local lemma", cli("goal", "record-local-derivation", route, lemma["id"], expect=2))

        fresh = prove("fresh", {"rfl": True})
        replacement = cli("goal", "record-local-derivation", route, fresh["id"])
        self.assertNotEqual(replacement["id"], first["id"])
        self.assertEqual(status()["local_lemma_id"], fresh["id"])
        with patch.dict(os.environ, fixture.fixture.env), Store() as store:
            history = store.query("human", "SELECT id FROM rm.route_condition_local_derivations WHERE request_id = %s",
                                  route)
        self.assertEqual({str(row["id"]) for row in history}, {first["id"], replacement["id"]})
