"""Exercise durable evaluation triggers and an isolated zero-model replay."""

from hashlib import sha256
import os
from unittest import TestCase, skipUnless
from unittest.mock import patch

import test_lean_flow as lean_fixtures
import test_lean_shadow as shadow_fixtures
from research_multiplier import evaluation
from research_multiplier.evaluations import lean_rechecks, lean_shadow
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store


@skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanRecheckTests(TestCase):
    def test_scan_deduplicates_and_restored_worker_recovers_its_attempt(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        setup = shadow_fixtures.LeanShadowTests()
        setup.setUp()
        self.addCleanup(setup.doCleanups)
        case = setup.case
        anchor, scope, _ = case.anchor("Monitor an exact rejected Lean candidate", "True", [])
        candidate = case.document("failed-recheck-candidate", {"format_version": 1, "proof": {"rfl": True}})
        self.assertIn("RMCandidate.lean", case.cli("lean", "check-theory", anchor["id"], candidate, expect=2))
        before = case.cli("research", "tasks", scope["claim_id"])["tasks"]
        [verify] = [job for job in before if job["kind"] == "verify"]
        incident = case.cli("trust", "report", candidate, "--kind", "workflow_blocked", "--job", verify["id"],
                            "--summary", "This checked workflow submitted reflexivity for a Truth goal.")
        captured = case.cli("trust", "lean-regression", incident["id"], "--actor", "researcher",
                            "--reason", "Retain the rejected candidate.", "--expect", "rejected")
        scan = case.cli("trust", "lean-recheck-scan")
        signals = {item["kind"]: item for item in scan["new"]}
        self.assertEqual(set(signals), {"development_replay", "workflow_blocked", "monthly_review", "implementation_changed"})
        self.assertEqual(signals["workflow_blocked"]["detail"]["incident_id"], incident["id"])
        self.assertEqual(signals["development_replay"]["regression_id"], captured["id"])
        self.assertEqual(case.cli("trust", "lean-recheck-scan")["new"], [])
        policy = case.cli("advance", "propose", "--for-packet", anchor["packet_id"], "--lean-model-rounds", 0)
        pressure = """
            INSERT INTO rm.lean_progress_runs(policy_id, inputs_sha256, result)
            VALUES (%s,%s,'{"status":"budget_exhausted"}'::jsonb)
        """
        # Feed the real status ledger with controlled observations around the threshold.
        with psycopg.connect(setup.source_dsn) as database:
            for number in (1, 2):
                database.execute(pressure, (policy["id"], str(number) * 64))
        self.assertEqual(case.cli("trust", "lean-recheck-scan")["new"], [])
        with psycopg.connect(setup.source_dsn) as database:
            database.execute(pressure, (policy["id"], "3" * 64))
        [budget] = case.cli("trust", "lean-recheck-scan")["new"]
        self.assertEqual(budget["kind"], "budget_pressure")
        self.assertEqual(budget["detail"]["budget_exhausted_passes"], 3)
        self.assertEqual(case.cli("trust", "lean-recheck-work")["needs_restore"], 1)
        self.assertEqual(case.cli("research", "tasks", scope["claim_id"])["tasks"], before)

        archive = setup.backup()
        scratch_dsn = setup.create_database()
        scratch = setup.root / "restored-rechecks"
        environment = setup.fixture.env
        with patch.dict(environment, {"RM_MIGRATION_DSN": scratch_dsn}):
            case.cli("research", "restore-project", archive, scratch)
        settings = {"RM_DATA_ROOT": str(scratch)}
        for role in ("HUMAN", "SCHEDULER", "VERIFIER"):
            key = f"RM_{role}_DSN"
            settings[key] = make_conninfo(environment[key], dbname=conninfo_to_dict(scratch_dsn)["dbname"])

        with patch.dict(environment, settings), patch.dict(os.environ, settings), paths.use_workspace(scratch, strict=True):
            self.assertEqual(case.cli("trust", "lean-recheck-scan")["new"], [])
            with Store() as store:
                def held():
                    selected = store.query("verifier", "SELECT * FROM rm.claim_lean_recheck(%s,NULL)", scan["implementation"])
                    self.assertEqual(len(selected), 1)
                    concurrent = case.cli("trust", "lean-recheck-work")
                    self.assertEqual(concurrent["runs"], [])
                store.transaction("verifier", held)
            unavailable = case.cli("trust", "lean-recheck-work", "--lean", scratch / "missing-lean", expect=1)
            [first] = unavailable["runs"]
            self.assertEqual(first["status"], "inconclusive")
            self.assertIsNone(first["verification_attempt_id"])
            self.assertEqual(case.cli("trust", "lean-recheck-work")["runs"], [])

            finish = Store.query
            def interrupted(store, role, sql, *values):
                if "rm.finish_lean_recheck(" in sql:
                    raise RuntimeError("test process lost just before recording the recheck outcome")
                return finish(store, role, sql, *values)

            with patch.object(Store, "query", interrupted):
                with self.assertRaisesRegex(Exception, "test process lost|database operation failed"):
                    lean_rechecks.work(1, retry=True)
            operation = (f"v0a-2:lean-regression:{signals['development_replay']['id']}:2:")
            with Store() as store:
                [prior] = store.query("verifier", """
                    SELECT a.id FROM rm.jobs j JOIN rm.attempts a ON a.job_id = j.id
                    WHERE starts_with(j.operation_key, %s)
                """, operation)
            [outcome] = case.cli("trust", "lean-recheck-work", "--retry-inconclusive")["runs"]
            self.assertEqual(outcome["status"], "passed")
            self.assertEqual(outcome["generation"], 2)
            self.assertEqual(outcome["verification_attempt_id"], str(prior["id"]))
            self.assertEqual(outcome["model_calls"], 0)
            self.assertEqual(case.cli("trust", "lean-recheck-work", "--retry-inconclusive")["runs"], [])
            current = {item["id"]: item for item in case.cli("trust", "lean-recheck-status")["items"]}
            self.assertEqual(current[signals["development_replay"]["id"]]["status"], "passed")
            receipt = scratch / "evaluation" / "private" / "objects" / outcome["report_sha256"]
            self.assertEqual(sha256(receipt.read_bytes()).hexdigest(), outcome["report_sha256"])
            self.assertFalse(case.cli("research", "evidence", scope["claim_id"])["evidence"])

        self.assertEqual(case.cli("research", "tasks", scope["claim_id"])["tasks"], before)

    def test_complete_sealed_benchmarks_from_distinct_candidates_trigger_once(self):
        setup = shadow_fixtures.LeanShadowTests()
        setup.setUp()
        self.addCleanup(setup.doCleanups)
        case = setup.case
        development, _, _ = case.anchor("Benchmark development case", "True", [], sensitivity="public")
        holdout, _, _ = case.anchor("Benchmark holdout case", "False", [], sensitivity="public")
        corpus_input = case.document("benchmark-corpus", {"format_version": 1,
            "development": [{"statement_id": development["id"], "expected": "verified"}],
            "holdout": [{"statement_id": holdout["id"], "expected": "refuted"}]})
        sealed = case.cli("eval", "lean-corpus-seal", corpus_input, "--actor", "researcher",
                          "--reason", "Freeze comparable scope before any experimental proof.")
        corpus_hash = sealed["artifact"]["sha256"]
        workspace = setup.fixture.workspace
        with paths.use_workspace(workspace, strict=True):
            archive = evaluation.archive()
            [step] = archive.read_json(sealed["artifact"])["development"]
            outcome = {"result": {"search_status": "proved", "outcome": "verified"}}
            body = {"kind": lean_shadow.CAMPAIGN_KIND, "split": "development", "source_unchanged": True,
                    "source": lean_shadow._identity(workspace), "evaluation_corpus_sha256": corpus_hash,
                    "manifest_sha256": "a" * 64, "starting_corpus": {"test": "same"}, "budget": {"rounds": 0},
                    "cases": [{"steps": [{**step, "scope": {"scope_sha256": step["scope_sha256"]},
                                          "sides": {"baseline": outcome, "candidate": outcome}}]}]}
            def report(candidate, **updates):
                return archive.put({**body, "comparison": {"baseline": {"model": "base"},
                    "candidate": {"model": candidate}}, **updates})["sha256"]

            first = report("candidate-a")
            second = report("candidate-b")
            held_out = report("candidate-c", split="holdout")
        with self.subTest("reject holdout observations"):
            error = case.cli("trust", "lean-recheck-scan", "--campaign", held_out, expect=2)
            self.assertIn("sealed development corpus", error)
        case.cli("trust", "lean-recheck-scan", "--campaign", first)
        self.assertEqual(case.cli("trust", "lean-recheck-scan", "--campaign", first)["new"], [])
        [signal] = case.cli("trust", "lean-recheck-scan", "--campaign", second)["new"]
        self.assertEqual(signal["kind"], "benchmark_plateau")
        self.assertEqual(signal["detail"]["previous_report_sha256"], first)
        self.assertEqual(signal["detail"]["report_sha256"], second)
        self.assertEqual(signal["detail"]["exact"], 1)
        self.assertEqual(case.cli("trust", "lean-recheck-scan", "--campaign", second)["new"], [])
