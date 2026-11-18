"""Exercise one scaffolding omission with the normal sealed evaluation entry point."""

import unittest

import test_lean_flow as lean_fixtures
import test_lean_shadow as shadow_fixtures


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanComponentAblationTests(unittest.TestCase):
    def test_omits_working_replay_without_changing_the_checker_or_source(self):
        fixture = shadow_fixtures.LeanShadowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        case = fixture.case
        source, _, _ = case.anchor("Reusable addition control", "True", [], sensitivity="public")
        [lemma] = case.cli("lean", "derive", source["id"], case.document("ablation_working_source", {
            "format_version": 1, "nodes": [{"name": "AdditionZero", "proposition": "∀ n : Nat, n + 0 = n",
                "proof": {"intro": {"count": 1, "body": {"rfl": True}}}}],
        }))["lemmas"]

        development = []
        for number in range(1, 11):
            anchor, _, _ = case.anchor(f"Ablation development {number}",
                                       f"∀ n : Nat, n + 0 = n ∧ n + {number} = n + {number}", [], sensitivity="public")
            development.append({"statement_id": anchor["id"], "expected": "verified"})
        working, scope, _ = case.anchor("Holdout needing verified working lemma", "∀ n : Nat, n + 0 = n ∧ True",
                                       [], sensitivity="public")
        false, _, _ = case.anchor("Holdout refutation control", "∀ n : Nat, n + 1 = n", [], sensitivity="public")
        holdout = [{"statement_id": working["id"], "expected": "verified"},
                   {"statement_id": false["id"], "expected": "refuted"}]

        def seal(name, items):
            document = case.document(name, {"format_version": 1, "development": items, "holdout": holdout})
            return case.cli("eval", "lean-corpus-seal", document, "--actor", "independent reviewer",
                            "--reason", "Review frozen paired ablation controls.")["artifact"]["sha256"]

        small = seal("undersized_ablation_corpus", development[:1])
        reviewed = seal("reviewed_ablation_corpus", development)
        archive = fixture.backup()
        twins = fixture.restore_pair(archive, case_id="reuse")
        manifest = case.document("paired_ablation", {"format_version": 1, "split": "holdout", "cases": [{
            "id": "reuse", "baseline_workspace": str(twins["BASELINE"]), "candidate_workspace": str(twins["CANDIDATE"]),
            "statements": [entry["statement_id"] for entry in holdout],
            "expected": [entry["expected"] for entry in holdout],
        }]})
        command = ("eval", "shadow-lean-campaign", manifest, "--rounds", 0,
                   "--ablate", "cross-claim-working-reuse")
        self.assertIn("needs a reviewed corpus seal", case.cli(*command, expect=2))
        self.assertIn("at least ten distinct", case.cli(*command, "--corpus-sha256", small, expect=2))
        command += ("--corpus-sha256", reviewed)
        self.assertIn("Change exactly one experimental variable", case.cli(*command, "--candidate-strategy", "legacy",
                                                                           expect=2))
        report = case.cli(*command)
        self.assertEqual(report["comparison_mode"], "component_ablation")
        self.assertTrue(report["source_unchanged"])
        self.assertFalse(report["activation_eligible"])
        self.assertEqual(report["evaluation_corpus_sha256"], reviewed)
        self.assertEqual(report["ablation"], {
            "name": "cross-claim-working-reuse", "version": "1", "omitted_side": "candidate",
            "reviewed_development_cases": 10, "paired_outcomes": {"both_correct": 1,
                "enabled_only_correct": 1, "omitted_only_correct": 0, "neither_correct": 0, "unmeasured_pairs": 0},
        })
        sides = report["cases"][0]["steps"][0]["sides"]
        self.assertEqual(sides["baseline"]["implementation_sha256"], sides["candidate"]["implementation_sha256"])
        self.assertTrue(sides["baseline"]["component_control"]["enabled"])
        self.assertFalse(sides["candidate"]["component_control"]["enabled"])
        self.assertEqual(sides["baseline"]["result"]["outcome"], "verified")
        self.assertEqual(sides["candidate"]["result"]["outcome"], "no_result")
        self.assertTrue(any(item.get("source_lemma_id") == lemma["id"] and item.get("status") == "lemma_verified"
                            for worker_pass in sides["baseline"]["result"]["passes"]
                            for item in worker_pass.get("program_obligations", [])))
        for side in ("baseline", "candidate"):
            self.assertEqual(report["totals"][side]["reserved_calls"], 0)
        self.assertEqual(case.cli("research", "tasks", scope["claim_id"])["tasks"], [])
        note = case.document("ablation_review", {"format_version": 1, "cases": []})
        self.assertIn("component ablation is diagnostic", case.cli(
            "eval", "lean-activate", report["report"]["sha256"], note, "--actor", "independent reviewer",
            "--reason", "verify ablation activation guard", expect=2))
