import os
import sys
import unittest
from unittest.mock import patch

import test_final_local_lemmas as final_fixtures
import test_lean_flow as lean_fixtures

from research_multiplier.workflows import lean_search


QUANTIFIED_OBLIGATION = "∀ n m : Nat, True → n + m = m + n"


ADAPTER = """\
import json

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


class Adapter:
    capabilities = ModelCapabilities(output_token_limit_enforced=True)

    def complete(self, request):
        task = json.loads(request["messages"][1]["content"])
        assert task["antecedents"] == ["True"]
        if request["model_id"] == "program-first-fixture":
            assert task["round"] == 1
            result = {"action": "reduction", "obligations": ["∀ n m : Nat, True → n + m = m + n"], "proof": {
                "intro": {"count": 4, "body": {"apply": [{"use": 1}, {"use": 3}, {"use": 2}, {"use": 0}]}}
            }}
            return AdapterReply(text=json.dumps(result), model_id=request["model_id"])
        if task["round"] == 1:
            assert task["search_focus"] == {"method": "advance_goal"}
            result = {"action": "blocked", "reason": "No direct proof for this syntactic form."}
        elif task["round"] == 2:
            assert task["search_focus"] == {"method": "alternative_route"}
            first = task["history"][0]
            assert first["status"] == "blocked" and first["action"] == "blocked" and "reason" not in first
            result = {"action": "reduction", "obligations": ["∀ n : Nat, True"], "proof": {
                "intro": {"count": 4, "body": {"apply": [{"use": 1}, {"use": 2}]}}
            }}
        else:
            assert task["round"] == 3
            assert task["search_focus"]["method"] == "key_lemma"
            assert task["search_focus"]["proposition"] == "∀ n : Nat, True"
            result = {"action": "lemma", "proposition": "∀ n : Nat, True",
                      "proof": {"intro": {"count": 2, "body": {"use": 0}}}}
        return AdapterReply(text=json.dumps(result), model_id=request["model_id"])


def factory(_):
    return Adapter()
"""


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanSearchContinuationTests(unittest.TestCase):
    def adapter_environment(self, fixture, model):
        (fixture.output / "lean_checkpoint_test_adapter.py").write_text(ADAPTER, encoding="utf-8")
        environment = {**fixture.env,
                       "RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER": "checkpoint-fixture",
                       "RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL": model,
                       "RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY": "checkpoint-fixture",
                       "RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER": "lean_checkpoint_test_adapter:factory"}
        environment.pop("RM_HUMAN_DSN")
        return environment

    def test_program_checks_new_quantified_model_obligation_before_another_call(self):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        anchor, scope, _ = case.anchor(
            "Replay an explicit quantified theorem", "∀ n m : Nat, m + n = n + m",
            [
                {"kind": "hypothesis", "natural": "Fixed truth used in the route implication.",
                 "formal": {"lean": {"proposition": "True"}}},
                {"kind": "theorem", "natural": "Addition on Nat is commutative.", "formal": {"lean": {
                    "proposition": "∀ a b : Nat, a + b = b + a", "declaration": "Nat.add_comm",
                }}},
            ], sensitivity="public",
        )
        fixture = case.fixture
        basis = case.cli("research", "basis", anchor["packet_id"])
        version, = [row["id"] for row in basis if row["body"]["kind"] == "theorem"]
        environment = self.adapter_environment(fixture, "program-first-fixture")
        with patch.dict(os.environ, environment), patch.object(sys, "path", [str(fixture.output), *sys.path]):
            result = lean_search.search(anchor["id"], rounds=2, confirm_public=True, max_new_rounds=2,
                                        output=fixture.output)
        self.assertEqual(result["status"], "awaiting_evidence_review")
        self.assertEqual([step["status"] for step in result["rounds"]], ["route_checked"])
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 1)
        node, = [step for step in result["program_obligations"] if step.get("proposition") == QUANTIFIED_OBLIGATION]
        self.assertEqual(node["status"], "lemma_verified")
        packet = case.cli("review", "packet", result["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
        self.assertIn(version, packet["key_steps"]["expanded_basis_version_ids"])
        self.assertIn(node["lemma_id"], packet["key_steps"]["local_lemma_ids"])

    def test_passes_recover_feedback_without_duplicate_dispatch_and_close_verified_route(self):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        statement, scope, _ = case.anchor(
            "Resume one checked search pass", "∀ n : Nat, n = n → True",
            [{"kind": "hypothesis", "natural": "The frozen starting premise in this checkpoint test.",
              "formal": {"lean": {"proposition": "True"}}}], sensitivity="public",
        )
        fixture = case.fixture
        environment = self.adapter_environment(fixture, "checkpoint-test")
        with patch.dict(os.environ, environment), patch.object(sys, "path", [str(fixture.output), *sys.path]):
            arguments = {"rounds": 3, "confirm_public": True, "max_new_rounds": 1, "output": fixture.output}
            first = lean_search.search(statement["id"], **arguments)
            replay = lean_search.search(statement["id"], **arguments)
            second = lean_search.search(statement["id"], continuation=first["record"], **arguments)
            third = lean_search.search(statement["id"], continuation=second["record"], **arguments)

        self.assertEqual(first, replay)
        self.assertEqual([first["next_round"], second["next_round"]], [2, 3])
        self.assertEqual([first["status"], second["status"], third["status"]],
                         ["search_continues", "search_continues", "awaiting_evidence_review"])
        self.assertEqual([step["status"] for step in third["rounds"]], ["blocked", "route_checked", "lemma_verified"])
        self.assertEqual(third["rounds"][:2], second["rounds"])
        self.assertEqual(case.cli("lean", "budget", statement["id"])["reserved_calls"], 3)
        report = case.cli("review", "packet", third["evidence_id"])
        self.assertEqual(report["claim_id"], scope["claim_id"])
        self.assertEqual(report["verification"]["review_status"], "pending")
        self.assertCountEqual(report["key_steps"]["local_lemma_ids"],
                              [third["rounds"][1]["reduction_id"], third["rounds"][2]["lemma_id"]])

    def test_refuted_program_routes_never_refute_the_original_disjunction(self):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        anchor, scope, _ = case.anchor(
            "Unseeded routes for parity", "∀ n : Nat, n % 2 = 0 ∨ n % 2 = 1", [], sensitivity="public",
        )
        with patch.dict(os.environ, case.fixture.env):
            result = lean_search.search(anchor["id"], rounds=0, max_new_rounds=1, output=case.fixture.output)
        self.assertEqual(result["status"], "proof_search_blocked")
        self.assertEqual(result["rounds"], [])
        self.assertEqual(result["open_routes"], [])
        self.assertEqual([item["status"] for item in result["route_attempts"]], ["route_refuted", "route_refuted"])
        self.assertEqual([item["witness"]["assignments"][0]["value"] for item in result["route_attempts"]], [1, 0])
        self.assertEqual([item["status"] for item in case.cli("lean", "reductions", anchor["id"])["reductions"]],
                         ["refuted_obligation", "refuted_obligation"])
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 0)
        self.assertFalse(case.cli("research", "evidence", scope["claim_id"])["evidence"])

    def test_full_boolean_enumeration_checks_original_goal_after_two_refuted_routes(self):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        left = "(a = true ∨ b = true) ∧ (c = true ∨ d = true) ∧ (e = true ∨ f = true)"
        right = "(a = false ∧ b = false) ∨ (c = false ∧ d = false) ∨ (e = false ∧ f = false)"
        goal = f"∀ a b c d e f : Bool, ({left}) ∨ ({right})"
        anchor, scope, _ = case.anchor("An exact six-variable Boolean proof", goal, [], sensitivity="public")
        with patch.dict(os.environ, case.fixture.env):
            result = lean_search.search(anchor["id"], rounds=0, max_new_rounds=1, output=case.fixture.output)
        self.assertEqual(result["status"], "awaiting_evidence_review")
        self.assertEqual(result["target"], "proof")
        self.assertEqual(result["method"], "finite_boolean_decision")
        self.assertEqual(result["assignments_checked"], 64)
        self.assertEqual([item["status"] for item in result["route_attempts"]], ["route_refuted", "route_refuted"])
        packet = case.cli("review", "packet", result["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
        self.assertEqual(packet["key_steps"]["statement_type"], goal)
        self.assertEqual(packet["verification"]["review_status"], "pending")
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 0)
