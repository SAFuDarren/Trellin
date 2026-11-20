"""Drive restricted intake and a private Lean proposal through the normal installed CLI."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.model_adapters import ModelConfig
from research_multiplier.storage.postgres import Store
from research_multiplier.workflows import runtime


ADAPTER = '''\
import json
from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


class LocalModel:
    capabilities = ModelCapabilities(private_local=True, output_token_limit_enforced=True)

    def complete(self, request):
        candidate = json.loads(request["messages"][1]["content"])
        prompt = request["messages"][0]["content"]
        if "selected mathematical target" in prompt:
            assert candidate["sensitivity"] == "restricted"
            response = {"formal": {"lean": {"proposition": "∀ n : Nat, n = n"}}}
        elif "natural-language conjectures" in prompt:
            assert candidate["intuition"] == "Try a statement that is uniform in a natural number."
            response = {"proposals": [{"statement": "Every natural number equals itself.",
                                       "reason": "The property is uniform.", "basis_version_ids": []}]}
        elif "Extract possible" in prompt:
            assert candidate["frozen_target"]["formal"]["lean"]["proposition"] == "∀ n : Nat, n = n"
            response = {"items": [{"natural": "Truth is available.", "kind": "hypothesis",
                                   "formal": {"lean": {"proposition": "True"}}}]}
        else:
            assert "public research statements" not in prompt
            response = {"action": "proof", "proof": {"intro": {"count": 2, "body": {"rfl": True}}}}
        return AdapterReply(json.dumps(response), request["model_id"])


def factory(config):
    assert config.private
    return LocalModel()
'''


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class PrivateLeanFlowTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli
        directory = self.case.fixture.workspace.parent
        (directory / "private_model_fixture.py").write_text(ADAPTER, encoding="utf-8")
        fixture = "private_model_fixture:factory"
        supplied = self.case.fixture.env
        supplied["PYTHONPATH"] = os.pathsep.join(filter(None, [str(directory), supplied.get("PYTHONPATH")]))
        supplied["RESEARCH_MULTIPLIER_TRUSTED_LOCAL_ADAPTERS"] = fixture
        for role in ("EXPLORE", "LEAN_AUTHOR"):
            prefix = f"RESEARCH_MULTIPLIER_PRIVATE_{role}_"
            supplied[prefix + "PROVIDER"] = "private-fixture"
            supplied[prefix + "MODEL"] = "fixture"
            supplied[prefix + "FAMILY"] = "fixture"
            supplied[prefix + "ADAPTER"] = fixture

    def test_private_target_basis_and_lean_proposal(self):
        case, cli = self.case, self.cli
        supplied = case.fixture.env
        input_path = case.document("private-local-question", {
            "title": "Private-local worker fixture", "domain": "lean-core",
            "statement": "Every natural number equals itself.", "sensitivity": "restricted",
        })
        self.assertIn("public material", cli("intake", "propose-target", input_path, "--confirm-public", expect=2))
        proposal = cli("intake", "propose-target", input_path, "--confirm-local")
        digest = proposal["record"]["sha256"]
        preview = cli("intake", "preview-target", "--from-proposal", digest)
        target = cli("intake", "freeze-target", "--from-proposal", digest,
                     "--hash", preview["target_sha256"], "--actor", "researcher", "--reason", "Checked the target.")
        notes = case.document("private-local-notes", {"notes": "Truth is available."})
        self.assertIn("public material", cli("intake", "draft", notes, "--target", target["id"], "--model", expect=2))
        draft = cli("intake", "draft", notes, "--target", target["id"], "--confirm-local")
        self.assertEqual(draft["body"]["model"]["call"]["parameters"]["private_model"]["transport"], "installed-local")
        item, = draft["comparison"]
        self.assertEqual(item["status"], "confirm_scope")
        decisions = case.document("private-local-basis-review", {item["id"]: {"action": "accept", "reason": "Checked."}})
        cli("intake", "confirm", draft["id"], "--hash", draft["intake_sha256"], "--decisions", decisions,
            "--actor", "researcher", "--reason", "Checked the starting scope.")
        bound = cli("intake", "target", target["id"])
        statement = cli("lean", "draft-theory", bound["packet_id"])
        cli("lean", "confirm", statement["id"], "--version", statement["version"],
            "--hash", statement["statement_sha256"], "--actor", "researcher", "--reason", "Checked the exact Lean scope.")

        with patch.dict(os.environ, supplied), Store() as store:
            config = ModelConfig.from_env("LEAN_AUTHOR", private=True)
            identity = config.private_provenance()
            with self.assertRaisesRegex(ValueError, "permission denied"):
                store.query("scheduler", "SELECT rm.authorize_private_lean_model(%s,%s)",
                            statement["id"], store.json(identity))
            payload = {
                "lean_author": True, "lean_search": True, "formalization_id": str(bound["packet_id"]),
                "formalization_sha256": store.query("human", """SELECT packet_sha256 FROM rm.formalization_status
                    WHERE id = %s""", bound["packet_id"])[0]["packet_sha256"],
                "author_statement_id": statement["id"], "author_statement_sha256": statement["statement_sha256"],
                "private_model": identity,
            }
            subject = {"claim_id": bound["claim_id"], "contract_sha256": statement["contract_sha256"]}
            with self.assertRaisesRegex(ValueError, "explicitly authorize"):
                runtime.schedule(store, subject, "prove", payload, 90, "unpermitted-private-local-fixture")

        result = cli("lean", "search", statement["id"], "--confirm-local", "--rounds", 1)
        self.assertEqual(result["status"], "awaiting_evidence_review", result)
        self.assertEqual(len(result["rounds"]), 1)
        state = cli("research", "resume", bound["claim_id"])
        self.assertIsNone(state["conclusion"])
        self.assertEqual(state["next"]["action"], "review_evidence")
        with patch.dict(os.environ, supplied), Store() as store:
            row, = store.query("verifier", "SELECT model_provider, model_parameters FROM rm.attempts WHERE id = %s",
                               result["rounds"][0]["model_attempt_id"])
            artifacts = Artifacts(case.fixture.workspace / "objects")
            contract = artifacts.put(store.frozen(subject["claim_id"])["contract_bytes"], raw=True)
            with self.assertRaisesRegex(ValueError, "attempt does not match the authorized local route"):
                runtime.execute(store, artifacts, subject, contract, "prove",
                                lambda: ("numeric_observation", {"action": "blocked", "reason": "test"}, None),
                                payload=payload, seconds=90, operation_key="missing-private-provenance")
        self.assertEqual(row["model_provider"], identity["provider"])
        self.assertEqual(row["model_parameters"]["private_model"], identity)
        compact = cli("research", "resume", bound["claim_id"], "--format", "text")
        self.assertIn(f"research-multiplier review explain {result['evidence_id']}", compact)
        explanation = cli("review", "explain", result["evidence_id"])
        self.assertIn("Checked Lean proposition:", explanation)
        self.assertIn("Truth is available.", explanation)
        self.assertIn("not observed in proof", explanation)

    def test_invalid_search_does_not_authorize_private_model(self):
        import psycopg

        statement, _scope, _runtime = self.case.anchor(
            "Invalid private search budget", "∀ n : Nat, n = n", []
        )
        with psycopg.connect(database.DSN, autocommit=True) as connection:
            def approvals():
                return connection.execute(
                    "SELECT count(*) FROM rm.private_lean_model_approvals WHERE statement_id = %s",
                    (statement["id"],),
                ).fetchone()[0]

            self.assertEqual(approvals(), 0)
            rejected = self.cli("lean", "search", statement["id"], "--confirm-local", "--rounds", 9, expect=2)
            self.assertIn("between 0 and 8 rounds", rejected)
            self.assertEqual(approvals(), 0)

    def test_private_discussion_resumes_without_calling_a_model(self):
        case, cli = self.case, self.cli
        goal = case.document("private-model-goal", {
            "title": "Restricted discussion restoration", "domain": "lean-core",
            "objective": "Find a property of natural numbers.", "basis_version_ids": [], "sensitivity": "restricted",
        })
        frame = cli("goal", "draft", goal)
        cli("goal", "confirm", frame["id"], "--hash", frame["frame_sha256"],
            "--actor", "researcher", "--reason", "Review the exact frame.")
        note = case.document("private-model-intuition", {"intuition": "Try a statement that is uniform in a natural number."})
        self.assertIn("public material", cli("goal", "discuss", frame["id"], note, "--confirm-public", expect=2))
        discussed = cli("goal", "discuss", frame["id"], note, "--confirm-local")
        model_key = "RESEARCH_MULTIPLIER_PRIVATE_EXPLORE_PROVIDER"
        provider = case.fixture.env.pop(model_key)
        try:
            resumed = cli("research", "resume", "--format", "text")
            self.assertIn(f"research-multiplier goal discussion {frame['id']}", resumed)
            latest = cli("goal", "discussion", frame["id"])
            self.assertEqual(latest, discussed)
        finally:
            case.fixture.env[model_key] = provider
        review = cli("goal", "formalize", frame["id"], latest["record"]["sha256"], "1", "--confirm-local")
        candidate = review["conjecture"]
        self.assertEqual(candidate["body"]["provenance"]["discussion"], latest["record"])
        self.assertEqual(candidate["body"]["provenance"]["model"]["parameters"]["private_model"]["transport"],
                         "installed-local")
        resume = cli("research", "resume")
        waiting = next(item for item in resume["frames_waiting_for_conjecture"]["items"]
                       if item["reference"]["goal_id"] == frame["id"])
        self.assertEqual(waiting["next"]["action"], "compare_formalized_conjectures")
        cli("goal", "select", candidate["id"], "--hash", candidate["conjecture_sha256"],
            "--actor", "researcher", "--reason", "Reviewed exact formalization.")
        self.assertIn("no discussion", cli("goal", "discussion", frame["id"], expect=2))

    def test_signed_worker_requires_the_exact_private_route(self):
        case, cli = self.case, self.cli
        statement, _, _ = case.anchor(
            "Private worker policy fixture", "∀ a : Nat, a = a",
            [{"kind": "hypothesis", "natural": "The worker fixture has an approved truth premise.",
              "formal": {"lean": {"proposition": "True"}}}],
            statement_text="The signed local worker demonstrates reflexivity.",
        )
        policy = cli("advance", "propose", "--for-packet", statement["packet_id"],
                     "--lean-model-rounds", "1", "--confirm-local")
        identity = policy["policy"]["lean"]["private_model"]
        self.assertEqual(identity["transport"], "installed-local")

        env = case.fixture.env
        human = env.pop("RM_HUMAN_DSN")
        try:
            self.assertEqual(cli("advance", "worker", "--policy", policy["id"])["lean_runs"], [])
        finally:
            env["RM_HUMAN_DSN"] = human
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Approve one bounded local proposal.")
        model_key = "RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_MODEL"
        model = env[model_key]
        del env["RM_HUMAN_DSN"]
        try:
            env[model_key] = "changed-model"
            blocked = cli("advance", "worker", "--policy", policy["id"])
            self.assertEqual(blocked["lean_runs"], [])
            self.assertIn("exact private model configuration signed", blocked["errors"][0]["reason"])
            env[model_key] = model
            report = cli("advance", "worker", "--policy", policy["id"])
        finally:
            env[model_key] = model
            env["RM_HUMAN_DSN"] = human
        self.assertEqual(report["errors"], [])
        run, = report["lean_runs"]
        self.assertEqual(run["status"], "awaiting_evidence_review", report)
