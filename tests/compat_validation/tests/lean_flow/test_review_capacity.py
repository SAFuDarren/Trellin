"""Use the normal approval, scheduler and incident commands with native Lean and PostgreSQL."""

from datetime import datetime, timezone
import os
import unittest
from unittest.mock import patch

from research_multiplier.storage.postgres import Store

import test_final_local_lemmas as fixtures
import test_lean_flow as database


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ReviewCapacityTests(unittest.TestCase):
    def test_notes_capacity_and_incident_regression(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.assertIsNone(case.cli("review", "capacity")["available_slots"])
        equality = "731 = 731"
        basis = {"kind": "hypothesis", "natural": "731 equals itself in the review capacity example.",
                 "formal": {"lean": {"proposition": equality}}}
        anchor, scope, _ = case.anchor("review-capacity", f"{equality} ∧ {equality}", [basis])
        intent = case.cli("scope", "lean-statement", anchor["id"])
        self.assertEqual(intent["lean"]["antecedents"], [equality])
        self.assertEqual(intent["statement_sha256"], anchor["statement_sha256"])
        self.assertIn("public research scope", case.cli("scope", "lean-statement", anchor["id"],
                                                        "--backtranslate-public", expect=2))
        premise = {"use": 0}
        proof = case.document("review-proof", {"format_version": 1,
                                            "proof": fixtures.intro(1, {"and": [premise, premise]})})
        before_check = datetime.now(timezone.utc)
        verified = case.cli("lean", "check-theory", anchor["id"], proof, "--out", case.fixture.output)
        receipt = verified["evidence_id"]
        approval = ("review-evidence", receipt, "approve", "--actor", "reviewer", "--reason", "Read the exact proof.")
        policy = ("review", "policy", "--actor", "reviewer", "--reason", "Keep automatic reviews within capacity.")
        self.addCleanup(case.cli, *policy)
        with patch.dict(os.environ, case.fixture.env), Store() as store:
            pending = store.query("human", """
                SELECT count(*) AS n FROM rm.research_actionable_review_inbox WHERE kind = 'review_evidence'
            """)[0]["n"]
            spent = store.query("human", """
                SELECT floor(coalesce(sum(minutes), 0))::integer AS n FROM rm.human_time_entries
                WHERE activity = 'review' AND logged_at >= now() - interval '7 days'
            """)[0]["n"]
        case.cli(*policy, "--require-understanding", "--max-pending", pending, "--weekly-minutes", spent + 5)
        self.assertEqual(case.cli("review", "capacity")["available_slots"], 0)
        self.assertTrue(case.cli("advance", "worker", "--stage", "scheduler")["review_paused"])
        self.assertIn("researcher explanation", case.cli(*approval, expect=2))
        self.assertIn("explanation", case.cli(*approval, "--explanation", "Pass through the premise.", expect=2))
        self.assertEqual(case.cli("review", "packet", receipt)["understanding"], [])
        case.cli(*approval, "--explanation", "Both conjuncts follow from the same assumed equality.",
                 "--lesson", "One premise can supply both parts of the conjunction.", "--minutes", 5)
        self.assertEqual(case.cli("review", "capacity")["available_slots"], 0)
        case.cli(*policy, "--require-understanding", "--max-pending", pending, "--weekly-minutes", spent + 6)
        self.assertEqual(case.cli("review", "capacity")["available_slots"], 1)
        self.assertFalse(case.cli("advance", "worker", "--stage", "scheduler")["review_paused"])
        case.cli("promote-evidence", receipt, "--reason", "Apply current exact evidence.")
        packet = case.cli("review", "packet", receipt)
        self.assertEqual(packet["claim_id"], scope["claim_id"])

        sample_args = ("trust", "sample", "--from", before_check.isoformat(),
                       "--to", datetime.now(timezone.utc).isoformat(), "--seed", "reviewed-2026")
        sample = case.cli(*sample_args)
        self.assertEqual([row["evidence_id"] for row in sample["cases"]], [receipt])
        self.assertEqual(case.cli(*sample_args), sample)
        case.cli("trust", "policy", "--min-attempts", 30, "--jump-percent", 30,
                 "--actor", "reviewer", "--reason", "Check independent verifier trends.")
        self.assertIsInstance(case.cli("trust", "monitor", "--last-hours", 1), list)
        self.assertIn("relative window", case.cli("trust", "monitor", "--last-hours", 0, expect=2))

        incident = case.cli("trust", "report", proof, "--kind", "verifier_vulnerability",
                            "--checker", packet["verification"]["effective_checker_revision"],
                            "--summary", "Reproduction fixture.")
        register = ("trust", "regression", incident["id"], proof, "--test-case", self.id(),
                    "--actor", "reviewer", "--reason", "Keep a bounded reproduction in the regression suite.")
        saved = case.cli(*register)
        self.assertEqual(case.cli(*register)["id"], saved["id"])
        self.assertEqual(case.cli("trust", "regressions", incident["id"]), [saved])
        self.assertEqual(len(saved["artifact_sha256"]), 64)
        self.assertEqual(case.cli("trust", "decide", incident["id"], "dismiss", "--actor", "reviewer",
                                  "--reason", "The fixture represents a rejected regression.")["action"], "dismiss")
