"""Check the Lean statement approval and verifier boundary on PostgreSQL."""

from contextlib import contextmanager
import os
import unittest

from research_multiplier.domain_packages import flow_alignment
from research_multiplier import flow_claim


DSN = os.environ.get("RM_TEST_DSN")
NETWORK = {"vertices": ["s", "t"], "source": "s", "sink": "t", "edges": [["s", "t", 7]], "target_value": 7}


def artifact(byte):
    digest = byte * 64
    return {"sha256": digest, "uri": f"urn:sha256:{digest}"}


STATEMENT = {
    "theorem": "RMFlow.Expected.realGoal",
    "theorem_type": "(∀ f : Real, Feasible f → value f ≤ 7) ∧ ∃ f : Real, Feasible f ∧ value f = 7",
    "scope": {"flow_type": "real", "network": NETWORK},
    "trusted_source": artifact("a"),
    "environment": {"toolchain": "leanprover/lean4:v4.19.0", "sha256": "b" * 64},
}
CHECK = {
    "compiled": True, "kernel_rechecked": True, "scope_matches": True, "accepted": True,
    "theorem": STATEMENT["theorem"], "candidate_theorem": "RMFlow.RealProof.proof",
    "trusted_source_sha256": "a" * 64, "environment_sha256": "b" * 64,
    "checker": {"name": "rm-lean-flow-audit", "version": "1"},
    "candidate": artifact("c"), "report": artifact("d"),
    "dependencies": {"axioms": ["propext", "Quot.sound"], "unauthorized_axioms": [],
                     "unapproved": [], "unsafe": []},
}


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import errors
        from psycopg.types.json import Jsonb

        from research_multiplier.storage.migrate import upgrade

        cls.connect = staticmethod(psycopg.connect)
        cls.errors = errors
        cls.json = staticmethod(Jsonb)
        upgrade(DSN)

    @contextmanager
    def as_role(self, role):
        from psycopg import sql

        with self.connect(DSN) as conn:
            conn.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role)))
            yield conn

    def packet(self, conn, *, approved):
        claim = flow_claim.public_claim(NETWORK, "Formal review fixture")
        row = conn.execute("""
            SELECT id, claim_id, version, packet_sha256 FROM rm.propose_formalization(%s,%s)
        """, (self.json(flow_alignment.build_packet(claim)), self.json(claim))).fetchone()
        if approved:
            conn.execute("""
                SELECT * FROM rm.confirm_formalization(%s,%s,%s,ARRAY[]::text[], 'human', 'Scope checked')
            """, (row[0], row[2], row[3]))
        return row[0], row[1]

    def propose(self, conn, packet_id, body=STATEMENT):
        packet_sha, contract_sha = conn.execute("""
            SELECT p.packet_sha256, c.contract_sha256 FROM rm.formalization_status p
            JOIN rm.claims c ON c.id = p.claim_id WHERE p.id = %s
        """, (packet_id,)).fetchone()
        body = {**body, "scope": {"principal_packet_sha256": packet_sha,
                                "claim_contract_sha256": contract_sha, **body["scope"]}}
        return conn.execute("""
            SELECT id, version, statement_sha256 FROM rm.propose_lean_statement(%s,%s)
        """, (packet_id, self.json(body))).fetchone()

    def confirm(self, conn, statement, *, expected_sha=None):
        statement_id, version, checksum = statement
        return conn.execute("""
            SELECT rm.confirm_lean_statement(%s,%s,%s,'human','Lean proposition checked')
        """, (statement_id, version, checksum if expected_sha is None else expected_sha)).fetchone()[0]

    def record(self, conn, statement, body=CHECK):
        return conn.execute("SELECT id FROM rm.record_lean_check(%s,%s,%s)",
                            (statement[0], statement[2], self.json(body))).fetchone()[0]

    def review(self, conn, check_id, action):
        return conn.execute("SELECT rm.review_lean_check(%s,%s,'human','Formal proof reviewed')",
                            (check_id, action)).fetchone()[0]

    def test_exact_version_and_approved_body_required_for_lean_result(self):
        with self.as_role("rm_human") as human:
            packet_id, _claim_id = self.packet(human, approved=False)
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.propose(human, packet_id)
            packet_id, claim_id = self.packet(human, approved=True)
            statement = self.propose(human, packet_id)
            self.assertEqual(self.propose(human, packet_id), statement)
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.confirm(human, statement, expected_sha="f" * 64)
        with self.as_role("rm_verifier") as verifier:
            with self.assertRaises(self.errors.InsufficientPrivilege), verifier.transaction():
                self.confirm(verifier, statement)
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                self.record(verifier, statement)
        with self.as_role("rm_human") as human:
            self.assertEqual(self.confirm(human, statement), statement[0])
            self.assertEqual(self.confirm(human, statement), statement[0])
            with self.assertRaises(self.errors.InsufficientPrivilege), human.transaction():
                self.record(human, statement)
        with self.as_role("rm_verifier") as verifier:
            for mismatch in (
                {"scope_matches": False, "accepted": False},
                {"candidate_theorem": "RMFlow.IntegerProof.proof"},
                {"environment_sha256": "f" * 64},
                {"trusted_source_sha256": "f" * 64},
                {"kernel_rechecked": False},
                {"checker": {"name": "other", "version": "1"}},
                {"dependencies": {**CHECK["dependencies"], "axioms": ["sorryAx"]}},
                {"dependencies": {**CHECK["dependencies"], "unapproved": ["Private.Helper"]}},
                {"report": {**CHECK["report"], "uri": "urn:sha256:" + "f" * 64}},
            ):
                with self.subTest(mismatch=mismatch):
                    with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                        self.record(verifier, statement, {**CHECK, **mismatch})
            check_id = self.record(verifier, statement)
            self.assertEqual(self.record(verifier, statement), check_id)
            self.assertEqual(verifier.execute("""
                SELECT review_status, check_ready FROM rm.lean_check_status WHERE id=%s
            """, (check_id,)).fetchone(), ("pending", True))
        with self.as_role("rm_human") as human:
            self.assertEqual(self.review(human, check_id, "approve"), self.review(human, check_id, "approve"))
            self.assertEqual(human.execute("""
                SELECT s.review_status, c.status FROM rm.lean_check_status s
                JOIN rm.claims c ON c.id = s.claim_id WHERE s.id=%s AND c.id=%s
            """, (check_id, claim_id)).fetchone(), ("approved", "open"))
            self.review(human, check_id, "return")
            self.assertEqual(human.execute("SELECT review_status FROM rm.lean_check_status WHERE id=%s",
                                           (check_id,)).fetchone(), ("returned",))

    def test_same_lean_proof_yields_separate_receipts_for_distinct_claims(self):
        receipts = []
        for _ in range(2):
            with self.as_role("rm_human") as human:
                packet, claim = self.packet(human, approved=True)
                statement = self.propose(human, packet)
                self.confirm(human, statement)
            with self.as_role("rm_verifier") as verifier:
                check = self.record(verifier, statement)
                issue = "SELECT id, claim_id FROM rm.issue_lean_evidence(%s)"
                receipt, subject = verifier.execute(issue, (check,)).fetchone()
                self.assertEqual(subject, claim)
                self.assertEqual(verifier.execute(issue, (check,)).fetchone(), (receipt, claim))
                receipts.append(receipt)
        self.assertNotEqual(*receipts)

    def test_pending_clarification_and_new_statement_block_stale_checks(self):
        with self.as_role("rm_human") as human:
            packet_id, _claim_id = self.packet(human, approved=True)
            original = self.propose(human, packet_id)
            self.confirm(human, original)
        with self.as_role("rm_scheduler") as scheduler:
            request = scheduler.execute("""
                SELECT rm.request_clarification(%s,'H2','Does this need a convention for flow?','Could change scope')
            """, (packet_id,)).fetchone()[0]
        with self.as_role("rm_verifier") as verifier:
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                self.record(verifier, original)
        with self.as_role("rm_human") as human:
            human.execute("SELECT rm.resolve_clarification(%s,'human','Keep the real domain')", (request,))
            amended = self.propose(human, packet_id, {**STATEMENT, "scope": {**STATEMENT["scope"], "review": 2}})
            self.assertEqual(amended[1], original[1] + 1)
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.confirm(human, original)
            self.confirm(human, amended)
        with self.as_role("rm_verifier") as verifier:
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                self.record(verifier, original)
            self.assertTrue(self.record(verifier, amended))
