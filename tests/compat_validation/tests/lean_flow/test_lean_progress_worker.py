"""Run signed, program-first Lean advancement through the installed CLI."""

import json
import unittest

import test_final_local_lemmas as fixtures
import test_lean_flow as database


def premise(text, description):
    return {"kind": "hypothesis", "natural": description, "formal": {"lean": {"proposition": text}}}


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanProgressWorkerTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli

    def policy(self, statement):
        route = self.cli("advance", "route", statement["packet_id"])
        self.assertEqual(route["route"], "lean_advancement")
        policy = self.cli("advance", "propose", "--for-packet", statement["packet_id"])
        self.assertEqual(policy["policy"]["lean"]["statement_id"], statement["id"])
        self.assertEqual(policy["policy"]["lean"]["model_rounds"], 0)
        return policy

    def approve(self, policy):
        self.cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
                 "--actor", "researcher", "--reason", "Allow only deterministic Lean work in the frozen scope.")

    def work(self, policy):
        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            return self.cli("advance", "worker", "--policy", policy["id"])
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human

    def test_policy_advances_without_model_and_leaves_failed_search_out_of_review(self):
        conclusion = "(1 : Nat) = 0"
        source, _, _ = self.case.anchor(
            "native-worker-origin", conclusion,
            [premise("(0 : Nat) = 1", "Assume zero equals one in the original conditional result.")],
            statement_text="Reverse the source's given equality.",
        )
        path = self.case.fixture.workspace.parent / "native-worker.lean"
        path.write_text("theorem originalWorker (h : (0 : Nat) = 1) : (1 : Nat) = 0 := Eq.symm h\n",
                        encoding="utf-8")
        proof = self.cli("lean", "check-source", source["id"], path, "--declaration", "originalWorker")
        self.cli("review-evidence", proof["evidence_id"], "approve", "--actor", "researcher",
                 "--reason", "Reviewed the exact conditional result.")
        self.cli("promote-evidence", proof["evidence_id"], "--reason", "Retain the original verified proof.")

        destination, destination_scope, _ = self.case.anchor(
            "native-worker-ready", conclusion,
            [premise("True ∧ (True ∧ ((0 : Nat) = 1))", "The required equality is nested in the reviewed context.")],
            statement_text="Reverse the equality contained in a nested conjunction.",
        )
        policy = self.policy(destination)
        self.assertEqual(self.work(policy)["lean_runs"], [])
        self.approve(policy)
        run, = self.work(policy)["lean_runs"]
        self.assertEqual(run["status"], "awaiting_evidence_review", run)
        received = self.cli("review", "packet", run["evidence_id"])
        self.assertEqual(received["claim_id"], destination_scope["claim_id"])
        self.assertEqual(self.work(policy)["lean_runs"], [])

        blocked, blocked_scope, _ = self.case.anchor(
            "native-worker-route-obstacle", f"∀ f : Nat → Nat, {conclusion}",
            [premise("True", "Only truth is available in the comparison scope.")],
            statement_text="Try the reverse equality when quantified over all natural-number functions.",
        )
        waiting = self.policy(blocked)
        self.approve(waiting)
        report, = self.work(waiting)["lean_runs"]
        self.assertNotIn("evidence_id", report)
        self.assertEqual(self.work(waiting)["lean_runs"], [])
        status = self.cli("advance", "status", waiting["id"])["status"]["lean_run"]
        self.assertIsNotNone(status)
        resume = self.cli("research", "resume", blocked_scope["claim_id"], "--details")
        self.assertIn(report["status"], json.dumps(resume))
        self.assertNotIn("decide_condition", json.dumps(resume))
        self.assertNotIn("review_lean_scope_change", json.dumps(resume))
