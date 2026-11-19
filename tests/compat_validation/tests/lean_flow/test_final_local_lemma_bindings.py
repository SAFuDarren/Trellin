"""Test the database boundary between the Lean audit and final evidence."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier import final_lean_local
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FinalLocalLemmaBindingTests(unittest.TestCase):
    def test_database_rejects_a_report_omitting_a_kernel_detected_reference(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        choice = {"kind": "hypothesis", "natural": "Two equals two in the report binding case.",
                  "formal": {"lean": {"proposition": "2 = 2"}}}
        anchor, scope, _ = case.anchor("report-binding", "2 = 2 ∧ 2 = 2", [choice])
        plan = case.document("plan", {"format_version": 1, "nodes": [
            {"name": "L1", "proposition": "2 = 2", "proof": fixtures.intro(1, {"use": 0})},
        ]})
        lemma, = case.cli("lean", "derive", anchor["id"], plan)["lemmas"]
        submission = {"format_version": 1, "proof": fixtures.intro(1, {
            "and": [fixtures.apply({"ref": lemma["id"]}, {"use": 0}), {"use": 0}],
        })}
        original_query = Store.query

        def omit_audited_reference(store, role, query, *params):
            if query.startswith("SELECT id FROM rm.record_verified_lean("):
                statement, digest, report = params
                aliases = report.obj["dependencies"]["direct_constants"]
                self.assertIn("RMTheory.Proof.basis_local_" + lemma["id"].replace("-", ""), aliases)
                params = (statement, digest, store.json({
                    **report.obj, "local_lemma_ids": [], "expanded_local_lemma_ids": [],
                }))
            return original_query(store, role, query, *params)

        with patch.dict(os.environ, case.fixture.env):
            with patch.object(Store, "query", omit_audited_reference):
                with self.assertRaisesRegex(ValueError, "does not match the Lean audit references"):
                    final_lean_local.check(anchor["id"], submission, case.fixture.output)
            self.assertEqual(case.cli("review", "inbox", "--claim", scope["claim_id"]), [])
            recovered = final_lean_local.check(anchor["id"], submission, case.fixture.output)
            self.assertEqual(recovered["local_lemma_ids"], [lemma["id"]])
            with Store() as store:
                links = store.query("verifier", """
                    SELECT use.lemma_id FROM rm.evidence e
                    JOIN rm.verification_local_lemma_use use ON use.check_id = e.lean_check_id
                    WHERE e.id = %s
                """, recovered["evidence_id"])
            self.assertEqual([str(row["lemma_id"]) for row in links], [lemma["id"]])
