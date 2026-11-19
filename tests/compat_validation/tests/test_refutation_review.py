"""Send only current, exact counterexample evidence to the original claim's review queue."""

import json
import os
import unittest
from unittest.mock import patch

import test_production as fixtures


DSN = os.environ.get("RM_TEST_DSN")


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class RefutationReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ProductionTests.setUpClass()
        cls.addClassCleanup(fixtures.ProductionTests.doClassCleanups)

    def setUp(self):
        self.fixture = fixtures.ProductionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.files = self.fixture.root

    def document(self, name, body):
        path = self.files / name
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_model_route_screens_false_target_then_issues_a_checked_refutation_without_a_model(self):
        cli = self.fixture.cli
        source = json.loads(self.fixture.network_input.read_bytes())
        source["target_value"] = 24
        network = self.document("network.json", source)
        with patch.dict(os.environ, self.fixture.env):
            result = cli("formalization", "draft", network, "--title", "Frozen false optimum")
            draft = json.loads(result.stdout)
            self.fixture.confirm_formalization(draft)
            proof = json.loads(cli("formalization", "prove", draft["id"], "--strategy", "model").stdout)
            entry, = json.loads(cli("review", "inbox", "--claim", draft["claim_id"]).stdout)
            self.assertEqual(entry["item_id"], proof["evidence_id"])
            self.assertIn("refutes the frozen target", entry["question"])
            report = json.loads(cli("review", "packet", entry["item_id"]).stdout)
            self.assertEqual(report["refutation"]["mode"], "exact_certificate")
            self.assertEqual(report["claim_status"], "open")
            self.assertEqual(report["verification"]["review_status"], "pending")
            self.assertTrue(all(attempt["model_id"] is None for attempt in report["verification"]["attempts"]))
            certificate = report["contract_comparison"]["verified_result"]
            self.assertEqual((certificate["value"], certificate["target_value"]), (23, 24))
            run = self.files / draft["claim_id"] / "run.json"
            cli("replay", run)
            retry = json.loads(cli("formalization", "prove", draft["id"]).stdout)
            self.assertEqual(retry["evidence_id"], proof["evidence_id"])

    def test_witness_from_a_relaxed_problem_never_creates_a_review_item(self):
        cli = self.fixture.cli
        model = self.document("lp.json", {"A": [[1]], "b": [2], "c": [1], "target_value": 3})
        wrong = self.document("relaxed.json", {"primal": [4], "dual": [1]})
        correct = self.document("original.json", {"primal": [2], "dual": [1]})
        with patch.dict(os.environ, self.fixture.env):
            draft = json.loads(cli("formalization", "draft", model, "--domain", "linear-program",
                                   "--title", "Frozen linear program").stdout)
            self.fixture.confirm_formalization(draft)
            cli("formalization", "prove", draft["id"], "--certificate", wrong, expect=2)
            self.assertEqual(json.loads(cli("review", "inbox", "--claim", draft["claim_id"]).stdout), [])
            valid = json.loads(cli("formalization", "prove", draft["id"], "--certificate", correct).stdout)
            entry, = json.loads(cli("review", "inbox", "--claim", draft["claim_id"]).stdout)
            self.assertEqual(entry["item_id"], valid["evidence_id"])
