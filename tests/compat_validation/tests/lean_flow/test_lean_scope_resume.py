"""Only checked changes to a Lake scope become researcher decisions."""

import os
import unittest
from unittest.mock import patch

import test_lean_flow as database
import test_lake_scope as fixtures

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanScopeResumeTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.LakeScopeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli

    def test_measured_change_is_deduplicated_and_clears_when_restored(self):
        self.case.build()
        with patch.object(database, "PROJECT", self.case.project):
            anchor, scope, options = self.case.case.anchor(
                "lake-resume-measured-scope",
                "(ScopeBase.scale = ScopeBase.scale) ∧ (True ∧ True)",
                [
                    {"kind": "hypothesis", "natural": "Truth is a premise of this particular resume test.",
                     "formal": {"lean": {"proposition": "True"}}},
                    {"kind": "theorem", "natural": "The current Lake example provides a conjunction of truths.",
                     "formal": {"lean": {"proposition": "True ∧ True", "declaration": "ScopeBase.known"}}},
                ],
                module="ScopeProof",
            )
        proof = ("lean", "check-source", anchor["id"], "--module", "ScopeProof",
                 "--declaration", "rebuildProof", *options)

        def scope_decisions():
            resume = self.cli("research", "resume", scope["claim_id"])
            return [item for item in resume["decisions"]["items"] if item["kind"] == "review_lean_scope_change"]

        self.assertEqual(scope_decisions(), [])
        self.case.build(scale=2)
        change = self.cli(*proof, expect=2)
        self.assertEqual(change["status"], "lake_scope_review_required")
        decision, = scope_decisions()
        self.assertEqual(decision["reference"]["statement_id"], anchor["id"])
        self.assertIn({"kind": "frozen_goal_or_premises"}, decision["changes"]["items"])

        self.cli(*proof, expect=2)
        self.assertEqual(scope_decisions(), [decision])

        with patch.dict(os.environ, self.case.case.fixture.env), Store() as store:
            with self.assertRaisesRegex(ValueError, "only observed changes"):
                store.query("verifier", "SELECT rm.record_lean_scope_observation(%s,%s,%s)",
                            anchor["id"], change["observed_environment_sha256"],
                            store.json([{"kind": "missing_requirements"}]))
        self.assertEqual(scope_decisions(), [decision])

        self.case.build()
        restored = self.cli(*proof)
        self.assertIs(restored["kernel_rechecked"], True)
        self.assertEqual(scope_decisions(), [])
