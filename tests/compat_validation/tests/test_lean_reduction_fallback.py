"""Guard the distinction between failed candidates and shared safety failures."""

import unittest
from unittest.mock import Mock, patch

from research_multiplier.proof import lean_reductions
from research_multiplier.final_lean_local import FinalProofRejected, ReceiptCommitError


class ReadyReductionTests(unittest.TestCase):
    anchor = {"id": "frozen", "statement_sha256": "confirmed", "check_ready": True,
              "body": {"scope": {"lean_hypotheses": []}}}

    def setUp(self):
        self.routes = [{"status": "ready", "implication_id": identifier,
                        "obligations": [{"lemma_id": lemma}]}
                       for identifier, lemma in (("first", "proof-one"), ("second", "proof-two"))]
        self.runtime = Mock(spec=["assert_current"])
        self.store = Mock()

    def test_independent_ready_candidate_runs_after_rejection(self):
        attempts = []
        with patch.object(lean_reductions, "check", side_effect=[
            FinalProofRejected("route cannot close", "verification-one"), {"evidence_id": "checked-second"},
        ]) as check, patch.object(lean_reductions.lean_workflow, "_statement", return_value=self.anchor):
            result = lean_reductions.close_ready(self.store, self.anchor, routes=self.routes, output="outside",
                                                 checked_runtime=self.runtime, attempts=attempts)
        self.assertEqual(check.call_count, 2)
        self.assertEqual(result["used_reduction"], "second")
        self.assertEqual(attempts, [{
            "implication_id": "first", "obligation_lemma_ids": ["proof-one"], "status": "closure_rejected",
            "reason": "route cannot close", "attempt_id": "verification-one",
        }])
        self.runtime.assert_current.assert_called_once()
        with patch.object(lean_reductions, "check", return_value={"evidence_id": "checked-second"}) as check:
            lean_reductions.close_ready(self.store, self.anchor, routes=self.routes, output="outside",
                                        checked_runtime=self.runtime, attempts=attempts)
        self.assertEqual(check.call_count, 1)
        self.assertIn({"ref": "second"}, check.call_args.args[1]["proof"]["apply"])

    def test_receipt_failure_does_not_bypass_the_commit_guard(self):
        with patch.object(lean_reductions, "check", side_effect=ReceiptCommitError("transaction failed")) as check:
            with self.assertRaises(ReceiptCommitError):
                lean_reductions.close_ready(self.store, self.anchor, routes=self.routes, output="outside",
                                            checked_runtime=self.runtime, attempts=[])
        check.assert_called_once()

    def test_withdrawn_scope_stops_the_second_route(self):
        retired = {**self.anchor, "check_ready": False}
        with patch.object(lean_reductions, "check", side_effect=FinalProofRejected("rejected", "attempt")) as check, \
                patch.object(lean_reductions.lean_workflow, "_statement", return_value=retired):
            with self.assertRaisesRegex(ValueError, "scope changed"):
                lean_reductions.close_ready(self.store, self.anchor, routes=self.routes, output="outside",
                                            checked_runtime=self.runtime, attempts=[])
        check.assert_called_once()
