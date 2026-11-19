"""Exercise unattended local lemma admission and revocation with native Lean and PostgreSQL."""

import json
import os
import unittest
from unittest.mock import patch

import test_lean_flow as fixtures

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LocalLemmaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LeanFlowIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cli = self.fixture.cli

    def document(self, name, body):
        path = self.fixture.output / f"{name}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def anchor(self):
        target = self.document("target", {
            "title": "Restricted repeated use of one arithmetic premise", "domain": "lean-core",
            "sensitivity": "restricted", "statement": "Four copies of the original equality hold.",
            "formal": {"lean": {"proposition": "(2 = 2 ∧ 2 = 2) ∧ (2 = 2 ∧ 2 = 2)"}},
        })
        preview = self.cli("intake", "preview-target", target)
        frozen = self.cli("intake", "freeze-target", target, "--hash", preview["target_sha256"],
                          "--actor", "researcher", "--reason", "Match the frozen consequent.")
        notes = self.document("starting-basis", {"items": [{
            "kind": "hypothesis", "natural": "The starting premise is two equals two.",
            "formal": {"lean": {"proposition": "2 = 2"}},
        }]})
        basis = self.cli("intake", "draft", notes, "--target", frozen["id"])
        decisions = self.document("decisions", {"i1": {"action": "accept", "reason": "Matches the premise."}})
        self.cli("intake", "confirm", basis["id"], "--hash", basis["intake_sha256"], "--decisions", decisions,
                 "--actor", "researcher", "--reason", "Check the single starting premise.")
        scope = self.cli("intake", "target", frozen["id"])
        anchor = self.cli("lean", "draft-theory", scope["packet_id"])
        self.cli("lean", "confirm", anchor["id"], "--version", anchor["version"], "--hash", anchor["statement_sha256"],
                 "--actor", "researcher", "--reason", "Check the exact local Lean elaboration.")
        basis_rows = self.cli("research", "basis", scope["packet_id"])
        premise, = [row for row in basis_rows if row["body"]["kind"] == "hypothesis"]
        return anchor, premise.get("id", premise.get("item_version_id"))

    def test_unattended_admission_exact_reuse_and_withdrawal(self):
        anchor, premise_id = self.anchor()
        self.assertIsNotNone(premise_id)
        statement = anchor["id"]
        first = {"name": "L1", "proposition": "2 = 2 ∧ 2 = 2",
                 "proof": {"intro": {"count": 1, "body": {"and": [{"use": 0}, {"use": 0}]}}}}
        second = {"name": "L2", "proposition": "2 = 2 ∧ (2 = 2 ∧ 2 = 2)",
                  "proof": {"intro": {"count": 1, "body": {
                      "and": [{"use": 0}, {"apply": [{"node_ref": "L1"}, {"use": 0}]}],
                  }}}}
        no_new_premise = {"name": "extra", "proposition": "False",
                          "proof": {"intro": {"count": 2, "body": {"use": 1}}}}
        bad = self.document("extra-premise", {"format_version": 1, "nodes": [no_new_premise]})
        smuggled = self.document("insert-premise", {"format_version": 1, "nodes": [
            {**no_new_premise, "hypotheses": ["False"]},
        ]})
        plan = self.document("derive", {"format_version": 1, "nodes": [first, second]})
        human = self.fixture.env.pop("RM_HUMAN_DSN")
        try:
            self.assertIn("distinct name, exact proposition and structured proof",
                          self.cli("lean", "derive", statement, smuggled, expect=2))
            self.assertIn("Lean", self.cli("lean", "derive", statement, bad, expect=2))
            self.assertEqual(self.cli("lean", "local-theorems", statement), [])
            interrupted = self.document("partly-rejected", {"format_version": 1, "nodes": [first, no_new_premise]})
            self.cli("lean", "derive", statement, interrupted, expect=2)
            saved, = self.cli("lean", "local-theorems", statement)
            result = self.cli("lean", "derive", statement, plan)
            self.assertEqual(result["lemmas"][0]["id"], saved["id"])
            l1, l2 = result["lemmas"]
            self.assertEqual([l1["status"], l2["status"]], ["verified", "verified"])
            self.assertEqual(l1["body"]["local_lemma_ids"], [])
            self.assertEqual(l2["body"]["local_lemma_ids"], [l1["id"]])
            self.assertEqual(l2["body"]["expanded_local_lemma_ids"], [l1["id"]])
            for lemma in (l1, l2):
                body = lemma["body"]
                self.assertTrue(body["kernel_rechecked"] and body["scope_matches"])
                self.assertEqual(body["dependencies"]["unauthorized_axioms"], [])
                self.assertEqual(body["dependencies"]["leading_binder_occurrence"], [True])
            self.assertIn("Lean", self.cli("lean", "derive", statement, plan, "--lean",
                                           self.fixture.output / "missing-lean", expect=2))
            replay = self.cli("lean", "derive", statement, plan)["lemmas"]
            self.assertEqual([entry["id"] for entry in replay], [l1["id"], l2["id"]])
            second["proof"]["intro"]["body"]["and"][1]["apply"][0] = {"ref": l1["id"]}
            persisted = self.document("reuse", {"format_version": 1, "nodes": [second]})
            self.assertEqual(self.cli("lean", "derive", statement, persisted)["lemmas"][0]["id"], l2["id"])

            with patch.dict(os.environ, self.fixture.env), Store() as store:
                with self.assertRaisesRegex(ValueError, "permission denied"):
                    store.query("scheduler", "SELECT rm.record_local_lean_lemma(%s,%s)",
                                l1["verification_attempt_id"], store.json(l1["body"]))
                bogus = {**l1["body"], "dependencies": {
                    **l1["body"]["dependencies"], "axioms": ["example.unapproved"],
                }}
                with self.assertRaisesRegex(ValueError, "dependency-checked verifier receipt"):
                    store.query("verifier", "SELECT rm.record_local_lean_lemma(%s,%s)",
                                l1["verification_attempt_id"], store.json(bogus))
                jobs = store.query("verifier", """
                    SELECT a.finding, a.model_provider, j.budget ->> 'max_model_calls' AS model_budget
                    FROM rm.attempts a JOIN rm.jobs j ON j.id = a.job_id
                    WHERE j.payload ->> 'lean_statement_id' = %s AND j.payload ->> 'target' = 'local_lemma'
                """, statement)
            self.assertCountEqual([job["finding"] for job in jobs], ["no_result", "exact_proof", "exact_proof"])
            self.assertTrue(all(job["model_provider"] is None and job["model_budget"] == "0" for job in jobs))
        finally:
            self.fixture.env["RM_HUMAN_DSN"] = human

        self.assertEqual(self.cli("review", "inbox", "--claim", anchor["claim_id"]), [])
        self.cli("research", "retire-input", "source", l1["body"]["candidate"]["sha256"],
                 "--reason", "Withdraw just the first verified proof.")
        recorded = self.cli("lean", "local-theorems", statement)
        self.assertEqual({l["id"]: l["status"] for l in recorded}, {
            l1["id"]: "needs_recheck", l2["id"]: "needs_recheck",
        })
        self.assertIn("needs recheck", self.cli("lean", "derive", statement, persisted, expect=2))

        independent = self.document("independent", {"format_version": 1, "nodes": [
            {"name": "other", "proposition": "2 = 2 ∨ False",
             "proof": {"intro": {"count": 1, "body": {"left": {"use": 0}}}}},
        ]})
        other = self.cli("lean", "derive", statement, independent)["lemmas"][0]
        self.assertEqual(other["status"], "verified")
        self.cli("research", "retire-item", premise_id, "--reason", "Withdraw the original approved premise.")
        self.assertEqual({l["status"] for l in self.cli("lean", "local-theorems", statement)}, {"needs_recheck"})
