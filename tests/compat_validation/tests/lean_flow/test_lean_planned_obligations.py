"""Verify a multi-obligation reduction from one structured model response."""

import json
import os
import sys
import unittest
from unittest.mock import patch

import test_lean_flow as fixtures
import test_final_local_lemmas as final_fixtures
from test_final_local_lemmas import apply, intro

from research_multiplier.workflows import lean_search


PLAN = {
    "action": "reduction",
    "method": "Prove the addition identity once and reuse it in a stronger lemma.",
    "obligations": ["∀ n : Nat, n + 0 = n", "∀ n : Nat, ((n + 0) + 0 = n) ∧ True"],
    "proof": intro(3, {"and_left": apply({"use": 1}, {"use": 2})}),
    "obligation_proofs": [intro(1, {"rfl": True}), intro(1, {"and": [
        apply({"node_ref": "Obligation1"}, {"use": 0}), {"trivial": True},
    ]})],
}


class PlannedObligationValidation(unittest.TestCase):
    def test_first_proof_cannot_reference_later_or_unlisted_facts(self):
        for proof, message in [({"node_ref": "Obligation2"}, "only earlier obligations"),
                               ({"ref": "unapproved"}, "only listed approved theorem")]:
            with self.subTest(proof=proof), self.assertRaisesRegex(ValueError, message):
                lean_search._candidate({**PLAN, "obligation_proofs": [proof, None]}, set())
        self.assertEqual(lean_search._candidate(PLAN, set()), PLAN)


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class PlannedObligationAdmission(unittest.TestCase):
    def test_checker_stores_both_model_nodes_and_closes_without_a_second_call(self):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        anchor, scope, _ = case.anchor("use an explicitly checked proof chain", "∀ n : Nat, (n + 0) + 0 = n", [],
                                       sensitivity="public")
        folder = str(case.fixture.output)
        (case.fixture.output / "lean_attached_plan.py").write_text(
            "from research_multiplier.model_adapters import AdapterReply, ModelCapabilities\n"
            "class Adapter:\n"
            "    capabilities = ModelCapabilities(output_token_limit_enforced=True)\n"
            "    def complete(self, request):\n"
            f"        return AdapterReply(text={json.dumps(PLAN)!r}, model_id=request['model_id'])\n"
            "def factory(_):\n"
            "    return Adapter()\n", encoding="utf-8",
        )
        author = "RESEARCH_MULTIPLIER_LEAN_AUTHOR_"
        environment = {**case.fixture.env, **{
            author + "PROVIDER": "planned-fixture", author + "MODEL": "planned-fixture", author + "FAMILY": "planned-fixture",
            author + "ADAPTER": "lean_attached_plan:factory",
        }}
        environment.pop("RM_HUMAN_DSN")
        with patch.dict(os.environ, environment), patch.object(sys, "path", [folder, *sys.path]):
            result = lean_search.search(anchor["id"], rounds=2, confirm_public=True, max_new_rounds=2,
                                        output=case.fixture.output)
        self.assertEqual(result["status"], "awaiting_evidence_review", result)
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 1)
        [route] = result["rounds"]
        self.assertEqual(route["status"], "route_checked")
        self.assertEqual(result["reduction_id"], route["reduction_id"])
        first, second = [step for step in result["program_obligations"] if step.get("source") == "model_proposal"]
        self.assertEqual((first["status"], second["status"]), ("lemma_verified", "lemma_verified"))
        rows = {row["id"]: row for row in case.cli("lean", "local-theorems", anchor["id"])}
        self.assertEqual(rows[second["lemma_id"]]["body"]["local_lemma_ids"], [first["lemma_id"]])
        packet = case.cli("review", "packet", result["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
        self.assertEqual(packet["verification"]["review_status"], "pending")
        self.assertIn(first["lemma_id"], packet["key_steps"]["expanded_local_lemma_ids"])
        self.assertIn(second["lemma_id"], packet["key_steps"]["expanded_local_lemma_ids"])
