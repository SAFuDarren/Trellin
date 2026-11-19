"""Expose interpretation holds without erasing an already checked Lean proof."""

import unittest

import test_final_local_lemmas as fixtures
from test_lean_flow import DSN


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class InterpretationStatusTests(unittest.TestCase):
    def test_open_and_adopted_receipts_follow_the_same_interpretation_hold(self):
        fixture = fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        premise = {"kind": "hypothesis", "natural": "Truth is the premise in the interpretation-status case.",
                   "formal": {"lean": {"proposition": "True"}}}
        anchor, scope, _ = fixture.anchor("Interpretation status after Lean review", "True ∧ True",
                                          [premise], sensitivity="public")
        goal, claim = scope["goal_id"], scope["claim_id"]
        plan = cli("advance", "propose", "--for-packet", scope["packet_id"])
        cli("advance", "approve", plan["id"], "--hash", plan["policy_sha256"],
            "--actor", "researcher", "--reason", "Watch exact closure of this frozen proposition.")
        proof = fixture.document("interpretation-proof", {"format_version": 1, "proof": fixtures.intro(
            1, {"and": [{"use": 0}, {"use": 0}]},
        )})
        result = cli("lean", "check-theory", anchor["id"], proof, "--out", fixture.fixture.output)
        receipt = result["evidence_id"]
        self.assertEqual([entry["item_id"] for entry in cli("review", "inbox", "--claim", claim)], [receipt])

        def question(kind, name):
            document = fixture.document(name, {
                "kind": kind, "statement": "False", "formal": {"lean": {"proposition": "False"}},
                "question": name, "impact": "Would change what the original premise permits.",
            })
            return cli("goal", "condition", goal, document)["id"]

        def holds(recorded):
            expected = "awaiting_interpretation_review"
            packet = cli("review", "packet", receipt)
            self.assertEqual(packet["claim_status"], expected)
            self.assertEqual(packet["recorded_claim_status"], recorded)
            self.assertTrue(packet["interpretation_hold"])
            self.assertFalse(packet["verification"]["needs_recheck"])
            status = cli("research", "evidence", claim)["claim"][0]
            self.assertEqual(status["status"], expected)
            self.assertEqual(status["recorded_status"], recorded)
            self.assertTrue(status["interpretation_hold"])
            frame = cli("goal", "show", goal)["frame"]
            self.assertEqual(frame["claim_status"], expected)
            self.assertEqual(frame["recorded_claim_status"], recorded)
            self.assertTrue(frame["interpretation_hold"])
            self.assertFalse(frame["proof_ready"])
            progress = cli("advance", "status", plan["id"])["status"]
            self.assertEqual(progress["claim_status"], expected)
            self.assertTrue(progress["interpretation_hold"])
            self.assertEqual(progress["state"], "scope_unavailable")
            self.assertNotIn(receipt, [row["item_id"] for row in cli("review", "inbox", "--claim", claim)])

        def resolve(identifier):
            cli("goal", "decide-condition", identifier, "reject", "--actor", "researcher",
                "--reason", "The frozen premise already captures the intended meaning.")

        question("route_condition", "Could a different proof route use this proposition?")
        self.assertFalse(cli("goal", "show", goal)["frame"]["interpretation_hold"])
        first = question("interpretation", "Does this reading change the frozen premise?")
        holds("open")
        resolve(first)
        self.assertEqual(cli("review", "packet", receipt)["claim_status"], "open")

        cli("review-evidence", receipt, "approve", "--actor", "researcher", "--reason", "Review the exact proof.")
        cli("promote-evidence", receipt, "--reason", "Adopt the proof under current scope.")
        self.assertEqual(cli("review", "packet", receipt)["claim_status"], "proved")
        self.assertIn(receipt, [entry["evidence_id"] for entry in cli("lean", "theorems")])

        reopened = question("interpretation", "Does a later ambiguity change the frozen meaning?")
        holds("proved")
        self.assertIn(reopened, [entry["item_id"] for entry in cli("review", "inbox", "--claim", claim)])
        self.assertNotIn(receipt, [entry["evidence_id"] for entry in cli("lean", "theorems")])
        resolve(reopened)
        restored = cli("research", "evidence", claim)["claim"][0]
        self.assertEqual(restored["status"], "proved")
        self.assertFalse(restored["interpretation_hold"])
        self.assertEqual(restored["decisive_evidence_id"], receipt)
        self.assertIn(receipt, [entry["evidence_id"] for entry in cli("lean", "theorems")])
