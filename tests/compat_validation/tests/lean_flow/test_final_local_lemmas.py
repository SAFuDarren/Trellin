"""Exercise final claim verification and retirement of a scoped Lean proof chain."""

import os
import unittest
from unittest.mock import patch

import test_lean_flow as lean_fixtures
import test_local_lemmas as fixtures

from research_multiplier.storage.postgres import Store


def intro(count, body):
    return {"intro": {"count": count, "body": body}}


def apply(*terms):
    return {"apply": list(terms)}


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FinalLocalLemmaTests(unittest.TestCase):
    def setUp(self):
        case = fixtures.LocalLemmaTests()
        case.setUp()
        self.fixture = case.fixture
        self.cli = case.cli
        self.document = case.document
        self.addCleanup(case.doCleanups)

    def anchor(self, label, consequent, choices, *, sensitivity="restricted", module=None, statement_text=None):
        target = self.document(label, {
            "title": label, "domain": "lean-core", "sensitivity": sensitivity,
            "statement": statement_text or consequent, "formal": {"lean": {"proposition": consequent}},
        })
        preview = self.cli("intake", "preview-target", target)
        frozen = self.cli("intake", "freeze-target", target, "--hash", preview["target_sha256"],
                          "--actor", "researcher", "--reason", "Review the exact target.")
        notes = self.document(label + "-basis", {"items": choices})
        draft = self.cli("intake", "draft", notes, "--target", frozen["id"])
        decisions = self.document(label + "-decisions", {
            item["id"]: {"action": "accept", "reason": "Review this exact formal version."}
            for item in draft["comparison"] if item["status"] not in {"reuse", "omit_frozen_target"}
        })
        self.cli("intake", "confirm", draft["id"], "--hash", draft["intake_sha256"], "--decisions", decisions,
                 "--actor", "researcher", "--reason", "Freeze the reviewed starting basis.")
        scope = self.cli("intake", "target", frozen["id"])
        runtime = ("--mathlib-project", lean_fixtures.PROJECT) if module else ()
        imports = (*runtime, "--import", module) if module else ()
        anchor = self.cli("lean", "draft-theory", scope["packet_id"], *imports)
        self.cli("lean", "confirm", anchor["id"], "--version", anchor["version"], "--hash", anchor["statement_sha256"],
                 "--actor", "researcher", "--reason", "Review the exact Lean scope and environment.")
        return anchor, scope, runtime

    def test_mathlib_chain_proves_frozen_goal_then_transitively_becomes_stale(self):
        square = "0 ≤ x ^ 2"
        order = "x ≤ x"
        l1_prop = f"∀ x : Real, {square} ∧ {order}"
        l2_body = f"({square} ∧ {order}) ∧ {order}"
        l2_prop = f"∀ x : Real, {l2_body}"
        consequent = f"∀ x : Real, ({l2_body}) ∧ {order}"
        choices = [
            {"kind": "hypothesis", "natural": "The original premise is reflexivity on the reals.",
             "formal": {"lean": {"proposition": f"∀ x : Real, {order}"}}},
            {"kind": "theorem", "natural": "A real square is nonnegative.",
             "formal": {"lean": {"proposition": f"∀ x : Real, {square}", "declaration": "sq_nonneg"}}},
        ]
        module = "Mathlib.Data.Real.Basic"
        anchor, scope, runtime = self.anchor("real-squares", consequent, choices, module=module)
        self.assertEqual(anchor["body"]["environment"]["external"]["modules"], [module])
        basis = self.cli("research", "basis", anchor["packet_id"])
        library_theorem, = [row["id"] for row in basis if row["body"]["kind"] == "theorem"]
        hypothesis, x = {"use": 0}, {"use": 1}
        hx = apply(hypothesis, x)
        plan = self.document("proof-chain", {"format_version": 1, "nodes": [
            {"name": "L1", "proposition": l1_prop, "proof": intro(2, {
                "and": [apply({"ref": library_theorem}, x), hx],
            })},
            {"name": "L2", "proposition": l2_prop, "proof": intro(2, {
                "and": [apply({"node_ref": "L1"}, hypothesis, x), hx],
            })},
        ], "goal_proof": intro(2, {"and": [apply({"node_ref": "L2"}, hypothesis, x), hx]})})

        human = self.fixture.env.pop("RM_HUMAN_DSN")
        try:
            chain = self.cli("lean", "derive", anchor["id"], plan, *runtime, "--out", self.fixture.output)
            l1, l2 = chain["lemmas"]
            proof = chain["goal"]
            self.assertEqual([row["status"] for row in (l1, l2)], ["verified", "verified"])
            self.assertEqual(l1["body"]["basis_version_ids"], [library_theorem])
            self.assertEqual(l2["body"]["local_lemma_ids"], [l1["id"]])
            self.assertTrue(proof["accepted"] and proof["kernel_rechecked"] and proof["scope_matches"])
            self.assertEqual(proof["expected_type"], anchor["body"]["elaborated_theorem_type"])
            self.assertEqual(proof["local_lemma_ids"], [l2["id"]])
            self.assertCountEqual(proof["expanded_local_lemma_ids"], [l1["id"], l2["id"]])
            self.assertIn(library_theorem, proof["expanded_basis_version_ids"])
            self.assertTrue(any(row["declaration"] == "sq_nonneg" and row["module"].startswith("Mathlib.")
                                for row in proof["dependencies"]["external_constants"]))
            original = self.document("direct-main-proof", {"format_version": 1, "proof": intro(2, {
                "and": [apply({"ref": l2["id"]}, hypothesis, x), hx],
            })})
            extra = self.document("extra-hypothesis", {"format_version": 1, "proof": intro(3, {
                "and": [apply({"ref": l2["id"]}, hypothesis, x), {"use": 2}],
            })})
            check = ("lean", "check-theory", anchor["id"])
            self.assertIn("Lean", self.cli(*check, extra, *runtime, "--out", self.fixture.output, expect=2))
            replay = self.cli(*check, original, *runtime, "--out", self.fixture.output)
            self.assertEqual(replay["evidence_id"], proof["evidence_id"])
            with patch.dict(os.environ, self.fixture.env), Store() as store:
                attempts = store.query("verifier", """
                    SELECT a.model_provider, j.budget ->> 'max_model_calls' AS model_budget
                    FROM rm.attempts a JOIN rm.jobs j ON j.id = a.job_id
                    WHERE j.payload ->> 'lean_statement_id' = %s
                """, anchor["id"])
                self.assertTrue(attempts)
                self.assertTrue(all(a["model_provider"] is None and a["model_budget"] == "0" for a in attempts))
        finally:
            self.fixture.env["RM_HUMAN_DSN"] = human

        receipt = proof["evidence_id"]
        inbox = self.cli("review", "inbox", "--claim", scope["claim_id"])
        self.assertEqual([row["item_id"] for row in inbox], [receipt])
        self.assertEqual(self.cli("review", "packet", receipt)["key_steps"]["local_lemma_ids"], [l2["id"]])
        self.cli("review-evidence", receipt, "approve", "--actor", "researcher", "--reason", "Review the final result.")
        self.cli("promote-evidence", receipt, "--reason", "Apply the reviewed result.")
        self.assertEqual(self.cli("review", "packet", receipt)["claim_status"], "proved")
        self.cli("research", "retire-input", "source", l1["body"]["candidate"]["sha256"],
                 "--reason", "Withdraw L1 only.")
        stale = self.cli("review", "packet", receipt)
        self.assertEqual(stale["claim_status"], "needs_recheck")
        self.assertTrue(stale["verification"]["needs_recheck"])
        self.assertIn("recheck", self.cli(*check, original, *runtime, "--out", self.fixture.output, expect=2))

    def test_lean_negation_can_reuse_a_scoped_lemma(self):
        choice = {"kind": "hypothesis", "natural": "Truth holds.", "formal": {"lean": {"proposition": "True"}}}
        anchor, scope, _ = self.anchor("false-under-true-premise", "False", [choice])
        plan = self.document("refutation-lemma", {"format_version": 1, "nodes": [
            {"name": "L1", "proposition": "True", "proof": intro(1, {"use": 0})},
        ]})
        lemma, = self.cli("lean", "derive", anchor["id"], plan)["lemmas"]
        proof = self.document("negation", {"format_version": 1, "proof":
            intro(1, apply({"use": 0}, apply({"ref": lemma["id"]}, {"trivial": True})))})
        report = self.cli("lean", "check-theory", anchor["id"], proof, "--refute", "--out", self.fixture.output)
        self.assertEqual(report["refutation_mode"], "logical_negation")
        self.assertEqual(report["expected_type"], "Not (True → False)")
        self.assertEqual(report["local_lemma_ids"], [lemma["id"]])
        self.assertEqual(report["evidence_kind"], "counterexample")
        self.assertEqual(self.cli("review", "packet", report["evidence_id"])["claim_id"], scope["claim_id"])
        self.cli("research", "retire-input", "source", lemma["body"]["candidate"]["sha256"],
                 "--reason", "Withdraw the negation lemma.")
        self.assertTrue(self.cli("review", "packet", report["evidence_id"])["verification"]["needs_recheck"])
