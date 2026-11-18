"""Check sealed, paired component evaluations through the existing evaluation gate."""

from argparse import ArgumentParser
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import test_lean_db as fixtures
from test_evaluation import cases_and_responses

from research_multiplier.cli import eval_cli
from research_multiplier import eval_component, evaluation
from research_multiplier.protocol.serialization import fingerprint


def protocol_for(baseline, candidate, kind="challenger"):
    changed = {"proof-context": "v2"} if kind == "challenger" else {}
    return {
        "format_version": 1, "kind": kind,
        "controls": {"model_revision": "fixed", "settings_sha256": "a" * 64},
        "baseline": {"response_sha256": fingerprint(baseline),
                     "components": {"proof-context": "v1", "review": "v1"}},
        "candidate": {"response_sha256": fingerprint(candidate), "components": {**changed, "review": "v1"}},
    }


class ComponentComparisonTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name)
        context = patch.dict(os.environ, {"RM_DATA_ROOT": scratch.name})
        context.start()
        self.addCleanup(context.stop)
        self.development, self.holdout, self.baseline, self.candidate = cases_and_responses()
        self.protocol = protocol_for(self.baseline, self.candidate)

    def compare(self, protocol=None, *, split="holdout"):
        return eval_component.compare(self.development, self.holdout, self.baseline, self.candidate,
                                      split, self.protocol if protocol is None else protocol)

    def cli(self, *args):
        parser = ArgumentParser()
        eval_cli.add_commands(parser.add_subparsers(required=True))
        return eval_cli._result(parser.parse_args(["eval", *map(str, args)]))

    def test_approved_scope_comes_from_existing_gate_and_replays(self):
        body, artifact = self.compare()
        result = eval_component.summary(body, artifact)
        self.assertTrue(result["recommended"])
        change = result["component_comparison"]
        self.assertEqual(change["component"], "proof-context")
        self.assertEqual(change["paired_outcomes"]["known_theorem"]["candidate_only"], 3)
        self.assertEqual(change["paired_outcomes"]["false_theorem"]["candidate_only"], 3)
        self.assertEqual(change["paired_outcomes"]["scope_drift"]["both_matched"], 2)
        self.assertEqual(change["paired_outcomes"]["invalid_proof"]["both_matched"], 2)
        self.assertEqual(eval_component.reproduce(artifact["sha256"]), (body, artifact))
        self.assertEqual(self.cli("reproduce", artifact["sha256"]), {"verified": artifact})
        self.assertEqual(eval_component.reproduce(change["case_evaluation"]["sha256"]),
                         evaluation.reproduce(change["case_evaluation"]["sha256"]))

        outputs = []
        for name, value in (("development", self.development), ("holdout", self.holdout), ("base", self.baseline),
                            ("candidate", self.candidate), ("change", self.protocol)):
            path = self.path / f"{name}.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            outputs.append(path)
        self.assertEqual(self.cli("compare", *outputs[:4], "--component", outputs[-1]), result)

    def test_exactly_one_change_and_exact_archives(self):
        protocol = protocol_for(self.baseline, self.candidate, "ablation")
        body, _ = self.compare(protocol)
        self.assertIsNone(body["component_comparison"]["candidate_version"])
        for change, diagnostic in (
            ({"kind": "challenger"}, "ablation removes"),
            ({"candidate": {**protocol["candidate"], "response_sha256": "b" * 64}}, "exact candidate"),
            ({"candidate": {**protocol["candidate"], "components": {"review": "v2"}}}, "exactly one"),
            ({"candidate": {**protocol["candidate"], "components": protocol["baseline"]["components"]}}, "exactly one"),
        ):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, diagnostic):
                self.compare({**protocol, **change})

        different_model = deepcopy(self.candidate)
        different_model["version"]["model"] = "another-model"
        with self.assertRaisesRegex(ValueError, "same model ID"):
            eval_component.compare(self.development, self.holdout, self.baseline, different_model,
                                   "holdout", protocol_for(self.baseline, different_model))

    def test_candidate_cannot_trade_scope_or_injection_regression_for_more_wins(self):
        for number in (3, 4):
            with self.subTest(number=number):
                degraded = deepcopy(self.candidate)
                case = f"case-{number}"
                if number == 3:
                    # Increase scope drift while continuing to solve the true cases.
                    degraded["responses"][case] = {"action": "submit", "subject_sha256": "0" * 64, "certificate": {}}
                    degraded["responses"]["case-7"] = degraded["responses"][case]
                    degraded["responses"]["case-4"] = degraded["responses"][case]
                else:
                    degraded["responses"][case] = {"action": "abstain"}
                body, _ = eval_component.compare(self.development, self.holdout, self.baseline, degraded,
                                                  "holdout", protocol_for(self.baseline, degraded))
                self.assertGreater(body["candidate"]["counts"]["matched"], body["baseline"]["counts"]["matched"])
                self.assertFalse(body["recommended"])

    @unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
    def test_component_manifest_is_bound_to_human_recommendation(self):
        fixtures.LeanDbTests.setUpClass()
        db = fixtures.LeanDbTests()
        body, artifact = self.compare()
        summary = eval_component.summary(body, artifact)
        with db.as_role("rm_verifier") as verifier:
            report_id = verifier.execute("SELECT rm.register_eval_report(%s)", (db.json(summary),)).fetchone()[0]
        with db.as_role("rm_human") as human:
            row = human.execute("SELECT summary FROM rm.evaluation_report_status WHERE id = %s",
                                (report_id,)).fetchone()
            self.assertEqual(row[0], summary)
            restored, restored_artifact = eval_component.reproduce(artifact["sha256"])
            self.assertEqual(eval_component.summary(restored, restored_artifact), row[0])
            with self.assertRaises(db.errors.CheckViolation), human.transaction():
                human.execute("SELECT rm.approve_eval_report(%s,%s,'researcher','paired trial')",
                              (report_id, summary["component_comparison"]["case_evaluation"]["sha256"]))
            human.execute("SELECT rm.approve_eval_report(%s,%s,'researcher','paired trial')",
                          (report_id, artifact["sha256"]))
            self.assertEqual(human.execute("SELECT actor_ref FROM rm.evaluation_report_status WHERE id = %s",
                                           (report_id,)).fetchone()[0], "researcher")
