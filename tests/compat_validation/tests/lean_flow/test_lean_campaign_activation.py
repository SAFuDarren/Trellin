"""Exercise the actual Lean campaign, researcher activation and public worker."""

import os
import unittest

import test_lean_flow
import test_lean_shadow as shadow_fixtures


@unittest.skipUnless(test_lean_flow.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanCampaignActivationTests(unittest.TestCase):
    def test_reviewed_holdout_changes_the_worker_and_rolls_back(self):
        shadow = shadow_fixtures.LeanShadowTests(methodName="test_pair_verifies_exact_goal_and_fences_mixed_workspace")
        self.addCleanup(shadow.doCleanups)
        shadow.setUp()
        cli = shadow.case.cli
        adapter = shadow.root / "fixed_lean_campaign_activation.py"
        adapter.write_text(shadow_fixtures.ADAPTER, encoding="utf-8")
        env = shadow.fixture.env
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(shadow.root), env.get("PYTHONPATH"))))
        for prefix, model in (
            ("RESEARCH_MULTIPLIER_LEAN_AUTHOR_", "extra-hypothesis"),
            ("RESEARCH_MULTIPLIER_PROFILE_SHADOWTEST_", "exact"),
        ):
            env.update({prefix + field: value for field, value in {
                "PROVIDER": "fixed-shadow", "MODEL": model, "FAMILY": "fixed-shadow",
                "ADAPTER": "fixed_lean_campaign_activation:factory",
            }.items()})

        proposition = "∀ n : Nat, n = n"
        proof, _, _ = shadow.case.anchor("Campaign true holdout", proposition, [], sensitivity="public")
        refutation, _, _ = shadow.case.anchor(
            "Campaign false holdout", "∀ n : Nat, n = 0", [], sensitivity="public",
        )
        archive = shadow.backup()
        dev, _, _ = shadow.case.anchor("development-only theorem", "∀ n : Nat, n + 0 = n", [], sensitivity="public")
        cases = []
        for case_id, anchor, expected in (("proof", proof, "verified"), ("refutation", refutation, "refuted")):
            copies = shadow.restore_pair(archive, case_id=case_id)
            cases.append({"id": case_id, "baseline_workspace": str(copies["BASELINE"]),
                          "candidate_workspace": str(copies["CANDIDATE"]),
                          "statements": [anchor["id"]], "expected": [expected]})
        corpus = shadow.case.document("activation_corpus", {
            "format_version": 1,
            "development": [{"statement_id": dev["id"], "expected": "verified"}],
            "holdout": [{"statement_id": case["statements"][0], "expected": case["expected"][0]} for case in cases],
        })
        seal = cli("eval", "lean-corpus-seal", corpus, "--actor", "reviewer",
                   "--reason", "Review disjoint inputs before the experiment.")
        manifest = shadow.case.document("activation_holdout", {"format_version": 1, "split": "holdout", "cases": cases})
        report = cli("eval", "shadow-lean-campaign", manifest, "--candidate-profile", "SHADOWTEST", "--rounds", 1,
                     "--confirm-public", "--max-model-calls", 4, "--corpus-sha256", seal["artifact"]["sha256"])
        self.assertEqual(report["evaluation_corpus_sha256"], seal["artifact"]["sha256"])
        self.assertTrue(report["source_unchanged"])
        self.assertEqual(report["totals"]["candidate"]["pending_scope_decisions"], 0)
        self.assertEqual(report["totals"]["candidate"]["pending_evidence_reviews"], 2)
        self.assertEqual(report["totals"]["baseline"]["correct"], 1)
        self.assertEqual(report["totals"]["candidate"]["correct"], 2)
        self.assertIsNone(cli("eval", "lean-active")["active"])

        measurements = [
            {"case_id": case["id"], "baseline_minutes": "1", "candidate_minutes": "1",
             "baseline_invalid_receipts": 0, "candidate_invalid_receipts": 0}
            for case in cases
        ]
        rejected_review = shadow.case.document("activation_review_invalid", {"format_version": 1, "cases": [
            {**measurements[0], "candidate_invalid_receipts": 1}, measurements[1],
        ]})
        rejected = cli("eval", "lean-activate", report["report"]["sha256"], rejected_review, "--actor", "reviewer",
                       "--reason", "Record one suspected invalid receipt.", expect=2)
        self.assertIn("receipt error", rejected)
        self.assertIsNone(cli("eval", "lean-active")["active"])
        review = shadow.case.document("activation_review", {"format_version": 1, "cases": measurements})
        activated = cli("eval", "lean-activate", report["report"]["sha256"], review, "--actor", "reviewer",
                        "--reason", "Both correct outcomes and their time measurements reviewed.")
        self.assertEqual(activated["effective"]["profile"], "SHADOWTEST")
        self.assertEqual(activated["measurements"]["baseline"]["exact"], 1)
        self.assertEqual(activated["measurements"]["candidate"]["exact"], 2)

        def run(label):
            target, _, _ = shadow.case.anchor(
                label, proposition, [], sensitivity="public", statement_text=f"{label}: equality is reflexive on Nat.",
            )
            policy = cli("advance", "propose", "--for-packet", target["packet_id"], "--lean-model-rounds", 1)
            cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
                "--actor", "reviewer", "--reason", "Approve one fixture model reservation.")
            human = env.pop("RM_HUMAN_DSN")
            try:
                delivery = cli("advance", "worker", "--policy", policy["id"], "--parallel", 1, "--limit", 1)
            finally:
                env["RM_HUMAN_DSN"] = human
            self.assertFalse(delivery["errors"], delivery)
            [outcome] = delivery["lean_runs"]
            [call] = cli("advance", "metrics", policy["id"], "--details")["model"]["calls"]
            return outcome, call

        outcome, call = run("Worker uses activated Lean author")
        self.assertEqual(outcome["lean_route_event_id"], activated["event_id"])
        self.assertEqual(outcome["status"], "awaiting_evidence_review")
        self.assertEqual(call["model_id"], "exact")
        restored = cli("eval", "lean-rollback", activated["event_id"], "--actor", "reviewer",
                       "--reason", "Verify that the exact prior author becomes effective again.")
        self.assertIsNone(restored["effective"]["profile"])
        self.assertEqual(cli("eval", "lean-active")["active"]["id"], restored["event_id"])

        outcome, call = run("Worker uses restored Lean author")
        self.assertEqual(outcome["lean_route_event_id"], restored["event_id"])
        self.assertEqual(outcome["status"], "budget_exhausted")
        self.assertEqual(call["model_id"], "extra-hypothesis")
