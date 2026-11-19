"""Check a program-selected finite witness through Lean and the evidence workflow."""

import unittest

import test_final_local_lemmas as final_fixtures
from test_lean_flow import DSN

from research_multiplier.proof.lean_counterexamples import candidate


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FiniteCounterexampleIntegrationTests(unittest.TestCase):
    def test_witness_refutes_the_frozen_implication_under_its_original_hypothesis(self):
        fixture = final_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        target = "∀ n : Nat, n ≤ 2 → n + 1 ≤ 2"
        hypothesis = "(1 : Nat) = 1"
        anchor, scope, _ = fixture.anchor(
            "A boundary witness for the frozen implication", target, [{
                "kind": "hypothesis", "natural": "One equals itself in the naturals.",
                "formal": {"lean": {"proposition": hypothesis}},
            }], sensitivity="public",
        )
        selected = candidate({"consequent": target, "antecedents": [hypothesis]})
        self.assertEqual(selected["witness"]["assignments"], [{"variable": "n", "type": "Nat", "value": 2}])
        policy = cli("advance", "propose", "--for-packet", anchor["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Approve a deterministic inquiry on the frozen scope.")
        worked = cli("advance", "worker", "--policy", policy["id"])
        self.assertEqual(worked["errors"], [], worked)
        run, = worked["lean_runs"]
        self.assertEqual(run["status"], "awaiting_evidence_review")
        self.assertEqual((run["target"], run["method"]), ("refutation", "finite_counterexample"))
        self.assertEqual(run["witness"], selected["witness"])
        self.assertNotIn("rounds", run)
        evidence = cli("review", "packet", run["evidence_id"])
        self.assertEqual(evidence["claim_status"], "open")
        self.assertEqual(evidence["verification"]["review_status"], "pending")
        self.assertEqual(evidence["verification"]["verdict"], "verified")
        negation = evidence["contract_comparison"]["verified_result"]["negated_type"]
        self.assertIn("Not (Eq 1 1 → ∀", negation)
        resume = cli("research", "resume", scope["claim_id"])
        self.assertEqual([item["kind"] for item in resume["decisions"]["items"]], ["review_evidence"])
        self.assertEqual(cli("advance", "worker", "--policy", policy["id"])["lean_runs"], [])

    def test_truth_table_refutes_symmetry_implying_transitivity_and_metrics_are_exact(self):
        fixture = final_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        target = ("∀ (R : Bool → Bool → Prop), (∀ a b : Bool, R a b → R b a) → "
                  "∀ a b c : Bool, R a b → R b c → R a c")
        guard = "∀ b : Bool, b = b"
        anchor, scope, _ = fixture.anchor(
            "Boolean relation satisfying symmetry alone", target, [{
                "kind": "hypothesis", "natural": "Equality is reflexive on Bool.",
                "formal": {"lean": {"proposition": guard}},
            }], sensitivity="public",
        )
        policy = cli("advance", "propose", "--for-packet", anchor["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Check the exact finite relation scope.")
        report = cli("advance", "worker", "--policy", policy["id"])
        self.assertEqual(report["errors"], [], report)
        run, = report["lean_runs"]
        witness = run["witness"]
        self.assertEqual(witness["frozen_hypotheses"], 1)
        self.assertEqual(witness["guard_count"], 3)
        self.assertEqual(witness["assignments"][0]["value"], [False, True, True, False])
        self.assertEqual(run["status"], "awaiting_evidence_review")
        self.assertGreater(run["worker_elapsed_ms"], 0)
        receipt = cli("review", "packet", run["evidence_id"])
        self.assertEqual(receipt["contract_comparison"]["scope_status"], "exact")
        self.assertIn("Not", receipt["contract_comparison"]["verified_result"]["negated_type"])
        self.assertEqual(receipt["claim_id"], scope["claim_id"])
        metrics = cli("advance", "metrics", policy["id"], "--details")
        self.assertEqual(metrics["passes"]["completed_passes"], 1)
        self.assertEqual(metrics["passes"]["timed_passes"], 1)
        self.assertEqual(int(metrics["passes"]["worker_elapsed_ms"]), run["worker_elapsed_ms"])
        self.assertEqual(metrics["model"]["reservation_count"], 0)
        self.assertIsNone(metrics["model"]["cost_usd"]["reported_total"])
        self.assertEqual(metrics["pending_human_decisions"]["count"], 1)
