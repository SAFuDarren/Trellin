"""Exercise derived public model context through native Lean and PostgreSQL."""

import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_final_local_lemmas as final
import test_lean_flow as lean
from test_lean_reductions import MODEL_EGRESS_PROBE

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(lean.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class PublicLeanContextTests(unittest.TestCase):
    def setUp(self):
        self.case = final.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_replayed_prerequisite_is_local_only_until_source_premise_is_released(self):
        cli, sample = self.case.cli, uuid4()
        premise = "(817 : Nat) = 817"
        choices = [{
            "kind": "hypothesis", "natural": f"Restricted source premise {sample}.",
            "formal": {"lean": {"proposition": premise}},
        }]
        self.case.anchor(f"Restricted intake {sample}", "True", choices)
        source, _, _ = self.case.anchor(
            f"Public target with local-only premise {sample}", "True", choices, sensitivity="public",
            statement_text=f"Public result chosen for this disclosure case {sample}.",
        )
        [source_premise] = [item for item in cli("research", "basis", source["packet_id"])
                            if item["body"]["kind"] == "hypothesis"]
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            with self.assertRaisesRegex(ValueError, "restricted or unclassified basis"):
                store.query("verifier", "SELECT rm.require_public_model_packet(%s)", source["packet_id"])
        origin = self.case.document("origin-chain", {
            "format_version": 1, "nodes": [
                {"name": "AdditionRoot", "proposition": "∀ (k : Nat), k + (0 + 0) = k",
                 "proof": final.intro(2, {"rfl": True})},
                {"name": "AdditionTwice", "proposition": "∀ (k : Nat), (k + 0) + 0 = k",
                 "proof": final.intro(2, final.apply({"node_ref": "AdditionRoot"}, {"use": 0}, {"use": 1}))},
            ],
        })
        first, second = cli("lean", "derive", source["id"], origin)["lemmas"]
        goal = "∀ n : Nat, ((n + 0) + 0 = n) ∧ (n + 7 = 7 + n)"
        target, _, _ = self.case.anchor(f"Public target with a separate open obligation {sample}", goal, [{
            "kind": "hypothesis", "natural": "Truth is the approved destination premise.",
            "formal": {"lean": {"proposition": "True"}},
        }], sensitivity="public")
        # Other cases may retire an entire proof artifact by content hash.
        # Keep this control's exact proposition distinct across fixtures.
        public_proposition = f"({sample.int} : Nat) = {sample.int}"
        public_helper = self.case.document("public-helper", {
            "format_version": 1, "nodes": [
                {"name": "PublicRefl", "proposition": public_proposition, "proof": final.intro(1, {"rfl": True})},
            ],
        })
        [public_lemma] = cli("lean", "derive", target["id"], public_helper)["lemmas"]
        env = self.case.fixture.env
        directory = self.case.fixture.output
        probe = directory / "model-request.json"
        (directory / "public_context_probe.py").write_text(MODEL_EGRESS_PROBE, encoding="utf-8")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(directory), env.get("PYTHONPATH"))))
        env.update(
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="disclosure-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL="disclosure-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="disclosure-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER="public_context_probe:factory",
            RM_TEST_MODEL_EGRESS_PROBE=str(probe),
        )
        policy = cli("advance", "propose", "--for-packet", target["packet_id"], "--lean-model-rounds", 1)
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"], "--actor", "test researcher",
            "--reason", "Use a local fixture to capture the authorized request.")
        work = cli("advance", "worker", "--policy", policy["id"], "--limit", 1)
        self.assertFalse(work["errors"], work)
        request = json.loads(probe.read_text(encoding="utf-8"))
        self.assertEqual(request["antecedents"], ["True"])
        self.assertEqual(request["consequent"], goal)
        self.assertIn({
            "id": public_lemma["id"], "proposition": public_proposition, "antecedents": ["True"],
        }, request["checked_local_lemmas"])
        self.assertNotIn(premise, json.dumps(request))
        local = {row["body"]["working_lemma_replay"]["lemma_id"]: row
                 for row in cli("lean", "local-theorems", target["id"]) if "working_lemma_replay" in row["body"]}
        self.assertEqual(set(local), {first["id"], second["id"]})
        self.assertIn(premise, local[first["id"]]["proposition"])
        self.assertEqual(local[second["id"]]["body"]["local_lemma_ids"], [local[first["id"]]["id"]])
        self.assertTrue(all(row["status"] == "verified" for row in local.values()))
        self.assertFalse({row["id"] for row in request["checked_local_lemmas"]}
                         & {row["id"] for row in local.values()})
        self.assertEqual(cli("lean", "budget", target["id"])["reserved_calls"], 1)

        with patch.dict(os.environ, env), Store() as store:
            def visible():
                return {str(row["id"]) for row in store.query(
                    "verifier", "SELECT * FROM rm.public_lean_model_lemmas(%s)", target["id"])}

            replayed = {row["id"] for row in local.values()}
            self.assertFalse(replayed & visible())
            cli("research", "release-item", source_premise["id"], "--hash", source_premise["sha256"],
                "--actor", "test researcher", "--reason", "Release exactly the previously restricted premise.")
            self.assertLessEqual(replayed, visible())
