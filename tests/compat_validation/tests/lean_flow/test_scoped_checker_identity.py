"""Use native Lean receipts to exercise revision attribution and containment."""

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier import final_lean_local
from research_multiplier.workflows import runtime
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ScopedCheckerIdentityTests(unittest.TestCase):
    def test_incidents_and_holds_target_the_scoped_final_checker(self):
        import psycopg

        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        choice = {"kind": "hypothesis", "natural": "Use reflexivity at five to track the checker revision.",
                  "formal": {"lean": {"proposition": "5 = 5"}}}
        anchor, _, _ = case.anchor("scoped-checker-incidents", "5 = 5 ∧ 5 = 5", [choice])
        plan = case.document("one-lemma", {"format_version": 1, "nodes": [
            {"name": "L1", "proposition": "5 = 5", "proof": fixtures.intro(1, {"use": 0})},
        ]})
        lemma, = case.cli("lean", "derive", anchor["id"], plan)["lemmas"]
        premise = {"use": 0}
        derived = fixtures.apply({"ref": lemma["id"]}, premise)

        def proof(left, right):
            return {"format_version": 1, "proof": fixtures.intro(1, {"and": [left, right]})}

        check = ("lean", "check-theory", anchor["id"])
        start = datetime.now(timezone.utc)
        scoped = case.cli(*check, case.document("scoped", proof(derived, premise)), "--out", case.fixture.output)
        ordinary = case.cli(*check, case.document("ordinary", proof(premise, premise)), "--out", case.fixture.output)
        scoped_id, ordinary_id = scoped["evidence_id"], ordinary["evidence_id"]
        scoped_packet = case.cli("review", "packet", scoped_id)["verification"]
        local = scoped["local_checker_revision"]
        base = scoped_packet["checker_revision"]
        self.assertNotEqual(local, base)
        self.assertEqual(scoped_packet["effective_checker_revision"], local)
        self.assertEqual(case.cli("review", "packet", ordinary_id)["verification"]["effective_checker_revision"], base)

        with patch.dict(os.environ, case.fixture.env), Store() as store:
            job, = store.query("verifier", """
                SELECT j.* FROM rm.jobs j JOIN rm.attempts a ON a.job_id = j.id WHERE a.id = %s
            """, scoped["attempt_id"])
            subject = {"claim_id": str(job["claim_id"]), "contract_sha256": job["contract_sha256"]}
            queued = runtime.schedule(store, subject, "verify", job["payload"], 120, "scoped-held-lease", model_calls=0)

        file = case.document("incident", {"reproduction": "Synthetic incident for the checker identity regression."})
        report = ("trust", "report", file, "--kind", "verifier_vulnerability", "--summary", "Check the displayed revision.")
        by_job = case.cli(*report, "--job", job["id"])
        self.assertEqual(by_job["checker_revision"], local)
        self.assertEqual(case.cli(*report, "--job", job["id"], "--checker", local)["id"], by_job["id"])
        self.assertEqual(case.cli(*report, "--checker", local)["checker_revision"], local)
        self.assertEqual(case.cli(*report, "--job", job["id"], "--checker", base)["checker_revision"], base)
        self.assertIn("differs", case.cli(*report, "--job", job["id"], "--checker",
                                        lemma["body"]["checker_revision"], expect=2))
        end = datetime.now(timezone.utc)
        window = case.cli("trust", "scan", "--from", start.isoformat(), "--to", end.isoformat())
        observations = {row["checker_revision"]: row for row in window["observations"]}
        for revision, incident_count in ((local, 2), (base, 1)):
            row = observations[revision]
            self.assertEqual((row["attempts"], row["verified_receipts"], row["open_reports"]), (1, 1, incident_count))

        original_query = Store.query
        held_registration = []

        def pause_after_check(store, role, query, *args):
            if query.startswith("SELECT id FROM rm.record_verified_lean("):
                held_registration.extend(args)
                raise ValueError("Pause after the native checker completed.")
            return original_query(store, role, query, *args)

        with patch.dict(os.environ, case.fixture.env), patch.object(Store, "query", pause_after_check):
            with self.assertRaisesRegex(ValueError, "Pause after the native checker"):
                final_lean_local.check(anchor["id"], proof(premise, derived), case.fixture.output)

        policy = case.cli("trust", "policy", "--min-attempts", 5, "--jump-percent", 25,
                          "--actor", "researcher", "--reason", "Test scoped containment boundaries.")
        # The scan above checks live native statistics. Seed a hold independently
        # to exercise all four fences without introducing fake verification attempts.
        with psycopg.connect(database.DSN) as db:
            hold, = db.execute("""
                INSERT INTO rm.checker_holds (policy_id, checker_revision, window_start, window_end,
                    attempts, successful_checks, prior_attempts, prior_successful_checks)
                VALUES (%s,%s,%s,%s,5,5,5,0) RETURNING id
            """, (policy["id"], local, start - timedelta(hours=1), end)).fetchone()

        with patch.dict(os.environ, case.fixture.env), Store() as store:
            with self.assertRaisesRegex(ValueError, "monitoring alert"):
                runtime.schedule(store, subject, "verify", job["payload"], 120, "scoped-held-queue", model_calls=0)
            with self.assertRaisesRegex(ValueError, "monitoring alert"):
                store.query("scheduler", "SELECT * FROM rm.claim_job('verify',%s,120,%s)", "probe", queued["id"])
            with self.assertRaisesRegex(ValueError, "monitoring alert"):
                store.query("verifier", "SELECT id FROM rm.record_verified_lean(%s,%s,%s)", *held_registration)
        case.cli("review-evidence", scoped_id, "approve", "--actor", "researcher", "--reason", "Read the scoped proof.")
        self.assertIn("monitoring alert", case.cli("promote-evidence", scoped_id, "--reason", "Apply review.", expect=2))
        case.cli("trust", "release", hold, "--actor", "researcher", "--reason", "Review the synthetic hold.")
        with patch.dict(os.environ, case.fixture.env), Store() as store:
            delayed, = store.query("verifier", "SELECT id FROM rm.record_verified_lean(%s,%s,%s)", *held_registration)
        case.cli("trust", "decide", by_job["id"], "contain", "--actor", "researcher", "--reason", "Retire only the local checker.")
        for receipt in (scoped_id, delayed["id"]):
            self.assertTrue(case.cli("review", "packet", receipt)["verification"]["needs_recheck"])
        self.assertFalse(case.cli("review", "packet", ordinary_id)["verification"]["needs_recheck"])
        case.cli("review-evidence", ordinary_id, "approve", "--actor", "researcher", "--reason", "Read the ordinary proof.")
        case.cli("promote-evidence", ordinary_id, "--reason", "Apply the unaffected review.")
