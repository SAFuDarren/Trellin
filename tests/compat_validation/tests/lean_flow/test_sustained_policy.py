"""Keep the signed round allowance across independent worker processes."""

import os
import unittest

import test_final_local_lemmas as fixtures
import test_lean_flow as database


ADAPTER = """\
import json

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


class Adapter:
    capabilities = ModelCapabilities()

    def complete(self, request):
        prompt = json.loads(request["messages"][-1]["content"])
        round_number = prompt["round"]
        assert round_number in (1, 2), "A local lemma must not refill the signed allowance."
        assert prompt["antecedents"] == ["True"]
        if round_number == 2:
            assert [step["status"] for step in prompt["history"]] == ["lemma_verified"]
        number = 71 + round_number
        result = {"action": "lemma", "proposition": f"({number} : Nat) = {number}",
                  "proof": {"intro": {"count": 1, "body": {"rfl": True}}}}
        return AdapterReply(text=json.dumps(result), model_id=request["model_id"])


def factory(config):
    return Adapter()
"""


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class SustainedPolicyTests(unittest.TestCase):
    def test_signed_allowance_survives_worker_restarts_and_new_local_lemmas(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        cli = case.cli
        target, scope, _ = case.anchor(
            "durable signed search allowance", "∀ f : Nat → Nat, f 11 = f 11",
            [{"kind": "hypothesis", "natural": "Truth is the entire permitted test basis.",
              "formal": {"lean": {"proposition": "True"}}}],
            sensitivity="public", statement_text="The value of a natural-number function at eleven equals itself.",
        )
        output = case.fixture.output
        (output / "sustained_policy_adapter.py").write_text(ADAPTER, encoding="utf-8")
        env = case.fixture.env
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(output), env.get("PYTHONPATH"))))
        env.update(RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="fixture-capped-inquiry",
                   RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL="fixture-capped-author",
                   RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="fixture-test",
                   RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER="sustained_policy_adapter:factory")

        policy = cli("advance", "propose", "--for-packet", target["packet_id"], "--lean-model-rounds", 2)

        def work():
            role = env.pop("RM_HUMAN_DSN")
            try:
                result = cli("advance", "worker", "--policy", policy["id"])
            finally:
                env["RM_HUMAN_DSN"] = role
            self.assertEqual(result["errors"], [])
            return result["lean_runs"]

        self.assertEqual(work(), [])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Try up to two proposals at the frozen scope.")
        [first] = work()
        self.assertEqual(first["status"], "search_continues", first)
        self.assertEqual(first["next_round"], 2)
        resume = cli("research", "resume", scope["claim_id"])
        self.assertEqual(resume["decisions"]["items"], [])
        self.assertEqual(resume["next"]["action"], "run_progress_worker")
        self.assertEqual(resume["lean_advance"]["recent_rounds"][0]["status"], "lemma_verified")
        self.assertEqual(cli("lean", "budget", target["id"])["reserved_calls"], 1)

        [second] = work()
        self.assertEqual(second["status"], "budget_exhausted", second)
        self.assertEqual([step["status"] for step in second["rounds"]], ["lemma_verified", "lemma_verified"])
        self.assertEqual(work(), [])
        self.assertEqual(cli("lean", "budget", target["id"])["reserved_calls"], 2)
        passes = cli("advance", "status", policy["id"])["lean_passes"]
        self.assertEqual([row["result"]["status"] for row in passes], ["budget_exhausted", "search_continues"])
        self.assertEqual(passes[0]["result"]["inquiry"]["generation"], passes[1]["result"]["inquiry"]["generation"])
        self.assertEqual(cli("research", "resume", scope["claim_id"])["decisions"]["items"], [])
