"""Check that concurrent verification cannot race a decisive verdict."""

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
import unittest
from uuid import uuid4

import test_final_local_lemmas as fixtures
import test_lean_flow as database


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class EvidenceConflictTests(unittest.TestCase):
    def test_promotion_rechecks_conflicts_after_concurrent_issuance(self):
        import psycopg

        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        choice = {"kind": "hypothesis", "natural": "Reflexivity at four for the publication race.",
                  "formal": {"lean": {"proposition": "4 = 4"}}}
        anchor, _, _ = case.anchor("evidence-conflict", "4 = 4 ∧ 4 = 4", [choice])
        document = case.document("proof", {"format_version": 1, "proof": fixtures.intro(1, {
            "and": [{"use": 0}, {"use": 0}],
        })})
        proved = case.cli("lean", "check-theory", anchor["id"], document, "--out", case.fixture.output)
        receipt = proved["evidence_id"]
        case.cli("review-evidence", receipt, "approve", "--actor", "researcher", "--reason", "Review original check.")

        def set_conflict_trigger_mode(mode):
            with psycopg.connect(database.DSN) as admin:
                admin.execute("ALTER TABLE rm.evidence " + mode + " TRIGGER guard_conflicting_receipt")

        # A sound verifier won't issue opposing receipts. Inject this synthetic
        # disagreement through the migration identity, keeping the race guard on.
        set_conflict_trigger_mode("ENABLE ALWAYS")
        self.addCleanup(set_conflict_trigger_mode, "ENABLE")

        def opposing_receipt(issuer):
            issuer.execute("SET LOCAL session_replication_role = replica")
            return issuer.execute("""
                INSERT INTO rm.evidence (claim_id, contract_sha256, attempt_id, kind, verdict, diff_status,
                    artifact_uri, artifact_sha256, verifier_name, verifier_version, code_revision,
                    environment_sha256, result, issuance_sha256)
                SELECT claim_id, contract_sha256, verification_attempt_id, 'counterexample', 'verified', 'exact',
                    artifact_uri, artifact_sha256, verifier_name, verifier_version, code_revision,
                    environment_sha256, result, %s
                FROM rm.evidence WHERE id = %s
                RETURNING id
            """, (uuid4().hex * 2, receipt)).fetchone()[0]

        with psycopg.connect(database.DSN) as issuer, psycopg.connect(case.fixture.env["RM_VERIFIER_DSN"]) as promoter:
            promoter.execute("SET LOCAL lock_timeout = '10s'")
            opposite_id = opposing_receipt(issuer)
            with ThreadPoolExecutor(max_workers=1) as pool:
                promoting = pool.submit(promoter.execute, "SELECT rm.promote_claim(%s,%s)", (receipt, "Apply review."))
                try:
                    timeout = monotonic() + 5
                    while monotonic() < timeout and not promoting.done():
                        blocking, = issuer.execute("SELECT pg_blocking_pids(%s)", (promoter.pgconn.backend_pid,)).fetchone()
                        if issuer.pgconn.backend_pid in blocking:
                            break
                        sleep(0.025)
                    else:
                        self.fail("Promotion did not wait for the conflicting receipt to commit.")
                finally:
                    issuer.commit()
                with self.assertRaisesRegex(psycopg.errors.CheckViolation, "conflicting exact evidence"):
                    promoting.result(timeout=10)
                promoter.rollback()

        case.cli("review-evidence", opposite_id, "return", "--actor", "researcher", "--reason", "Review disagreement.")
        case.cli("promote-evidence", receipt, "--reason", "Apply the remaining current receipt.")
        with psycopg.connect(database.DSN) as issuer:
            with self.assertRaisesRegex(psycopg.errors.CheckViolation, "conflicts with the current decisive evidence"):
                opposing_receipt(issuer)
            issuer.rollback()
        self.assertEqual(case.cli("review", "packet", receipt)["claim_status"], "proved")
