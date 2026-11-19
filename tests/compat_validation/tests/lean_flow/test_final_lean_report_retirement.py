"""Follow a retired final report through the real Lean and PostgreSQL review path."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures

from research_multiplier import final_lean_local
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(fixtures.lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FinalLeanReportRetirementTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests("test_lean_negation_can_reuse_a_scoped_lemma")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli

    def candidate(self, label):
        premise = {"kind": "hypothesis", "natural": f"Given True for {label}.",
                   "formal": {"lean": {"proposition": "True"}}}
        anchor, scope, _ = self.case.anchor(label, "(True ∧ True) ∧ True", [premise])
        use = {"use": 0}
        plan = self.case.document(label + "-lemma", {"format_version": 1, "nodes": [{
            "name": "L1", "proposition": "True ∧ True", "proof": fixtures.intro(1, {"and": [use, use]}),
        }]})
        lemma, = self.cli("lean", "derive", anchor["id"], plan)["lemmas"]
        submission = {"format_version": 1, "proof": fixtures.intro(1, {
            "and": [fixtures.apply({"ref": lemma["id"]}, use), use],
        })}
        return anchor, scope, submission

    def verified(self, label):
        anchor, scope, submission = self.candidate(label)
        document = self.case.document(label + "-proof", submission)
        result = self.cli("lean", "check-theory", anchor["id"], document, "--out", self.case.fixture.output)
        receipt = result["evidence_id"]
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            checked, = store.query("verifier", """
                SELECT c.body #>> '{report,sha256}' AS report, e.artifact_sha256 AS candidate
                FROM rm.evidence e JOIN rm.lean_check_status c ON c.id = e.lean_check_id
                WHERE e.id = %s
            """, receipt)
        self.assertNotEqual(checked["report"], checked["candidate"])
        return scope["claim_id"], receipt, checked["report"]

    def retire(self, digest):
        self.cli("research", "retire-input", "source", digest, "--reason", "Withdraw the exact verifier report.")

    def approve(self, receipt, **options):
        return self.cli("review-evidence", receipt, "approve", "--actor", "researcher",
                        "--reason", "Review final verification.", **options)

    def test_withdrawal_blocks_approval_and_promotion(self):
        claim, receipt, digest = self.verified("Withdraw pending final report")
        self.assertEqual([entry["item_id"] for entry in self.cli("review", "inbox", "--claim", claim)], [receipt])
        self.retire(digest)
        self.assertTrue(self.cli("review", "packet", receipt)["verification"]["needs_recheck"])
        self.assertEqual(self.cli("review", "inbox", "--claim", claim), [])
        self.assertIn("approval requires", self.approve(receipt, expect=2))
        self.assertIn("current verified receipt",
                      self.cli("promote-evidence", receipt, "--reason", "Try adoption after withdrawal.", expect=2))

    def test_withdrawal_marks_an_already_adopted_claim_for_recheck(self):
        _, receipt, digest = self.verified("Withdraw adopted final report")
        self.approve(receipt)
        self.cli("promote-evidence", receipt, "--reason", "Apply the current result.")
        self.assertEqual(self.cli("review", "packet", receipt)["claim_status"], "proved")
        self.retire(digest)
        stale = self.cli("review", "packet", receipt)
        self.assertEqual(stale["claim_status"], "needs_recheck")
        self.assertTrue(stale["verification"]["needs_recheck"])

    def test_report_withdrawn_after_verification_cannot_be_registered(self):
        anchor, scope, submission = self.candidate("Withdraw before registration")
        original_query = Store.query
        retired = []

        def retire_before_issue(store, role, query, *values):
            if "rm.record_verified_lean(" in query and not retired:
                report = values[2].obj["report"]["sha256"]
                original_query(store, "human", "SELECT rm.retire_verification_input(%s,%s,%s)",
                               "source", report, "Withdraw the check report before receipt registration.")
                retired.append(report)
            return original_query(store, role, query, *values)

        with patch.dict(os.environ, self.case.fixture.env):
            with patch.object(Store, "query", retire_before_issue):
                with self.assertRaisesRegex(ValueError, "Lean verification report requires recheck"):
                    final_lean_local.check(anchor["id"], submission, self.case.fixture.output)
            self.assertEqual(len(retired), 1)
            with self.assertRaisesRegex(ValueError, "Lean verification report requires recheck"):
                final_lean_local.check(anchor["id"], submission, self.case.fixture.output)
            with Store() as store:
                checks = store.query("verifier", "SELECT id FROM rm.lean_check_status WHERE statement_id = %s",
                                     anchor["id"])
            self.assertEqual(checks, [])
        self.assertEqual(self.cli("review", "inbox", "--claim", scope["claim_id"]), [])
