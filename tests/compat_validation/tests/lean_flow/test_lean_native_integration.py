"""Reference source and Lake proofs through ordinary search and blueprint entry points."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as proof
import test_lean_flow as database
import test_native_theorem_reuse as native


SOURCE = "(1 : Nat) = 0"
SOURCE_PREMISE = "(0 : Nat) = 1"
DEST_PREMISE = f"True ∧ (True ∧ ({SOURCE_PREMISE}))"

ADAPTER = '''\
import json
import os

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


class Adapter:
    capabilities = ModelCapabilities()

    def complete(self, request):
        user = json.loads(request["messages"][1]["content"])
        version = os.environ["RM_TEST_SEARCH_NATIVE_VERSION"]
        theorem, = [row for row in user["approved_theorems"]
                    if row.get("application") == "native" and row["id"] == version]
        assert theorem["antecedents"] == ["(0 : Nat) = 1"]
        assert theorem["proposition"] == "(1 : Nat) = 0"
        if user["round"] == 1:
            result = {"action": "lemma", "proposition": theorem["proposition"],
                      "proof": {"intro": {"count": 1, "body": {"ref": theorem["id"]}}}}
        else:
            assert user["history"][0]["status"] == "lemma_verified"
            lemma, = user["checked_local_lemmas"]
            result = {"action": "proof", "proof": {"intro": {"count": 2, "body": {
                "and": [{"apply": [{"ref": lemma["id"]}, {"use": 0}]}, {"rfl": True}]
            }}}}
        return AdapterReply(text=json.dumps(result), model_id=request["model_id"])


def factory(config):
    return Adapter()
'''


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class NativeLeanIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = native.NativeTheoremReuseTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case = self.fixture.case
        self.cli = self.fixture.cli

    def source_file(self, premise=SOURCE_PREMISE, conclusion=SOURCE):
        source, _, _ = self.case.anchor(
            "automatic-search-native-source", conclusion,
            [native.hypothesis(premise, "Original premise for native search.")], sensitivity="public",
            statement_text="Reverse the hypothesis in the native search source.",
        )
        path = self.fixture.external / "search-source.lean"
        path.write_text(
            f"theorem searchSource (h : {premise}) : {conclusion} := Eq.symm h\n",
            encoding="utf-8",
        )
        checked = self.cli("lean", "check-source", source["id"], path, "--declaration", "searchSource")
        self.fixture.promote(checked)
        path.unlink()
        return self.fixture.exported(checked)

    def test_search_uses_native_intermediate_then_replays_local_lemma(self):
        version = self.source_file()
        destination, scope, _ = self.case.anchor(
            "automatic-search-native-dest", f"∀ n : Nat, ({SOURCE}) ∧ n = n",
            [native.hypothesis(DEST_PREMISE, "Nested destination premise for native search.")], sensitivity="public",
        )
        adapter_dir = self.fixture.external / "model-fixture"
        adapter_dir.mkdir()
        (adapter_dir / "native_search_adapter.py").write_text(ADAPTER, encoding="utf-8")
        env = self.case.fixture.env
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(adapter_dir), env.get("PYTHONPATH"))))
        env.update(RM_TEST_SEARCH_NATIVE_VERSION=version["id"],
                   RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="native-search-fixture",
                   RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL="native-search-author",
                   RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="fixture",
                   RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER="native_search_adapter:factory")
        result = self.fixture.without_human("lean", "search", destination["id"], "--confirm-public", "--rounds", "2")
        self.assertEqual(result["status"], "awaiting_evidence_review", result)
        first, last = result["rounds"]
        self.assertEqual(first["status"], "lemma_verified", first)
        self.assertEqual(last["status"], "awaiting_evidence_review", last)
        lemma, = self.cli("lean", "local-theorems", destination["id"])
        self.assertEqual(lemma["id"], first["lemma_id"])
        self.assertIn(version["id"], lemma["body"]["basis_version_ids"])
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertIn(lemma["id"], receipt["key_steps"]["local_lemma_ids"])
        self.assertEqual(receipt["claim_id"], scope["claim_id"])
        self.cli("research", "retire-item", version["id"], "--reason", "Withdraw the reused theorem version.")
        self.assertTrue(self.cli("review", "packet", result["evidence_id"])["verification"]["needs_recheck"])

    def test_search_closes_checked_reduction_with_native_obligation_before_model(self):
        premise = "(17 : Nat) = 42"
        conclusion = "(42 : Nat) = 17"
        version = self.source_file(premise, conclusion)
        destination, _, _ = self.case.anchor(
            "automatic-search-native-reduction", f"∀ n : Nat, ({conclusion}) ∧ n = n",
            [native.hypothesis(f"True ∧ (True ∧ ({premise}))", "Nested premise of this native reduction.")],
            sensitivity="public",
            statement_text="Finish the native reduction without a model.",
        )
        reduction = self.case.document("automatic-native-reduction", {
            "format_version": 1, "obligations": [conclusion],
            "proof": proof.intro(3, {"and": [{"use": 1}, {"rfl": True}]}),
        })
        pending, = self.cli("lean", "reductions", destination["id"], "--register", reduction)["reductions"]
        self.assertEqual(pending["status"], "awaiting_obligations")
        result = self.fixture.without_human("lean", "search", destination["id"], "--rounds", "0")
        self.assertEqual(result["status"], "awaiting_evidence_review", result)
        self.assertEqual(result["rounds"], [])
        self.assertEqual(result["reduction_id"], pending["implication_id"])
        obligation, = result["native_obligations"]
        self.assertEqual(obligation["theorem_version_id"], version["id"])
        self.assertEqual(obligation["status"], "lemma_verified")
        self.assertEqual(self.cli("lean", "budget", destination["id"])["reserved_calls"], 0)

    def test_lake_blueprint_consumes_native_proof_and_keeps_missing_premise_as_work(self):
        source_project = self.fixture.lake_project("blueprint-native-origin", source=True)
        destination_project = self.fixture.lake_project("blueprint-native-destination", source=False)
        fact = {"kind": "theorem", "natural": "The Lake blueprint's approved marker-bound declaration.",
                "formal": {"lean": {"proposition": "ReplayFacts.marker ≤ 1", "declaration": "ReplayFacts.trusted"}}}
        with patch.object(database, "PROJECT", source_project):
            origin, _, flags = self.case.anchor(
                "native-blueprint-lake-origin", native.LAKE_GOAL,
                [native.hypothesis(native.LAKE_PREMISE, "Lake blueprint origin premise."), fact],
                module="ReplayOrigin",
            )
        checked = self.cli("lean", "check-source", origin["id"], "--module", "ReplayOrigin",
                           "--declaration", "ReplayOrigin.original", *flags)
        self.fixture.promote(checked)
        version = self.fixture.exported(checked)
        with patch.object(database, "PROJECT", destination_project):
            destination, _, flags = self.case.anchor(
                "native-blueprint-lake-destination", f"({native.LAKE_GOAL}) ∧ True",
                [native.hypothesis(f"({native.LAKE_PREMISE}) ∧ True", "Lake blueprint destination premise.")],
                module="ReplayFacts",
            )
            blocked, blocked_scope, _ = self.case.anchor(
                "native-blueprint-lake-blocked", f"({native.LAKE_GOAL}) ∧ True",
                [native.hypothesis("True", "Comparison permits only truth.")], module="ReplayFacts",
                statement_text="Compare a weaker scope while keeping the original goal.",
            )
        fact_version, = [row for row in self.cli("research", "basis", origin["packet_id"])
                         if row["body"].get("formal", {}).get("lean", {}).get("declaration") == "ReplayFacts.trusted"]
        for statement in (destination, blocked):
            self.cli("research", "attach-item", statement["packet_id"], fact_version["id"],
                     "--reason", "Reuse the already reviewed Lake declaration.")
        recipe = proof.intro(1, {"and": [{"node_ref": "origin"}, {"trivial": True}]})
        graph = self.case.document("native-lake-blueprint", {"format_version": 1, "nodes": [
            {"name": "origin", "statement_id": origin["id"]},
            {"name": "successor", "statement_id": destination["id"], "proof": recipe},
            {"name": "blocked", "statement_id": blocked["id"], "proof": recipe},
        ]})
        source_row, accepted, open_route = self.fixture.without_human("lean", "blueprint", graph, "--work",
                                                                    "--limit", "2", *flags)["nodes"]
        self.assertEqual(source_row["status"], "available")
        self.assertEqual(source_row["theorem_version_id"], version["id"])
        self.assertEqual(accepted["status"], "awaiting_evidence_review", accepted)
        self.assertEqual(open_route["status"], "proof_search_blocked", open_route)
        self.assertIsNone(open_route["evidence_id"])
        self.assertTrue(open_route["obligations"])
        self.assertEqual(self.cli("research", "resume", blocked_scope["claim_id"])["decisions"]["items"], [])
