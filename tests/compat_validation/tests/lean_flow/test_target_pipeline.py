"""Use the reviewed target and basis, even for a convincing negative result."""

from functools import partial
import json
import os
import unittest
from unittest.mock import patch

import test_lean_flow as fixtures

from research_multiplier import general_lean
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class TargetPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LeanFlowIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cli = self.fixture.cli
        self.root = self.fixture.output

    def document(self, name, **body):
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def test_two_exact_approvals_and_a_checked_refutation_leave_the_original_open(self):
        target = self.document("target", title="False under a true assumption", domain="lean-core",
                               sensitivity="restricted", statement="False holds.",
                               formal={"lean": {"proposition": "False"}})
        preview = self.cli("intake", "preview-target", target)
        frozen = self.cli("intake", "freeze-target", target, "--hash", preview["target_sha256"],
                          "--actor", "researcher", "--reason", "Checked exact consequent")
        self.assertIsNone(frozen["claim_id"])
        notes = self.document("notes", items=[{
            "kind": "hypothesis", "natural": "True holds as the original premise.",
            "formal": {"lean": {"proposition": "True"}},
        }])
        basis = self.cli("intake", "draft", notes, "--target", frozen["id"])
        decisions = self.document("basis-approval", i1={"action": "accept", "reason": "Matches the original premise"})
        self.cli("intake", "confirm", basis["id"], "--hash", basis["intake_sha256"], "--decisions", decisions,
                 "--actor", "researcher", "--reason", "Reviewed the exact starting basis")
        checked_target = self.cli("intake", "target", frozen["id"])
        condition = {
            "kind": "interpretation", "statement": "Assume False.",
            "formal": {"lean": {"proposition": "False"}},
            "question": "Can the route assume False?", "impact": "Makes any goal derivable.",
        }
        with patch.dict(os.environ, self.fixture.env), Store() as store:
            with self.assertRaisesRegex(ValueError, "research decision|human role|only a researcher"):
                store.query("scheduler", "SELECT rm.request_research_condition(%s,%s)",
                            checked_target["goal_id"], store.json(condition))
            with self.assertRaisesRegex(ValueError, "research decision"):
                store.query("scheduler", "SELECT rm.request_clarification(%s,'goal','Add false?','Changes the scope')",
                            checked_target["packet_id"])
            store.query("human", "SELECT rm.request_research_condition(%s,%s)", checked_target["goal_id"],
                        store.json({**condition, "kind": "route_condition"}))
        self.assertEqual(self.cli("review", "inbox", "--claim", checked_target["claim_id"]), [])
        lean = self.cli("lean", "draft-theory", checked_target["packet_id"])
        self.cli("lean", "confirm", lean["id"], "--version", lean["version"], "--hash", lean["statement_sha256"],
                 "--actor", "researcher", "--reason", "Reviewed local Lean elaboration")
        wrong = self.document("bad", format_version=1, proof={"intro": {"count": 1, "body": {"trivial": True}}})
        prove = partial(self.cli, "lean", "check-theory", lean["id"])
        prove(wrong, "--refute", "--out", self.root, expect=2)
        self.assertEqual(self.cli("review", "inbox", "--claim", checked_target["claim_id"]), [])
        negative = self.document("negative", format_version=1, proof={
            "intro": {"count": 1, "body": {"apply": [{"use": 0}, {"trivial": True}]}},
        })
        receipt = prove(negative, "--refute", "--out", self.root)
        task, = self.cli("review", "inbox", "--claim", checked_target["claim_id"])
        self.assertEqual(task["item_id"], receipt["evidence_id"])
        self.assertIn("refutes the frozen target", task["question"])
        report = self.cli("review", "packet", receipt["evidence_id"])
        self.assertEqual(report["claim_status"], "open")
        self.assertEqual(report["refutation"]["mode"], "logical_negation")
        self.assertIsNone(report["refutation"]["witness"])
        self.assertEqual(report["contract_comparison"]["frozen_conclusion"]["statement"], "False holds.")
        self.assertEqual(report["verification"]["review_status"], "pending")

    def test_native_auditor_rejects_an_unregistered_imported_fact(self):
        lean, runtime = general_lean._runtime(None, fixtures.PROJECT, ["Mathlib.Data.Real.Basic"])
        auditor, _ = general_lean._environment(lean, runtime)
        goal = "∀ x : Real, 0 ≤ x ^ 2"
        reviewed_source = general_lean._type_source(goal, [], runtime.manifest["modules"])
        with self.assertRaisesRegex(ValueError, "dependency policy rejected"):
            general_lean._check(lean, auditor, goal, [], reviewed_source, "(fun x : Real => sq_nonneg x)",
                                {}, mathlib=runtime)
