"""Re-run a recorded native Lean rejection through the workspace CLI."""

from hashlib import sha256
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

import test_lean_flow as lean_fixtures
import test_lean_shadow as shadow_fixtures


@skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanIncidentRegressionTests(TestCase):
    def test_capture_failed_checker_attempt_and_replay_in_separate_workspace(self):
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        setup = shadow_fixtures.LeanShadowTests()
        setup.setUp()
        self.addCleanup(setup.doCleanups)
        case = setup.case
        anchor, scope, _ = case.anchor("Replay a rejected Lean candidate", "True", [])
        proof = case.document("failed-lean-proof", {"format_version": 1, "proof": {"rfl": True}})
        failure = case.cli("lean", "check-theory", anchor["id"], proof, expect=2)
        self.assertIn("RMCandidate.lean:", failure)
        before = case.cli("research", "tasks", scope["claim_id"])["tasks"]
        original, = [task for task in before if task["kind"] == "verify"]
        incident = case.cli("trust", "report", proof, "--kind", "workflow_blocked", "--job", original["id"],
                            "--summary", "The candidate used equality reflexivity for a Truth goal.")
        register = ("trust", "lean-regression", incident["id"], "--actor", "researcher", "--reason",
                    "Check the exact rejected candidate on the reviewed scope.", "--expect")
        saved = case.cli(*register, "rejected")
        wrong_expectation = case.cli(*register, "verified")
        self.assertEqual(case.cli(*register, "rejected")["id"], saved["id"])
        self.assertEqual(saved["case"]["original"]["attempt_id"], original["attempt_id"])
        self.assertEqual(saved["case"]["original"]["inputs"]["statement_sha256"], anchor["statement_sha256"])
        self.assertIn("separate workspace", case.cli("trust", "replay-lean-regression", saved["id"], expect=2))

        archive = setup.backup()
        scratch_dsn = setup.create_database()
        scratch = setup.root / "isolated-regression"
        environment = setup.fixture.env
        with patch.dict(environment, {"RM_MIGRATION_DSN": scratch_dsn}):
            case.cli("research", "restore-project", archive, scratch)
        scratch_settings = {"RM_DATA_ROOT": str(scratch)}
        for role in ("HUMAN", "SCHEDULER", "VERIFIER"):
            key = f"RM_{role}_DSN"
            scratch_settings[key] = make_conninfo(environment[key], dbname=conninfo_to_dict(scratch_dsn)["dbname"])
        replay = ("trust", "replay-lean-regression", saved["id"])

        with patch.dict(environment, scratch_settings):
            candidate_hash = saved["case"]["original"]["inputs"]["submission"]["sha256"]
            candidate = scratch / "objects" / candidate_hash
            original_bytes = candidate.read_bytes()
            candidate.write_bytes(b"changed")
            self.assertIn("artifact checksum failed", case.cli(*replay, expect=2))
            candidate.write_bytes(original_bytes)
            unavailable = case.cli(*replay, "--lean", scratch / "missing-lean", expect=1)
            self.assertEqual(unavailable["status"], "inconclusive")
            self.assertIsNone(unavailable["execution"])
            first = case.cli(*replay)
            second = case.cli(*replay)
            failed = case.cli("trust", "replay-lean-regression", wrong_expectation["id"], expect=1)
            self.assertFalse(case.cli("research", "evidence", scope["claim_id"])["evidence"])

        self.assertEqual(first["status"], "passed")
        self.assertEqual(first["split"], "development")
        self.assertEqual(first["model_calls"], 0)
        self.assertEqual(first["observed"], "rejected")
        self.assertEqual(first["fixture"]["sha256"], saved["artifact_sha256"])
        self.assertNotEqual(first["execution"]["attempt_id"], original["attempt_id"])
        self.assertNotEqual(first["execution"]["attempt_id"], second["execution"]["attempt_id"])
        self.assertEqual(failed["status"], "failed")
        report = Path(scratch, "evaluation", "private", "objects", first["report"]["sha256"])
        self.assertEqual(sha256(report.read_bytes()).hexdigest(), first["report"]["sha256"])
        self.assertEqual(case.cli("research", "tasks", scope["claim_id"])["tasks"], before)
