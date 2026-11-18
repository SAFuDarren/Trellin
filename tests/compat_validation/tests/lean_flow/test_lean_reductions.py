"""Check persistent sufficient conditions with native Lean and PostgreSQL."""

import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_final_local_lemmas as final
import test_lean_flow as lean

from research_multiplier.storage.postgres import Store


MODEL_EGRESS_PROBE = """\
import json
import os
from pathlib import Path

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


class Adapter:
    capabilities = ModelCapabilities()

    def complete(self, request):
        context = json.loads(request["messages"][1]["content"])
        Path(os.environ["RM_TEST_MODEL_EGRESS_PROBE"]).write_text(json.dumps(context), encoding="utf-8")
        reply = {"action": "blocked", "reason": "Stop after observing the permitted request."}
        return AdapterReply(text=json.dumps(reply), model_id=request["model_id"])


def factory(config):
    return Adapter()
"""


@unittest.skipUnless(lean.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanReductionTests(unittest.TestCase):
    def setUp(self):
        self.case = final.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli
        self.document = self.case.document

    def test_public_search_checks_ready_reduction_without_a_model_or_a_budget_slot(self):
        sample = uuid4()
        choices = [{
            "kind": "hypothesis", "natural": f"Truth holds in local reduction fixture {sample}.",
            "formal": {"lean": {"proposition": "True"}},
        }]
        source, _, _ = self.case.anchor(f"program-first-local-{sample}", "True ∧ True", choices)
        anchor, scope, _ = self.case.anchor(f"program-first-reduction-{sample}", "True ∧ True", choices,
                                           sensitivity="public")
        restricted, = self._hypotheses(source)
        premise_version, = self._hypotheses(anchor)
        self.assertEqual(premise_version["id"], restricted["id"])
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            with self.assertRaisesRegex(ValueError, "restricted or unclassified basis"):
                store.query("verifier", "SELECT rm.require_public_model_packet(%s)", anchor["packet_id"])
        premise = {"use": 0}
        obligation = {"use": 1}
        spec = self.document("program-first-reduction", {
            "format_version": 1, "obligations": ["True"],
            "proof": final.intro(2, {"and": [premise, obligation]}),
        })
        command = ("lean", "reductions", anchor["id"])
        saved, = self.cli(*command, "--register", spec)["reductions"]
        local = self.document("program-first-obligation", {"format_version": 1, "nodes": [{
            "name": "Truth", "proposition": "True", "proof": final.intro(1, premise),
        }]})
        self.cli("lean", "derive", anchor["id"], local)
        self.cli("lean", "budget", anchor["id"], "--limit", 0, "--actor", "researcher",
                 "--reason", "Run deterministic work only.")
        result = self.cli("lean", "search", anchor["id"], "--confirm-public", "--out", self.case.fixture.output)
        self.assertEqual(result["status"], "awaiting_evidence_review")
        self.assertEqual(result["reduction_id"], saved["implication_id"])
        self.assertEqual(result["rounds"], [])
        self.assertEqual(self.cli("lean", "budget", anchor["id"])["reserved_calls"], 0)
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(receipt["claim_id"], scope["claim_id"])
        self.assertEqual(receipt["contract_comparison"]["scope_status"], "exact")
        self.assertEqual(receipt["verification"]["review_status"], "pending")
        self.assertTrue(all(row["model_provider"] is None for row in receipt["verification"]["attempts"]))
        self.assertEqual(receipt["key_steps"]["expected_type"], "True → And True True")
        self.assertIn({"item": "H1", "proposition": "True", "occurs_in_proof_term": True},
                      receipt["key_steps"]["premises"])

    def test_public_model_search_denies_restricted_basis_until_exact_release(self):
        sample = uuid4()
        choices = [{
            "kind": "hypothesis", "natural": f"Truth is the reviewed premise of disclosure fixture {sample}.",
            "formal": {"lean": {"proposition": "True"}},
        }]
        self.case.anchor(f"model-egress-private-origin-{sample}", "True ∧ True", choices)
        consequent = "∀ f : Nat → Nat, f 19 = f 19"
        anchor, scope, _ = self.case.anchor(f"model-egress-public-target-{sample}", consequent, choices,
                                           sensitivity="public")
        premise, = self._hypotheses(anchor)
        search = ("lean", "search", anchor["id"])
        budget = ("lean", "budget", anchor["id"])
        self.cli(*budget, "--limit", 1, "--actor", "researcher", "--reason", "Allow one public proposal.")
        local = self.cli(*search, "--rounds", 0)
        self.assertEqual(local["status"], "proof_search_blocked", local)

        directory = self.case.fixture.output
        probe = directory / "public-model-context.json"
        (directory / "model_egress_probe.py").write_text(MODEL_EGRESS_PROBE, encoding="utf-8")
        environment = self.case.fixture.env
        environment["PYTHONPATH"] = os.pathsep.join(filter(None, (str(directory), environment.get("PYTHONPATH"))))
        adapter = "model_egress_probe:factory"
        environment.update(
            RESEARCH_MULTIPLIER_TRUSTED_LOCAL_ADAPTERS=adapter,
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="egress-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL="egress-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="egress-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER=adapter,
            RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_PROVIDER="egress-fixture",
            RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_MODEL="egress-fixture",
            RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_FAMILY="egress-fixture",
            RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_ADAPTER=adapter,
            RM_TEST_MODEL_EGRESS_PROBE=str(probe),
        )
        self.assertIn("--confirm-public", self.cli(*search, "--rounds", 1, expect=2))
        self.assertIn("--confirm-public for a public Lean search scope",
                      self.cli(*search, "--rounds", 1, "--confirm-local", expect=2))
        self.assertIn("restricted or unclassified basis",
                      self.cli(*search, "--rounds", 1, "--confirm-public", expect=2))
        self.assertFalse(probe.exists())
        self.assertEqual(self.cli(*budget)["reserved_calls"], 0)
        with patch.dict(os.environ, environment), Store() as store:
            self.assertEqual(store.query("verifier", """
                SELECT id FROM rm.jobs WHERE claim_id = %s AND budget ->> 'max_model_calls' <> '0'
            """, scope["claim_id"]), [])

        self.cli("research", "release-item", premise["id"], "--hash", premise["sha256"],
                 "--actor", "researcher", "--reason", "Release the exact reviewed premise for this test.")
        result = self.cli(*search, "--rounds", 1, "--confirm-public")
        self.assertEqual(result["status"], "blocked", result)
        [round_result] = result["rounds"]
        self.assertEqual(round_result["status"], "blocked")
        request = json.loads(probe.read_text(encoding="utf-8"))
        self.assertEqual(request["consequent"], consequent)
        self.assertEqual(request["antecedents"], ["True"])
        self.assertEqual(self.cli(*budget)["reserved_calls"], 1)
        self.assertEqual(self.cli("lean", "show", anchor["id"])["checks"], [])

    def _hypotheses(self, anchor):
        return [item for item in self.cli("research", "basis", anchor["packet_id"])
                if item["body"]["kind"] == "hypothesis"]

    def test_persist_route_discharge_only_verified_lemmas_and_recheck_on_retirement(self):
        self.assertIsNotNone(lean.PROJECT)
        premise = "∀ x : Real, x ≤ x"
        first = "∀ x : Real, 0 ≤ x ^ 2"
        second = "∀ x : Real, 0 ≤ x ^ 2 ∧ x ≤ x"
        goal = "∀ x : Real, (0 ≤ x ^ 2 ∧ x ≤ x) ∧ 0 ≤ x ^ 2"
        anchor, scope, runtime = self.case.anchor("real-square-reduction", goal, [
            {"kind": "hypothesis", "natural": "Each real is no larger than itself.",
             "formal": {"lean": {"proposition": premise}}},
            {"kind": "theorem", "natural": "A real square is nonnegative.",
             "formal": {"lean": {"proposition": first, "declaration": "sq_nonneg"}}},
        ], module="Mathlib.Data.Real.Basic")
        lookup = ("lean", "reductions", anchor["id"], *runtime, "--out", self.case.fixture.output)
        def use(index):
            return {"use": index}

        implication = final.intro(4, {"and": [final.apply(use(2), use(3)), final.apply(use(1), use(3))]})
        spec = self.document("reduction", {"format_version": 1, "obligations": [first, second], "proof": implication})
        invalid = self.document("wrong-implication", {
            "format_version": 1, "obligations": [first, premise], "proof": implication,
        })
        self.assertIn("Lean", self.cli(*lookup, "--register", invalid, expect=2))
        self.assertFalse(self.cli(*lookup)["reductions"])
        basis = self.cli("research", "basis", anchor["packet_id"])
        square, = [row["id"] for row in basis if row["body"]["kind"] == "theorem"]
        square_proof = final.intro(2, final.apply({"ref": square}, use(1)))
        first_plan = self.document("first-obligation", {"format_version": 1, "nodes": [
            {"name": "Equivalent", "proposition": f"({first})", "proof": square_proof},
            {"name": "Square", "proposition": first, "proof": square_proof},
        ]})

        human = self.case.fixture.env.pop("RM_HUMAN_DSN")

        def resume():
            # The research projection must work with only the human login.
            with patch.dict(self.case.fixture.env, {"RM_HUMAN_DSN": human, "RM_VERIFIER_DSN": "",
                                                    "RM_SCHEDULER_DSN": "", "RM_AGENT_DSN": ""}):
                return self.cli("research", "resume", scope["claim_id"], "--details")

        try:
            recorded, = self.cli(*lookup, "--register", spec)["reductions"]
            self.assertEqual(recorded["status"], "awaiting_obligations")
            equivalent, first_row = self.cli("lean", "derive", anchor["id"], first_plan, *runtime)["lemmas"]
            self.assertNotEqual(equivalent["id"], first_row["id"])
            still_open = self.cli(*lookup, "--work")
            self.assertNotIn("goal", still_open)
            pending, = still_open["reductions"]
            self.assertEqual([row["lemma_id"] for row in pending["obligations"]], [first_row["id"], None])
            resumed = resume()
            self.assertNotIn("search_budget", resumed)
            route, = resumed["reductions"]["items"]
            self.assertEqual(route["status"], pending["status"])
            self.assertEqual(route["details"]["obligation_lemma_ids"], [first_row["id"], None])
            self.assertEqual([item["verified"] for item in route["obligations"]], [True, False])
            self.assertEqual(resumed["next"]["action"], "prove_reduction_obligations")

            second_plan = self.document("second-obligation", {"format_version": 1, "nodes": [{
                "name": "Pair", "proposition": second,
                "proof": final.intro(2, {"and": [
                    final.apply({"ref": first_row["id"]}, use(0), use(1)), final.apply(use(0), use(1)),
                ]}),
            }]})
            second_row, = self.cli("lean", "derive", anchor["id"], second_plan, *runtime)["lemmas"]
            ready, = self.cli(*lookup)["reductions"]
            resumed = resume()
            self.assertEqual(resumed["reductions"]["items"][0]["status"], ready["status"])
            self.assertEqual(resumed["next"]["action"], "close_reduction")
            self.assertEqual(resumed["next"]["reference"]["implication_id"], recorded["implication_id"])
            result = self.cli(*lookup, "--work")
            closed, = result["reductions"]
            self.assertEqual(closed["status"], "goal_checked")
            self.assertEqual(closed["review_status"], "pending")
            self.assertEqual(result["used_reduction"], recorded["implication_id"])
            self.assertEqual(result["goal"]["expected_type"], anchor["body"]["elaborated_theorem_type"])
            self.assertCountEqual(result["goal"]["local_lemma_ids"],
                                  [first_row["id"], second_row["id"], recorded["implication_id"]])
            self.assertNotIn("goal", self.cli(*lookup, "--work"))
            resumed = resume()
            checked, = resumed["reductions"]["items"]
            self.assertEqual(checked["status"], closed["status"])
            self.assertEqual(checked["reference"]["evidence_id"], closed["evidence_id"])
            self.assertEqual(checked["review_status"], closed["review_status"])
            self.assertEqual(resumed["next"]["action"], "review_evidence")
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human

        inbox = self.cli("review", "inbox", "--claim", scope["claim_id"])
        self.assertEqual([row["item_id"] for row in inbox], [closed["evidence_id"]])
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            with self.assertRaisesRegex(ValueError, "current, exact implication"):
                store.query("verifier", "SELECT rm.record_lean_reduction(%s,%s,%s::text[])",
                            anchor["id"], recorded["implication_id"], [first, premise])
            with self.assertRaisesRegex(ValueError, "permission denied"):
                store.query("agent", "SELECT rm.record_lean_reduction(%s,%s,%s::text[])",
                            anchor["id"], recorded["implication_id"], [first, second])
            with self.assertRaisesRegex(ValueError, "permission denied"):
                store.query("agent", "SELECT * FROM rm.lean_reduction_status(%s)", anchor["id"])
        self.cli("research", "retire-input", "source", first_row["body"]["candidate"]["sha256"],
                 "--reason", "Check that downstream proof waits for recheck.")
        retired, = self.cli(*lookup, "--work")["reductions"]
        self.assertEqual(retired["status"], "needs_recheck")
        self.assertIsNone(retired["evidence_id"])
        self.assertEqual(retired["stale_evidence_id"], closed["evidence_id"])
        stale, = resume()["reductions"]["items"]
        self.assertEqual(stale["status"], retired["status"])
        self.assertNotIn("evidence_id", stale["reference"])
        self.assertEqual(stale["details"]["stale_evidence_id"], retired["stale_evidence_id"])
        self.assertEqual(stale["details"]["obligation_lemma_ids"], [None, None])
        packet = self.cli("review", "packet", closed["evidence_id"])
        self.assertTrue(packet["verification"]["needs_recheck"])

    def test_resume_projects_the_current_public_search_budget(self):
        anchor, scope, _ = self.case.anchor("public-resume-budget", "True ∧ True", [
            {"kind": "hypothesis", "natural": "Truth supplies the premise for viewing a public search budget.",
             "formal": {"lean": {"proposition": "True"}}},
        ], sensitivity="public")
        self.cli("lean", "budget", anchor["id"], "--limit", 3,
                 "--actor", "researcher", "--reason", "Demonstrate the current budget in the resume snapshot.")
        with patch.dict(self.case.fixture.env, {"RM_VERIFIER_DSN": "", "RM_SCHEDULER_DSN": "", "RM_AGENT_DSN": ""}):
            researched = self.cli("research", "resume", scope["claim_id"])
        self.assertEqual(researched["search_budget"], {"call_limit": 3, "reserved_calls": 0, "remaining_calls": 3})
