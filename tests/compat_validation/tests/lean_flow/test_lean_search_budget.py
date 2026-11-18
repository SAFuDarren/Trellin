"""Exercise the shared call limit through the real task queue and public CLI."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_final_local_lemmas as final_fixtures
import test_lean_flow as lean_fixtures

from research_multiplier.policy import lean_budget
from research_multiplier.workflows import runtime
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.model_adapters import ModelRun
from research_multiplier.models.model_errors import ModelFailure
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanSearchBudgetTests(unittest.TestCase):
    def setUp(self):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.case = case
        self.fixture = case.fixture
        patcher = patch.dict("os.environ", self.fixture.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        key = uuid4()
        self.statement, self.scope, _ = case.anchor(f"goal budget {key}", "True", [], sensitivity="public",
                                                   statement_text=f"Truth holds (goal budget {key}).")
        self.cli = case.cli
        self.artifacts = Artifacts(self.fixture.output / "goal-budget-objects", workspace=False)
        with Store() as store:
            frozen = store.frozen(self.scope["claim_id"])
        self.subject = {"claim_id": str(self.scope["claim_id"]), "contract_sha256": frozen["contract_sha256"]}
        self.contract = self.artifacts.put(frozen["contract_bytes"], raw=True)

    def budget(self, limit=None, *, history=False):
        args = ["lean", "budget", self.statement["id"]]
        if history:
            args += ["--history"]
        if limit is not None:
            args += ["--limit", str(limit), "--actor", "Researcher", "--reason", "Set the allowance under test."]
        return self.cli(*args)

    def payload(self, route):
        return {"lean_author": True, "lean_search": True, "search_route": route, "round": 1,
                "formalization_id": self.statement["packet_id"],
                "formalization_sha256": self.statement["body"]["scope"]["principal_packet_sha256"],
                "author_statement_id": self.statement["id"],
                "author_statement_sha256": self.statement["statement_sha256"]}

    def test_racing_distinct_routes_get_one_slot_and_lost_usage_stays_unknown(self):
        self.assertEqual(self.budget()["call_limit"], 8)
        self.assertEqual(self.budget(1)["remaining_calls"], 1)
        race = Barrier(2)

        def reserve(route):
            with Store() as store:
                job = runtime.schedule(store, self.subject, "prove", self.payload(route), 30, route)
                race.wait(timeout=10)
                try:
                    row, = lean_budget.claim(store, route, 30, job["id"], retry=False)
                except lean_budget.Exhausted:
                    return job["id"], 0
                return job["id"], row["lease_generation"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            observations = list(executor.map(reserve, ("first-route", "second-route")))
        self.assertCountEqual([generation for _, generation in observations], [0, 1])
        reserved, = [job_id for job_id, generation in observations if generation]
        denied, = [job_id for job_id, generation in observations if not generation]
        with Store() as store:
            rows = store.query("scheduler", "SELECT status, lease_generation FROM rm.jobs WHERE id = %s", denied)
            self.assertEqual(rows, [{"status": "queued", "lease_generation": 0}])
            with self.assertRaisesRegex(ValueError, "RMB01"):
                store.query("scheduler", "SELECT * FROM rm.claim_job('prove','direct',30,%s)", denied)
            with self.assertRaisesRegex(ValueError, "permission denied"):
                store.query("scheduler", "SELECT rm.set_lean_search_call_limit(%s,2,'worker','Not authorized')",
                            self.statement["id"])
            store.query("human", "SELECT rm.cancel_research_job(%s, 'Researcher', 'Simulate a lost response')", reserved)
        report = self.budget(history=True)
        self.assertEqual((report["reserved_calls"], report["remaining_calls"]), (1, 0))
        self.assertEqual(report["outcomes"]["no_report"], 1)
        self.assertEqual(report["input_tokens"], {"reported_total": None, "reported_slots": 0, "unknown_slots": 1})
        self.assertEqual(report["calls"][0]["job_id"], str(reserved))
        self.assertIsNone(report["calls"][0]["attempt_id"])
        self.assertEqual(report["policy_changes"][0]["previous_limit"], 8)

    def test_old_attempts_and_replays_remain_visible_after_retry(self):
        self.budget(1)
        executions = []

        def fail():
            executions.append("failed")
            raise ModelFailure("timeout", "No provider usage returned.", retryable=True)

        def respond():
            executions.append("responded")
            result = {"action": "blocked"}
            metadata = {"provider": "budget-fixture", "id": "budget-fixture", "family": "budget-fixture",
                        "parameters": {}, "prompt_sha256": "a" * 64,
                        "input_tokens": 12, "output_tokens": 0, "cost_usd": "0.000123"}
            return "numeric_observation", result, ModelRun(result, metadata, {})

        def run(action, *, retry=False):
            with Store() as store:
                return runtime.execute(store, self.artifacts, self.subject, self.contract, "prove", action,
                                       payload=self.payload("retry"), operation_key="retry-route", retry=retry,
                                       budgeted=True)

        failure = run(fail)
        with self.assertRaises(lean_budget.Exhausted):
            run(respond, retry=True)
        self.assertEqual(run(respond)["id"], failure["id"])
        self.assertEqual(executions, ["failed"])
        self.budget(2)
        successful = run(respond, retry=True)
        self.assertEqual(run(fail)["id"], successful["id"])
        self.assertEqual(executions, ["failed", "responded"])
        report = self.budget(history=True)
        self.assertEqual((report["reserved_calls"], report["remaining_calls"]), (2, 0))
        self.assertEqual([row["status"] for row in report["calls"]], ["failed", "responded"])
        self.assertEqual(report["calls"][0]["failure_code"], "timeout")
        self.assertEqual([row["attempt_id"] for row in report["calls"]], [failure["id"], successful["id"]])
        self.assertEqual(report["input_tokens"], {"reported_total": 12, "reported_slots": 1, "unknown_slots": 1})
        self.assertEqual(report["output_tokens"], {"reported_total": 0, "reported_slots": 1, "unknown_slots": 1})
        self.assertEqual(report["cost_usd"], {"reported_total": "0.000123", "reported_slots": 1, "unknown_slots": 1})
