"""Exercise persisted model-dispatch recovery with native Lean and PostgreSQL."""

import os
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_final_local_lemmas as final_fixtures
import test_lean_flow as lean_fixtures

from research_multiplier.workflows import lean_search, research_progress, runtime
from research_multiplier.storage.postgres import DatabaseOperationError, Store


ADAPTER = """\
import json
import os
from pathlib import Path

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


class Adapter:
    capabilities = ModelCapabilities(output_token_limit_enforced=True)

    def complete(self, request):
        with Path(os.environ["RM_DISPATCH_TEST_LOG"]).open("a", encoding="utf-8") as log:
            log.write("dispatched\\n")
        if request["model_id"] == "dispatch-timeout":
            raise TimeoutError
        result = {"action": "proof", "proof": {"intro": {"count": 1, "body": {"rfl": True}}}}
        return AdapterReply(text=json.dumps(result), model_id=request["model_id"])


def factory(_):
    return Adapter()
"""


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanDispatchRecoveryTests(unittest.TestCase):
    def prepare(self, model):
        case = final_fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        anchor, scope, _ = case.anchor(f"Recover a checked model operation {uuid4()}",
                                      "∀ f : Nat → Nat, f 29 = f 29", [], sensitivity="public")
        fixture = case.fixture
        log = fixture.output / "model-dispatches.log"
        (fixture.output / "recovery_test_adapter.py").write_text(ADAPTER, encoding="utf-8")
        fixture.env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(fixture.output), fixture.env.get("PYTHONPATH"))))
        fixture.env.update(
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="recovery-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL=model,
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="recovery-fixture",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER="recovery_test_adapter:factory",
            RM_DISPATCH_TEST_LOG=str(log),
        )
        policy = case.cli("advance", "propose", "--for-packet", anchor["packet_id"], "--lean-model-rounds", 2)
        case.cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"], "--actor", "researcher",
                 "--reason", "Allow exactly two reserved slots for this frozen proof task.")
        return case, anchor, scope, policy["id"], log

    def test_temporary_database_failure_after_lease_resumes_the_same_unsent_job(self):
        case, anchor, scope, policy_id, log = self.prepare("dispatch-success")
        execute = runtime.execute
        injected = []

        def interrupt_gate(store, artifacts, subject, contract, kind, action, **options):
            if options.get("resume_undispatched") and not injected:
                normal_action = action

                def action():
                    injected.append(True)
                    unavailable = DatabaseOperationError("verifier", "57P01", retryable=True)
                    with patch.object(lean_search, "load_model_search_scope", side_effect=unavailable):
                        return normal_action()

            return execute(store, artifacts, subject, contract, kind, action, **options)

        env = {key: value for key, value in case.fixture.env.items() if key != "RM_HUMAN_DSN"}
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "path", [str(case.fixture.output), *sys.path]):
            with patch.object(runtime, "execute", side_effect=interrupt_gate), self.assertRaises(
                runtime.BeforeDispatchUnavailable
            ):
                research_progress.advance_cycle("combined", 1, [policy_id], 1)
        self.assertEqual(injected, [True])
        self.assert_recovers_unsent_job(case, anchor, scope, policy_id, log)

    def test_temporary_database_failure_in_project_reservation_recovers_without_a_duplicate_call(self):
        case, anchor, scope, policy_id, log = self.prepare("dispatch-success")
        query = Store.query

        def interrupt_reservation(store, role, sql, *values):
            if "SELECT rm.reserve_project_model(" in sql:
                raise DatabaseOperationError(role, "57P01", retryable=True)
            return query(store, role, sql, *values)

        env = {key: value for key, value in case.fixture.env.items() if key != "RM_HUMAN_DSN"}
        with (patch.dict(os.environ, env, clear=True),
              patch.object(sys, "path", [str(case.fixture.output), *sys.path]),
              patch.object(Store, "query", interrupt_reservation),
              self.assertRaises(runtime.BeforeDispatchUnavailable)):
            research_progress.advance_cycle("combined", 1, [policy_id], 1)
        self.assert_recovers_unsent_job(case, anchor, scope, policy_id, log)

    def assert_recovers_unsent_job(self, case, anchor, scope, policy_id, log):
        self.assertFalse(log.exists())
        [failure] = case.cli("lean", "budget", anchor["id"], "--history")["calls"]
        self.assertEqual(failure["failure_code"], "database_before_model_dispatch")
        self.assertTrue(failure["retryable"])
        self.assertIsNone(failure["model_provider"])
        self.assertEqual(case.cli("advance", "status", policy_id)["lean_passes"], [])

        work = ("advance", "worker", "--policy", policy_id, "--parallel", 1, "--limit", 1)
        result = case.cli(*work)
        self.assertFalse(result["errors"], result)
        [run] = result["lean_runs"]
        self.assertEqual(run["status"], "awaiting_evidence_review", run)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["dispatched"])
        budget = case.cli("lean", "budget", anchor["id"], "--history")
        self.assertEqual(budget["reserved_calls"], 2)
        self.assertEqual({call["job_id"] for call in budget["calls"]}, {failure["job_id"]})
        self.assertEqual([call["lease_generation"] for call in budget["calls"]], [1, 2])
        self.assertEqual(case.cli("review", "packet", run["evidence_id"])["claim_id"], scope["claim_id"])
        self.assertEqual(case.cli(*work)["lean_runs"], [])
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["dispatched"])

    def expire_final_verification(self, case, policy_id, log, *, last_generation=False):
        import psycopg

        submit = Store.submit
        expired = []

        def expire_before_submit(store, kind, job_id, owner, generation, body, *, metrics=None):
            if kind == "verify" and body["finding"] == "exact_proof" and log.exists() and not expired:
                expired.append((job_id, generation))
                with psycopg.connect(lean_fixtures.DSN) as db:
                    changed = db.execute("""
                        UPDATE rm.jobs
                        SET lease_until = clock_timestamp() - interval '1 second',
                            max_attempts = CASE WHEN %s THEN lease_generation ELSE max_attempts END
                        WHERE id = %s AND lease_generation = %s AND lease_owner = %s
                    """, (last_generation, job_id, generation, owner)).rowcount
                    self.assertEqual(changed, 1)
            return submit(store, kind, job_id, owner, generation, body, metrics=metrics)

        env = {key: value for key, value in case.fixture.env.items() if key != "RM_HUMAN_DSN"}
        with (patch.dict(os.environ, env, clear=True),
              patch.object(sys, "path", [str(case.fixture.output), *sys.path]),
              patch.object(Store, "submit", expire_before_submit),
              self.assertRaisesRegex(runtime.VerificationQueueError, "lease expired") as interrupted):
            research_progress.advance_cycle("combined", 1, [policy_id], 1)
        self.assertTrue(interrupted.exception.retryable)
        self.assertEqual(interrupted.exception.__cause__.state, "23514")
        self.assertEqual(case.cli("advance", "status", policy_id)["lean_passes"], [])
        [(job_id, generation)] = expired
        with psycopg.connect(lean_fixtures.DSN) as db:
            self.assertEqual(db.execute(
                "SELECT lease_generation FROM rm.attempts WHERE job_id = %s", (job_id,)
            ).fetchall(), [])
        return job_id, generation

    def test_expired_verification_recovers_same_candidate_without_another_model_call(self):
        import psycopg

        case, anchor, scope, policy_id, log = self.prepare("dispatch-success")
        job_id, generation = self.expire_final_verification(case, policy_id, log)
        work = ("advance", "worker", "--policy", policy_id, "--parallel", 1, "--limit", 1)
        result = case.cli(*work)
        self.assertFalse(result["errors"], result)
        [run] = result["lean_runs"]
        self.assertEqual(run["status"], "awaiting_evidence_review")
        self.assertEqual(case.cli("review", "packet", run["evidence_id"])["claim_id"], scope["claim_id"])
        with psycopg.connect(lean_fixtures.DSN) as db:
            attempts = db.execute(
                "SELECT lease_generation, finding FROM rm.attempts WHERE job_id = %s", (job_id,)
            ).fetchall()
        self.assertEqual(attempts, [(generation + 1, "exact_proof")])
        self.assertEqual(case.cli(*work)["lean_runs"], [])
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 1)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["dispatched"])

    def test_exhausted_verification_stops_without_advancing_the_model_round(self):
        case, anchor, scope, policy_id, log = self.prepare("dispatch-success")
        self.expire_final_verification(case, policy_id, log, last_generation=True)
        work = ("advance", "worker", "--policy", policy_id, "--parallel", 1, "--limit", 1)
        result = case.cli(*work)
        self.assertFalse(result["errors"], result)
        [run] = result["lean_runs"]
        self.assertEqual(run["status"], "verification_queue_blocked")
        self.assertIn("exhausted its recovery attempts", run["reason"])
        self.assertNotIn("rounds", run)
        resume = case.cli("research", "resume", scope["claim_id"])
        self.assertIn("inspect_verification_queue", str(resume))
        self.assertEqual(case.cli(*work)["lean_runs"], [])
        self.assertEqual(case.cli("lean", "budget", anchor["id"])["reserved_calls"], 1)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["dispatched"])

    def test_attempt_that_may_have_reached_the_provider_is_not_automatically_retried(self):
        case, anchor, _, policy_id, log = self.prepare("dispatch-timeout")
        work = ("advance", "worker", "--policy", policy_id, "--parallel", 1, "--limit", 1)
        result = case.cli(*work)
        [run] = result["lean_runs"]
        self.assertEqual(run["status"], "proposal_failed", result)
        self.assertEqual(case.cli(*work)["lean_runs"], [])
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["dispatched"])
        budget = case.cli("lean", "budget", anchor["id"], "--history")
        self.assertEqual(budget["reserved_calls"], 1)
        [failure] = budget["calls"]
        self.assertEqual(failure["failure_code"], "timeout")
        self.assertTrue(failure["retryable"])
