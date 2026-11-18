"""Check deterministic holdout replay and approval fences."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

import test_lean_db as database_fixtures

from research_multiplier import eval_runner, evaluation
from research_multiplier.domain_packages import production_domains
from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


DSN = os.environ.get("RM_TEST_DSN")


def cases_and_responses():
    groups = ["known_theorem", "false_theorem", "scope_drift", "invalid_proof"]

    def case(number, group):
        target = number + 1 if group == "false_theorem" else number
        spec = {"vertices": ["s", "t"], "source": "s", "sink": "t",
                "edges": [["s", "t", number]], "target_value": target}
        return {"id": f"case-{number}", "domain": "fixed-directed-max-flow", "input": spec, "group": group}

    development = {"format_version": 1, "cases": [case(11, "known_theorem")]}
    holdout = {"format_version": 1, "cases": [case(number, groups[(number - 1) % len(groups)]) for number in range(1, 11)]}
    candidate = {"format_version": 1, "version": {"harness": "example", "model": "fixed", "revision": "v2"}, "responses": {}}
    baseline = {"format_version": 1, "version": {"harness": "example", "model": "fixed", "revision": "v1"}, "responses": {}}
    subjects = evaluation.keys(holdout)
    for case in holdout["cases"]:
        if case["group"] in {"known_theorem", "false_theorem"}:
            certificate = production_domains.get(case["domain"]).candidate(case["input"])["certificate"]
            candidate["responses"][case["id"]] = {"action": "submit", "subject_sha256": subjects[case["id"]],
                                                  "certificate": certificate}
        else:
            certificate = {}
            candidate["responses"][case["id"]] = {"action": "reject"}
            digest = "0"*64 if case["group"] == "scope_drift" else subjects[case["id"]]
            baseline["responses"][case["id"]] = {"action": "submit", "subject_sha256": digest,
                                                 "certificate": certificate}
    return development, holdout, baseline, candidate


class EvaluationTests(unittest.TestCase):
    def test_collect_passes_only_the_sealed_contract_and_preserves_usage(self):
        development, holdout, _, _ = cases_and_responses()
        holdout = {**holdout, "cases": holdout["cases"][:4]}
        requests = []

        class FixedAdapter:
            capabilities = ModelCapabilities(output_token_limit_enforced=True, token_usage_available=True)

            def complete(self, request):
                prompt = request["messages"][1]["content"]
                contract = json.loads(prompt)
                requests.append(contract)
                self_case.assertEqual(set(contract), {"frozen_claim", "subject_sha256"})
                self_case.assertEqual(request["budget"]["max_model_calls"], 1)
                self_case.assertNotIn("case-11", prompt)
                self_case.assertNotIn("invalid_proof", prompt)
                self_case.assertNotIn("known_theorem", prompt)
                frozen = contract["frozen_claim"]
                spec = frozen["small_case_spec"]
                value = spec["edges"][0][2]
                certificate = production_domains.get(frozen["domain"]).candidate(spec)["certificate"]
                response = {"action": "submit", "certificate": certificate,
                            "subject_sha256": contract["subject_sha256"] if value != 3 else "0" * 64}
                if value == 4:
                    response = {"action": "abstain", "unexpected": True}
                return AdapterReply(json.dumps(response), "fixed-eval-1", model_revision="fixture-2026",
                                    input_tokens=213, output_tokens=41)

        self_case = self
        fixture = ModuleType("rm_eval_fixture")
        fixture.build = lambda _config: FixedAdapter()
        configuration = {
            "RESEARCH_MULTIPLIER_PROVER_PROVIDER": "fixed-eval",
            "RESEARCH_MULTIPLIER_PROVER_MODEL": "fixed-eval-1",
            "RESEARCH_MULTIPLIER_PROVER_FAMILY": "offline",
            "RESEARCH_MULTIPLIER_PROVER_ADAPTER": "rm_eval_fixture:build",
        }
        with (TemporaryDirectory() as location,
              patch.dict(sys.modules, {fixture.__name__: fixture}),
              patch.dict(os.environ, {"RM_DATA_ROOT": location, **configuration}, clear=True)):
            with self.assertRaisesRegex(ValueError, "Confirm the selected contracts are public"):
                eval_runner.collect(development, holdout, "holdout", "model")
            with self.assertRaisesRegex(ValueError, "declared case limit"):
                eval_runner.collect(development, holdout, "holdout", "model", confirm_public=True, max_cases=3)
            self.assertEqual(requests, [])
            collected = eval_runner.collect(development, holdout, "holdout", "model", confirm_public=True, max_cases=4)
            self.assertEqual(len(requests), 4)
            baseline = eval_runner.collect(development, holdout, "holdout", "program")
            self.assertEqual(len(requests), 4)
            self.assertTrue(Path(collected["responses_path"]).is_relative_to(Path(location).resolve()))
            evaluation_artifacts = evaluation.archive()
            manifest = evaluation_artifacts.read_json(collected["provenance"])
            self.assertEqual(manifest["inputs"]["holdout"], evaluation_artifacts.put(holdout))
            attempt = manifest["attempts"]["case-1"]
            self.assertEqual(attempt["model"]["revision"], "fixture-2026")
            self.assertEqual((attempt["model"]["input_tokens"], attempt["model"]["output_tokens"]), (213, 41))
            self.assertGreaterEqual(attempt["elapsed_ms"], 0)
            self.assertEqual(
                evaluation_artifacts.read_json(attempt["transcript"])["request"]["messages"][1]["role"], "user",
            )
            self.assertEqual(manifest["attempts"]["case-4"]["failure"]["code"], "response_contract")
            model_responses = json.loads(Path(collected["responses_path"]).read_bytes())
            program_responses = json.loads(Path(baseline["responses_path"]).read_bytes())
            self.assertEqual(evaluation_artifacts.read_json(collected["responses"]), model_responses)
            body, artifact = evaluation.compare(development, holdout, program_responses, model_responses, "holdout")
            self.assertEqual(body["candidate"]["cases"]["case-1"]["observed"], "verified")
            self.assertEqual(body["candidate"]["cases"]["case-2"]["observed"], "refuted")
            self.assertTrue(body["candidate"]["cases"]["case-3"]["scope_drift"])
            self.assertEqual(body["candidate"]["cases"]["case-4"]["observed"], "abstained")
            self.assertEqual(evaluation.reproduce(artifact["sha256"]), (body, artifact))

    def test_reports_reproduce_and_reject_copied_holdouts(self):
        development, holdout, baseline, candidate = cases_and_responses()
        with TemporaryDirectory() as location, patch.dict(os.environ, {"RM_DATA_ROOT": location}):
            body, artifact = evaluation.compare(development, holdout, baseline, candidate, "holdout")
            self.assertEqual(body["candidate"]["counts"]["matched"], 10)
            self.assertEqual(body["baseline"]["counts"]["scope_drift"], 2)
            self.assertTrue(body["recommended"])
            self.assertTrue((Path(location) / "evaluation" / "private" / "objects" / artifact["sha256"]).is_file())
            self.assertEqual(evaluation.reproduce(artifact["sha256"]), (body, artifact))
            self.assertNotIn("cases", evaluation.summary(body, artifact)["candidate"])
            copied = {"format_version": 1, "cases": [{**development["cases"][0], "id": "renamed"}]}
            with self.assertRaisesRegex(ValueError, "distinct IDs and exact inputs"):
                evaluation.compare(development, copied, baseline, candidate, "holdout")

    @unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
    def test_verifier_registration_and_human_recommendation_are_separate(self):
        from psycopg import errors
        from psycopg.types.json import Jsonb

        database_fixtures.LeanDbTests.setUpClass()
        fixture = database_fixtures.LeanDbTests()
        with TemporaryDirectory() as location, patch.dict(os.environ, {"RM_DATA_ROOT": location}):
            body, artifact = evaluation.compare(*cases_and_responses(), "holdout")
            summary = Jsonb(evaluation.summary(body, artifact))
            with fixture.as_role("rm_agent") as agent:
                with self.assertRaises(errors.InsufficientPrivilege), agent.transaction():
                    agent.execute("SELECT rm.register_eval_report(%s)", (summary,))
            with fixture.as_role("rm_verifier") as verifier:
                report_id = verifier.execute("SELECT rm.register_eval_report(%s)", (summary,)).fetchone()[0]
                with self.assertRaises(errors.InsufficientPrivilege), verifier.transaction():
                    verifier.execute("SELECT rm.approve_eval_report(%s,%s,'operator','reviewed')",
                                     (report_id, artifact["sha256"]))
            with fixture.as_role("rm_human") as human:
                with self.assertRaises(errors.CheckViolation), human.transaction():
                    human.execute("SELECT rm.approve_eval_report(%s,%s,'operator','reviewed')", (report_id, "0"*64))
                approved = human.execute("SELECT rm.approve_eval_report(%s,%s,'operator','reviewed')",
                                         (report_id, artifact["sha256"])).fetchone()[0]
                self.assertEqual(approved, report_id)


class InjectedEvaluationTests(unittest.TestCase):
    def test_synthetic_faults_reach_the_intended_check(self):
        from research_multiplier import eval_injections

        with TemporaryDirectory() as location, patch.dict(os.environ, {"RM_DATA_ROOT": location}):
            for capacity in (3, 7):
                flow = {"vertices": ["s", "t"], "source": "s", "sink": "t",
                        "edges": [["s", "t", capacity]], "target_value": capacity}
                coloring = {"vertices": ["u", "v"], "edges": [["u", "v"]], "colors": 2}
                cases = {"format_version": 1, "cases": [
                    {"id": f"{domain}-{group}", "domain": domain, "group": group, "input": instance}
                    for (domain, instance) in (("fixed-directed-max-flow", flow), ("graph-coloring", coloring))
                    for group in ("scope_drift", "invalid_proof")
                ]}
                report = eval_injections.run(cases)
                self.assertEqual(report["detected"], 4)
                self.assertEqual(report["count"], 4)
                self.assertEqual({item["detail"] for item in report["results"].values()},
                                 {"subject_sha256_mismatch", "certificate_check_failed"})
                self.assertEqual(report, eval_injections.run(cases))


    def test_scope_mutation_works_at_both_color_limits(self):
        from research_multiplier import eval_injections

        cases = {"format_version": 1, "cases": [
            {"id": "one", "domain": "graph-coloring", "group": "scope_drift",
             "input": {"vertices": ["a", "b"], "edges": [], "colors": 1}},
            {"id": "eight", "domain": "graph-coloring", "group": "scope_drift",
             "input": {"vertices": ["a", "b"], "edges": [["a", "b"]], "colors": 8}},
        ]}
        with TemporaryDirectory() as location, patch.dict(os.environ, {"RM_DATA_ROOT": location}):
            self.assertEqual(eval_injections.run(cases)["detected"], 2)
