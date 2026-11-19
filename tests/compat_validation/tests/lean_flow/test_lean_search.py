"""Exercise bounded model proposals against the native Lean verifier and real PostgreSQL."""

import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import test_final_local_lemmas as final_fixtures
import test_lean_flow as lean_fixtures

from research_multiplier.proof import lean_reductions
from research_multiplier.storage.postgres import Store


ADAPTER = """\
import json

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


def intro(body):
    return {"intro": {"count": 1, "body": body}}


class Adapter:
    capabilities = ModelCapabilities(output_token_limit_enforced=True)

    def complete(self, request):
        user = json.loads(request["messages"][1]["content"])
        assert user["antecedents"] == ["True"]
        assert user["approved_theorems"] == []
        model_step = user["round"]
        lemmas = user["checked_local_lemmas"]
        premise = {"use": 0}
        if request["model_id"] == "fixed-search-budget":
            assert model_step == 1
            result = {"action": "lemma", "proposition": "(31 : Nat) = 31", "proof": intro({"rfl": True})}
        elif request["model_id"] == "fixed-search-proposed-routes":
            if model_step in (1, 2):
                obligations = ["False"] if model_step == 1 else ["∀ _n : Nat, True"]
                choice = "right" if model_step == 1 else "left"
                if model_step == 2:
                    assert user["open_routes"][0]["obligations"][0]["proposition"] == "False"
                result = {"action": "reduction", "obligations": obligations,
                          "proof": {"intro": {"count": 2, "body": {choice: {"use": 1}}}}}
            elif model_step == 3:
                assert len(user["open_routes"]) == 2
                result = {"action": "blocked", "reason": "The False obligation is unavailable."}
            else:
                assert model_step == 4 and user["history"][-1]["feedback"] == "Try another checked route before stopping."
                result = {"action": "lemma", "proposition": "∀ _n : Nat, True",
                          "proof": {"intro": {"count": 2, "body": {"use": 0}}}}
        elif request["model_id"] == "fixed-search-reduction":
            assert 1 <= model_step <= 2, "The checked reduction should close without another model call."
            if model_step == 2:
                failed, = user["history"]
                assert failed["status"] == "rejected"
            proof = premise if model_step == 1 else {"and": [premise, premise]}
            result = {"action": "lemma", "proposition": "∀ _n : Nat, True ∧ True",
                      "proof": {"intro": {"count": 2, "body": proof}}}
        elif request["model_id"] == "fixed-search-continue":
            assert model_step == 1 and not user["history"]
            if lemmas:
                assert len(lemmas) == 2
                assert {row["proposition"] for row in lemmas} == {"True ∧ True", "(True ∧ True) ∧ True"}
                second = next(row for row in lemmas if row["proposition"] == "(True ∧ True) ∧ True")
                result = {"action": "proof",
                           "proof": {"intro": {"count": 2, "body": {
                              "and": [{"apply": [{"ref": second["id"]}, premise]}, premise]}}}}
            else:
                result = {"action": "blocked", "reason": "No current local lemma was supplied."}
        elif user["consequent"] == "False":
            assert not lemmas
            if model_step == 1:
                result = {"action": "refutation", "proof": intro(premise), "hypotheses": ["False"]}
            else:
                failed, = user["history"]
                assert failed["status"] == "proposal_failed" and "no extra premises" in failed["feedback"]
                result = {"action": "refutation", "proof": intro({"apply": [premise, {"trivial": True}]})}
        elif model_step == 1:
            assert not lemmas
            result = {"action": "lemma", "proposition": "True ∧ True",
                      "proof": intro({"and": [premise, premise]})}
        elif model_step == 2:
            lemma, = lemmas
            assert lemma["proposition"] == "True ∧ True" and lemma["antecedents"] == ["True"]
            assert user["history"] == [{"action": "lemma", "status": "lemma_verified"}]
            result = {"action": "lemma", "proposition": "(True ∧ True) ∧ True",
                      "proof": intro({"and": [{"apply": [{"ref": lemma["id"]}, premise]}, premise]})}
        elif model_step == 3:
            first, second = lemmas
            assert second["proposition"] == "(True ∧ True) ∧ True"
            result = {"action": "proof", "proof": {"intro": {"count": 2, "body": {"use": 1}}}}
        else:
            first, second = lemmas
            assert second["proposition"] == "(True ∧ True) ∧ True"
            assert len(user["history"]) == 3
            rejected = user["history"][-1]
            assert rejected["status"] == "rejected" and "native checker rejected" in rejected["feedback"]
            result = {"action": "proof",
                       "proof": {"intro": {"count": 2, "body": {
                              "and": [{"apply": [{"ref": second["id"]}, premise]}, premise]}}}}
        return AdapterReply(text=json.dumps(result), model_id=request["model_id"])


def factory(config):
    return Adapter()
"""


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanSearchTests(unittest.TestCase):
    def setUp(self):
        self.case = final_fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli
        fixture = self.case.fixture
        (fixture.output / "lean_search_adapter.py").write_text(ADAPTER, encoding="utf-8")
        fixture.env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(fixture.output), fixture.env.get("PYTHONPATH"))))
        fixture.env.update(RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="fixture-search",
                           RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL="fixed-search-author",
                           RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="fixture-search",
                           RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER="lean_search_adapter:factory")

    def anchor(self, consequent, *, sensitivity="public"):
        key = uuid4().hex
        return self.case.anchor(f"Bounded search {key}", consequent, [{
            "kind": "hypothesis", "natural": f"Truth holds in this reviewed search fixture {key}.",
            "formal": {"lean": {"proposition": "True"}},
        }], sensitivity=sensitivity, statement_text=f"{consequent} (search fixture {key})")

    def search(self, statement_id, rounds):
        return self.cli("lean", "search", statement_id, "--confirm-public", "--rounds", str(rounds),
                        "--out", self.case.fixture.output)

    def test_model_continues_from_persisted_checked_lemma_ids_without_a_human_login(self):
        statement, scope, _ = self.anchor("∀ _n : Nat, ((True ∧ True) ∧ True) ∧ True")
        command = ("lean", "search", statement["id"])
        self.assertIn("--confirm-public", self.cli(*command, expect=2))
        role = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            partial = self.search(statement["id"], 2)
            self.assertEqual(partial["status"], "budget_exhausted")
            first, second = partial["rounds"]
            self.assertEqual([row["status"] for row in (first, second)], ["lemma_verified", "lemma_verified"])
            result = self.search(statement["id"], 4)
            self.assertEqual(result["status"], "awaiting_evidence_review")
            self.assertEqual(result["rounds"][:2], partial["rounds"])
            self.assertEqual(result["rounds"][2]["status"], "rejected")
            self.assertNotIn("evidence_id", result["rounds"][2])
            self.assertEqual(result["target"], "proof")
            l1, l2 = self.cli("lean", "local-theorems", statement["id"])
            self.assertEqual(l1["id"], first["lemma_id"])
            self.assertEqual(l2["body"]["local_lemma_ids"], [l1["id"]])
            self.assertEqual(l2["id"], second["lemma_id"])
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = role
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(receipt["key_steps"]["local_lemma_ids"], [l2["id"]])
        self.assertEqual(receipt["claim_status"], "open")
        self.assertEqual(receipt["verification"]["review_status"], "pending")
        self.assertEqual(receipt["verification"]["lean_search_traces"], [{
            "trace_sha256": result["record"]["sha256"],
            "proposal_attempt_ids": [step["model_attempt_id"] for step in result["rounds"]],
        }])
        disclosure = self.cli("review", "disclosure", result["evidence_id"])
        self.assertEqual(disclosure["ai_use"]["model_attempts_by_role"], {"prove": 4})
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            attempts = store.query("verifier", """
                SELECT a.finding, a.model_provider FROM rm.attempts a
                JOIN rm.jobs j ON j.id = a.job_id
                WHERE j.claim_id = %s AND j.payload -> 'lean_search' = 'true'::jsonb
            """, scope["claim_id"])
        self.assertEqual(attempts, [{"finding": "numeric_observation", "model_provider": "fixture-search"}] * 4)

    def test_an_extra_hypothesis_is_rejected_and_exact_refutation_only_enters_review(self):
        statement, scope, _ = self.anchor("False")
        partial = self.search(statement["id"], 1)
        self.assertEqual(partial["status"], "budget_exhausted")
        self.assertEqual(partial["rounds"][0]["status"], "proposal_failed")
        self.assertEqual(self.cli("review", "inbox", "--claim", scope["claim_id"]), [])
        result = self.search(statement["id"], 2)
        self.assertEqual(result["status"], "awaiting_evidence_review")
        self.assertEqual(result["target"], "refutation")
        self.assertEqual(result["rounds"][0], partial["rounds"][0])
        evidence = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(evidence["claim_status"], "open")
        self.assertEqual(evidence["refutation"]["mode"], "logical_negation")
        self.assertEqual(evidence["verification"]["review_status"], "pending")
        self.assertIsNone(evidence["refutation"]["witness"])

    def test_new_route_reuses_only_current_same_anchor_lemmas(self):
        statement, _, _ = self.anchor("∀ _n : Nat, ((True ∧ True) ∧ True) ∧ True")
        partial = self.search(statement["id"], 2)
        self.assertEqual(partial["status"], "budget_exhausted")
        first, second = [step["lemma_id"] for step in partial["rounds"]]

        other, _, _ = self.anchor("∀ _n : Nat, ((True ∧ True) ∧ True) ∧ True")
        self.assertEqual(self.search(other["id"], 1)["rounds"][0]["status"], "lemma_verified")
        self.case.fixture.env["RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL"] = "fixed-search-continue"

        result = self.search(statement["id"], 1)
        self.assertEqual(result["status"], "awaiting_evidence_review")
        self.assertNotIn(result["rounds"][0]["model_attempt_id"],
                         [step["model_attempt_id"] for step in partial["rounds"]])
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(receipt["key_steps"]["local_lemma_ids"], [second])
        self.assertCountEqual(receipt["key_steps"]["expanded_local_lemma_ids"], [first, second])
        disclosure = self.cli("review", "disclosure", result["evidence_id"])
        self.assertEqual(disclosure["ai_use"]["model_attempts_by_role"], {"prove": 3})
        self.assertCountEqual([row["id"] for row in disclosure["provenance"]["attempts"] if row["model_used"]],
                              [step["model_attempt_id"] for step in [*partial["rounds"], *result["rounds"]]])
        self.assertEqual(self.search(statement["id"], 1), result)

        archived_first = next(row for row in self.cli("lean", "local-theorems", statement["id"]) if row["id"] == first)
        self.cli("research", "retire-input", "source", archived_first["body"]["candidate"]["sha256"],
                 "--reason", "Retract the first lemma's proof artifact.")
        self.assertTrue(self.cli("review", "packet", result["evidence_id"])["verification"]["needs_recheck"])
        self.assertEqual(self.search(statement["id"], 1)["status"], "blocked")

    def test_restricted_scope_has_no_model_route(self):
        statement, scope, _ = self.anchor("True ∧ True", sensitivity="restricted")
        self.assertIn("requires a public scope", self.cli("lean", "search", statement["id"], "--confirm-public",
                                                         "--out", self.case.fixture.output, expect=2))
        self.assertEqual(self.cli("research", "tasks", scope["claim_id"])["tasks"], [])

    def test_last_search_round_closes_ready_reduction_at_model_cap(self):
        statement, scope, _ = self.anchor("∀ _n : Nat, (True ∧ True) ∧ True")
        reduction = self.case.document("last-round-reduction", {
            "format_version": 1, "obligations": ["∀ _n : Nat, True ∧ True"],
            "proof": final_fixtures.intro(
                3, {"and": [{"apply": [{"use": 1}, {"use": 2}]}, {"use": 0}]}
            ),
        })
        command = ("lean", "reductions", statement["id"])
        pending, = self.cli(*command, "--register", reduction)["reductions"]
        self.assertEqual(pending["status"], "awaiting_obligations")
        self.cli("lean", "budget", statement["id"], "--limit", 2, "--actor", "researcher",
                 "--reason", "Limit this test to two model attempts.")
        self.case.fixture.env["RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL"] = "fixed-search-reduction"

        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            result = self.search(statement["id"], 2)
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human
        self.assertEqual(result["status"], "awaiting_evidence_review")
        self.assertEqual(result["target"], "proof")
        self.assertEqual(result["reduction_id"], pending["implication_id"])
        rejected, verified = result["rounds"]
        self.assertEqual((rejected["status"], verified["status"]), ("rejected", "lemma_verified"))
        self.assertNotEqual(result["verification_attempt_id"], verified["verification_attempt_id"])

        checked, = self.cli(*command)["reductions"]
        self.assertEqual(checked["status"], "goal_checked")
        self.assertEqual(checked["evidence_id"], result["evidence_id"])
        budget = self.cli("lean", "budget", statement["id"])
        self.assertEqual((budget["reserved_calls"], budget["remaining_calls"]), (2, 0))
        inbox = self.cli("review", "inbox", "--claim", scope["claim_id"])
        self.assertEqual([item["item_id"] for item in inbox], [result["evidence_id"]])
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(receipt["claim_status"], "open")
        self.assertEqual(receipt["verification"]["review_status"], "pending")
        self.assertEqual(receipt["key_steps"]["statement_type"], statement["body"]["theorem_type"])
        self.assertCountEqual(receipt["key_steps"]["local_lemma_ids"],
                              [pending["implication_id"], verified["lemma_id"]])
        self.assertEqual(receipt["verification"]["lean_search_traces"], [{
            "trace_sha256": result["record"]["sha256"],
            "proposal_attempt_ids": [rejected["model_attempt_id"], verified["model_attempt_id"]],
        }])
        disclosure = self.cli("review", "disclosure", result["evidence_id"])
        self.assertEqual(disclosure["ai_use"]["model_attempts_by_role"], {"prove": 2})

        # A later search sees the existing reduction even when it could afford another call.
        self.cli("lean", "budget", statement["id"], "--limit", 3, "--actor", "researcher",
                 "--reason", "Allow an additional attempt to detect unintended dispatch.")
        rerun = self.search(statement["id"], 2)
        self.assertEqual(rerun["status"], "awaiting_evidence_review")
        self.assertEqual(rerun["evidence_id"], result["evidence_id"])
        self.assertEqual(rerun["rounds"], [])
        self.assertTrue(rerun["reused_evidence"])

        # Simulate observing readiness just before a competing transaction checked it.
        callback = Mock()
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            competing = lean_reductions.close_ready(
                store, statement, routes=[{**checked, "status": "ready"}], output=self.case.fixture.output,
                reuse_current=True, on_receipt=callback,
            )
        self.assertEqual(competing["evidence_id"], result["evidence_id"])
        self.assertTrue(competing["reused_evidence"])
        callback.assert_not_called()

        self.cli("review-evidence", result["evidence_id"], "approve", "--actor", "researcher",
                 "--reason", "Approve the exact proof without applying its conclusion yet.")
        self.assertEqual(self.search(statement["id"], 2), rerun)
        budget = self.cli("lean", "budget", statement["id"])
        self.assertEqual((budget["reserved_calls"], budget["remaining_calls"]), (2, 1))
        reviewed = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(reviewed["verification"]["lean_search_traces"], receipt["verification"]["lean_search_traces"])

    def test_full_review_budget_prevents_starting_a_model_job(self):
        statement, scope, _ = self.anchor("True ∧ True")
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            store.query("human", "SELECT * FROM rm.record_research_review_policy(%s,%s,%s,%s,%s)",
                        None, 1, False, "researcher", "Set a one-minute review limit.")
            store.query("human", "SELECT rm.log_human_time(%s,%s,%s)", scope["claim_id"], "review", 1)
            try:
                result = self.cli("lean", "search", statement["id"], "--confirm-public",
                                  "--lean", self.case.fixture.output / "not-an-executable")
                self.assertEqual(result["status"], "awaiting_review_capacity")
                self.assertEqual(result["rounds"], [])
                self.assertEqual(self.cli("research", "tasks", scope["claim_id"])["tasks"], [])
            finally:
                store.query("human", "SELECT * FROM rm.record_research_review_policy(%s,%s,%s,%s,%s)",
                            None, None, False, "researcher", "Restore the default review policy.")


    def test_closed_program_proof_uses_frozen_antecedent_without_model(self):
        statement, scope, _ = self.anchor("(True → True) ∧ ((0 : Nat) = 0)", sensitivity="restricted")
        self.case.fixture.env.pop("RM_AGENT_DSN")
        result = self.cli("lean", "search", statement["id"], "--rounds", "0")
        self.assertEqual(result["status"], "awaiting_evidence_review", result)
        self.assertEqual(result["method"], "program")
        self.assertEqual(result["rounds"], [])
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(receipt["claim_id"], scope["claim_id"])
        self.assertEqual(receipt["key_steps"]["statement_type"], statement["body"]["theorem_type"])
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            jobs = store.query("verifier", "SELECT kind FROM rm.jobs WHERE claim_id = %s", scope["claim_id"])
        self.assertEqual(jobs, [{"kind": "verify"}])

    def test_program_discharges_two_open_obligations_and_verifies_the_original_goal(self):
        statement, scope, _ = self.anchor("(True ∧ ((17 : Nat) = 17)) ∧ ((19 : Nat) = 19)")
        reduction = self.case.document("program-reduction", {
            "format_version": 1, "obligations": ["True ∧ ((17 : Nat) = 17)", "(19 : Nat) = 19"],
            "proof": final_fixtures.intro(3, {"and": [{"use": 1}, {"use": 2}]}),
        })
        registered, = self.cli("lean", "reductions", statement["id"], "--register", reduction)["reductions"]
        self.assertEqual(registered["status"], "awaiting_obligations")
        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        self.case.fixture.env.pop("RM_AGENT_DSN")
        try:
            result = self.cli("lean", "search", statement["id"], "--rounds", "0")
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human
        self.assertEqual(result["status"], "awaiting_evidence_review", result)
        self.assertEqual(result.get("reduction_id"), registered["implication_id"], result)
        self.assertEqual([item["status"] for item in result["program_obligations"]], ["lemma_verified"] * 2)
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertEqual(receipt["claim_id"], scope["claim_id"])
        self.assertCountEqual(receipt["key_steps"]["local_lemma_ids"],
                              [registered["implication_id"], *[item["lemma_id"] for item in result["program_obligations"]]])

    def test_model_can_register_distinct_routes_without_adding_assumptions(self):
        statement, scope, _ = self.anchor("(∀ _n : Nat, True) ∨ False")
        self.case.fixture.env["RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL"] = "fixed-search-proposed-routes"
        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            pending = self.search(statement["id"], 1)
            self.assertEqual(pending["status"], "budget_exhausted", pending)
            self.assertEqual(pending["open_routes"][0]["obligations"], [{"proposition": "False", "lemma_id": None}])
            result = self.search(statement["id"], 4)
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human
        self.assertEqual(result["status"], "awaiting_evidence_review", result)
        first, second, blocked, verified = result["rounds"]
        self.assertEqual([item["status"] for item in (first, second, blocked, verified)],
                         ["route_checked", "route_checked", "blocked", "lemma_verified"])
        self.assertNotEqual(first["reduction_id"], second["reduction_id"])
        self.assertEqual(result["reduction_id"], second["reduction_id"])
        review = self.cli("research", "resume", scope["claim_id"])["decisions"]["items"]
        self.assertEqual([(item["kind"], item["reference"]["item_id"]) for item in review],
                         [("review_evidence", result["evidence_id"])])
        receipt = self.cli("review", "packet", result["evidence_id"])
        self.assertCountEqual(receipt["key_steps"]["local_lemma_ids"], [second["reduction_id"], verified["lemma_id"]])

    def test_program_preserves_partial_conjunction_as_checked_route_work(self):
        statement, scope, _ = self.anchor("((0 : Nat) = 0) ∧ (∀ _n : Nat, True)")
        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        self.case.fixture.env.pop("RM_AGENT_DSN")
        try:
            result = self.cli("lean", "search", statement["id"], "--rounds", "0")
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human
        self.assertEqual(result["status"], "proof_search_blocked", result)
        implication, lemma = result["program_obligations"]
        self.assertEqual((implication["status"], lemma["status"]), ("route_checked", "lemma_verified"))
        route, = result["open_routes"]
        self.assertEqual(route["implication_id"], implication["reduction_id"])
        self.assertEqual(route["obligations"], [
            {"proposition": "(0 : Nat) = 0", "lemma_id": lemma["lemma_id"]},
            {"proposition": "∀ _n : Nat, True", "lemma_id": None},
        ])
        self.assertEqual(self.cli("research", "resume", scope["claim_id"])["decisions"]["items"], [])


    def test_monthly_model_allowance_denies_dispatch_then_resumes_after_revision(self):
        statement, scope, _ = self.anchor("∀ _n : Nat, ((True ∧ True) ∧ True) ∧ True")
        self.case.fixture.env["RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL"] = "fixed-search-budget"
        signature = ("--actor", "researcher", "--reason", "Bound this search fixture.")
        self.cli("research", "model-budget", "tariff", "LEAN_AUTHOR", "0.10",
                 "--max-input-bytes", "60000", "--max-output-tokens", "4000", *signature)
        self.cli("research", "model-budget", "limit", "0", *signature)
        try:
            denied = self.search(statement["id"], 2)
            self.assertEqual(denied["status"], "budget_exhausted", denied)
            self.assertEqual(denied["reason"], "project_model_allowance")
            self.assertEqual(self.cli("research", "model-budget", "status")["month"]["reserved_microusd"], 0)
            self.assertEqual(self.search(statement["id"], 2), denied)
            self.cli("research", "model-budget", "limit", "0.10", *signature)
            resumed = self.search(statement["id"], 1)
            self.assertEqual(resumed["rounds"][0]["status"], "lemma_verified", resumed)
            self.assertEqual(self.cli("research", "model-budget", "status")["month"]["reserved_microusd"], 100_000)
            self.cli("research", "model-budget", "limit", "0.20", *signature)
            self.assertEqual(self.search(statement["id"], 1), resumed)
            self.assertEqual(self.cli("research", "model-budget", "status")["month"]["reserved_microusd"], 100_000)
            self.assertEqual(self.cli("research", "resume", scope["claim_id"])["decisions"]["items"], [])
        finally:
            self.cli("research", "model-budget", "limit", "--unlimited", *signature)
