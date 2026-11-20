"""Run independently drafted Lean routes through the signed worker and real databases."""

import json
import os
import sys
import unittest
from unittest.mock import patch

import test_final_local_lemmas as final_fixtures
import test_lean_flow as lean_fixtures
from research_multiplier.workflows import lean_search, research_progress


ADAPTER = '''\
import json
import os
from pathlib import Path
from threading import Barrier

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities

_BARRIER = Barrier(2)


class Adapter:
    capabilities = ModelCapabilities(output_token_limit_enforced=True)

    def complete(self, request):
        task = json.loads(request["messages"][1]["content"])
        focus = task["search_focus"]
        directory = Path(os.environ["RM_TEST_PARALLEL_OBSERVATIONS"])
        (directory / (str(task["round"]) + ".json")).write_text(json.dumps(task), encoding="utf-8")
        _BARRIER.wait(timeout=8)
        obligation = focus["proposition"]
        if request["model_id"] == "pause-both" or "* 1" in obligation:
            result = {"action": "blocked", "reason": "No proof in this lane."}
        else:
            result = {"action": "lemma", "proposition": obligation,
                      "proof": {"intro": {"count": 1, "body": {"rfl": True}}}}
        return AdapterReply(text=json.dumps(result), model_id=request["model_id"])


def factory(_):
    return Adapter()
'''


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ParallelLeanRouteTests(unittest.TestCase):
    def _prepare(self, model, rounds, stall_limit):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        terms = {"close-one": 2, "pause-both": 3, "restart-wave": 4}
        addition = "n" + " + 0" * terms[model]
        anchor, scope, _ = case.anchor(
            f"Confirm two distinct arithmetic routes for {model}",
            f"∀ n : Nat, (({addition} = n) ∨ (n * 1 = n))", [], sensitivity="public",
        )
        directory = case.fixture.output
        (directory / "parallel_test_adapter.py").write_text(ADAPTER, encoding="utf-8")
        env = case.fixture.env
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(directory), env.get("PYTHONPATH"))))
        prefix = "RESEARCH_MULTIPLIER_LEAN_AUTHOR_"
        env.update({prefix + key: value for key, value in {
            "PROVIDER": "parallel-test", "MODEL": model, "FAMILY": "parallel-test",
            "ADAPTER": "parallel_test_adapter:factory",
        }.items()})
        env["RM_TEST_PARALLEL_OBSERVATIONS"] = str(directory)
        cli = case.cli
        policy = cli("advance", "propose", "--for-packet", anchor["packet_id"],
                     "--lean-model-rounds", rounds, "--lean-route-parallelism", 2,
                     "--lean-route-stall-limit", stall_limit)
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"], "--actor", "researcher",
            "--reason", "Review two bounded routes under the original theorem.")
        return case, anchor, scope, policy["id"]

    def _work(self, case, policy_id):
        env = case.fixture.env
        human = env.pop("RM_HUMAN_DSN")
        try:
            result = case.cli("advance", "worker", "--policy", policy_id, "--parallel", 2, "--limit", 1)
        finally:
            env["RM_HUMAN_DSN"] = human
        self.assertFalse(result["errors"], result)
        [run] = result["lean_runs"]
        return run

    def test_overlapping_isolated_proposals_close_the_original_scope(self):
        case, anchor, scope, policy_id = self._prepare("close-one", 2, 2)
        run = self._work(case, policy_id)
        self.assertEqual(run["status"], "awaiting_evidence_review", run)
        self.assertEqual(run["parallel_batch"]["request_count"], 2)
        self.assertNotEqual(run["rounds"][0]["focus"]["implication_id"], run["rounds"][1]["focus"]["implication_id"])
        trace = json.loads((case.fixture.workspace / "objects" / run["record"]["sha256"]).read_bytes())
        [winning] = [step for step in trace["rounds"] if step["status"] == "lemma_verified"]
        self.assertEqual(winning["route_checkpoint"], {"verified": 1, "pending": 0, "stalled": 0}, trace)
        unadmitted = [step for step in trace["rounds"] if step["status"] == "proposal_unadmitted"]
        self.assertTrue(all("route_checkpoint" not in step for step in unadmitted), trace)
        for index in (1, 2):
            prompt = json.loads((case.fixture.output / f"{index}.json").read_bytes())
            self.assertEqual(prompt["history"], [])
            [route] = prompt["open_routes"]
            self.assertEqual(route["implication_id"], prompt["search_focus"]["implication_id"])
            self.assertEqual(prompt["antecedents"], [])
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 2)
        packet = case.cli("review", "packet", run["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
        self.assertEqual(packet["verification"]["review_status"], "pending")

    def test_paused_lanes_do_not_spend_unused_signed_model_slots(self):
        case, anchor, _, policy_id = self._prepare("pause-both", 4, 1)
        first = self._work(case, policy_id)
        self.assertEqual(first["status"], "search_continues", first)
        self.assertTrue(all(route["paused"] for route in first["route_lanes"]), first)
        stop = self._work(case, policy_id)
        self.assertEqual((stop["status"], stop["reason"]), ("proof_search_blocked", "route_stall_limit"))
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 2)

    def test_restart_reuses_both_archived_responses_before_admission(self):
        case, anchor, scope, policy_id = self._prepare("restart-wave", 2, 2)
        propose = lean_search._parallel_attempts

        def interrupted(*args, **kwargs):
            responses = propose(*args, **kwargs)
            self.assertEqual(len(responses), 2)
            self.assertTrue(all(attempt is not None for _, _, attempt in responses))
            raise RuntimeError("simulated interruption after proposals were recorded")

        env = {key: value for key, value in case.fixture.env.items() if key != "RM_HUMAN_DSN"}
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "path", [str(case.fixture.output), *sys.path]):
            with patch.object(lean_search, "_parallel_attempts", side_effect=interrupted):
                with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                    research_progress.advance_cycle("combined", 1, [policy_id], 2)
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 2)
        run = self._work(case, policy_id)
        self.assertEqual(run["status"], "awaiting_evidence_review", run)
        self.assertEqual(run["parallel_batch"]["request_count"], 2)
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 2)
        packet = case.cli("review", "packet", run["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
