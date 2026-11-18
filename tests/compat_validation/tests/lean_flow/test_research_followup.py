"""Derive possible next research scopes while keeping the accepted scope frozen."""

import unittest

import test_final_local_lemmas as fixtures
import test_lean_flow as database


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FollowUpTests(unittest.TestCase):
    def test_unused_premise_creates_new_reviewed_scope_and_retains_original(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        anchor, scope, _ = case.anchor(
            "follow-up-after-adopted-conjunction", "True ∧ ((19 : Nat) = 19)",
            [{"kind": "hypothesis", "natural": "A reviewed premise used only in the original context.",
              "formal": {"lean": {"proposition": "(22 : Nat) = 22"}}}])
        self.assertIn("current, adopted exact Lean proof",
                      case.cli("research", "follow-up", scope["claim_id"], expect=2))
        proof = case.cli("lean", "search", anchor["id"], "--rounds", 0)
        self.assertEqual(proof["status"], "awaiting_evidence_review")
        case.cli("review-evidence", proof["evidence_id"], "approve", "--actor", "researcher",
                 "--reason", "Verified the exact proposition.")
        case.cli("promote-evidence", proof["evidence_id"], "--reason", "Adopt the verified original claim.")
        work = case.cli("research", "follow-up", scope["claim_id"])
        weaker, = (row for row in work["candidates"] if row["kind"] == "test_weaker_basis")
        self.assertEqual(weaker["omit_label"], "H1")
        self.assertEqual({row["kind"] for row in work["candidates"]}, {"test_weaker_basis", "separate_consequence"})
        frame = case.cli("research", "follow-up", scope["claim_id"], "--prepare", weaker["sha256"])
        self.assertEqual(frame["body"]["basis_version_ids"], [])
        self.assertIn("goal confirm", frame["next_command"])
        self.assertIn("confirmed frame", case.cli("research", "follow-up", frame["id"], "--propose", expect=2))
        case.cli("goal", "confirm", frame["id"], "--hash", frame["frame_sha256"], "--actor", "researcher",
                 "--reason", "Review the separate weaker basis.")
        candidate = case.cli("research", "follow-up", frame["id"], "--propose")
        selected = case.cli("goal", "select", candidate["id"], "--hash", candidate["conjecture_sha256"],
                            "--actor", "researcher", "--reason", "Choose the weaker hypothesis set for a new proof.")
        self.assertNotEqual(selected["claim_id"], scope["claim_id"])
        old = case.cli("research", "resume", scope["claim_id"])
        new = case.cli("research", "resume", selected["claim_id"])
        self.assertEqual(old["status"], "proved")
        self.assertEqual(len(old["scope"]["hypotheses"]), 1)
        self.assertEqual(new["scope"]["hypotheses"], [])
        self.assertIsNone(new["conclusion"])
