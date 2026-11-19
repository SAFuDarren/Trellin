"""A checker interruption must leave model-derived receipts recoverable and undisclosed until committed."""

import os
import sys
import unittest
from unittest.mock import patch

import test_lean_flow as lean
import test_lean_search as fixtures

from research_multiplier.final_lean_local import ReceiptCommitError
from research_multiplier.workflows.lean_search import search
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(lean.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanSearchAtomicTests(unittest.TestCase):
    def test_model_receipt_and_trace_commit_together_and_resume_without_another_call(self):
        fixture = fixtures.LeanSearchTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        statement, scope, _ = fixture.anchor("False")
        output = fixture.case.fixture.output
        env = fixture.case.fixture.env
        original = Store.query
        rolled_back = []

        def interrupt_after_trace(store, role, statement, *values):
            rows = original(store, role, statement, *values)
            if "SELECT rm.record_lean_search_provenance(" in statement:
                rolled_back.append(values[0])
                with Store() as independent:
                    self.assertEqual(independent.query("human", "SELECT id FROM rm.evidence WHERE id = %s", values[0]), [])
                raise OSError("injected interruption after trace insert and before commit")
            return rows

        with patch.dict(os.environ, env), patch.object(sys, "path", [str(output), *sys.path]):
            with patch.object(Store, "query", interrupt_after_trace):
                with self.assertRaisesRegex(ReceiptCommitError, "receipt and its provenance"):
                    search(statement["id"], rounds=2, confirm_public=True, output=output)
            with Store() as store:
                self.assertEqual(store.query("human", "SELECT id FROM rm.evidence WHERE id = %s", rolled_back[0]), [])
            result = search(statement["id"], rounds=2, confirm_public=True, output=output)

        self.assertEqual(result["status"], "awaiting_evidence_review")
        self.assertEqual(result["target"], "refutation")
        with patch.dict(os.environ, env), Store() as store:
            rows = store.query("verifier", """
                SELECT a.id FROM rm.attempts a JOIN rm.jobs j ON j.id = a.job_id
                WHERE j.claim_id = %s AND j.payload -> 'lean_search' = 'true'::jsonb
            """, scope["claim_id"])
        self.assertCountEqual([str(row["id"]) for row in rows], [step["model_attempt_id"] for step in result["rounds"]])
        self.assertEqual(len(rows), 2)
        packet = fixture.cli("review", "packet", result["evidence_id"])
        self.assertEqual(packet["verification"]["lean_search_traces"][0]["trace_sha256"], result["record"]["sha256"])
