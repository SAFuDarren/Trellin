"""Exercise formalization gates on an isolated PostgreSQL database."""

from contextlib import contextmanager
import os
import unittest

from research_multiplier.flow_claim import public_claim


DSN = os.environ.get("RM_TEST_DSN")
BUDGET = {"wall_seconds": 60, "max_attempts": 1}
NETWORK = {"vertices": ["s", "t"], "source": "s", "sink": "t", "edges": [["s", "t", 1]], "target_value": 1}


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FormalizationDbTests(unittest.TestCase):
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
    def connect_as(self, role):
        from psycopg import sql

        with self.connect(DSN) as conn:
            conn.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role)))
            yield conn

    def packet(self, claim_id=None, *, unresolved=False):
        goal = {"id": "goal", "kind": "goal", "natural": "Find the largest feasible flow.",
                "formal": {"goal": "max_flow"}, "status": "stated"}
        assumption = {"id": "H2", "kind": "hypothesis", "natural": "Flow values are real.",
                      "formal": {"scalar": "real"}, "status": "stated"}
        if unresolved:
            assumption.update(status="needs_confirmation", question="Are all real-valued flows in scope?")
        return {"format_version": 1, "claim_id": claim_id, "items": [goal, assumption]}

    def propose(self, conn, packet, claim=None, previous=None):
        return conn.execute("""
            SELECT id, claim_id, version, packet_sha256 FROM rm.propose_formalization(%s,%s,%s)
        """, (self.json(packet), self.json(claim) if claim else None, previous)).fetchone()

    def confirm(self, conn, proposal, ids, *, version=None, digest=None):
        packet_id, _claim_id, original_version, original_digest = proposal
        return conn.execute("""
            SELECT * FROM rm.confirm_formalization(%s,%s,%s,%s::text[],'researcher','Reviewed scope')
        """, (packet_id, original_version if version is None else version,
              original_digest if digest is None else digest, ids)).fetchone()

    def enqueue(self, conn, subject, contract, job_payload, key="proof"):
        return conn.execute("""
            SELECT id FROM rm.enqueue_stage(%s,%s,'prove',%s,%s,%s)
        """, (subject, contract, self.json(job_payload), self.json(BUDGET), key)).fetchone()[0]

    def test_freeze_and_queue_require_exact_packet_approval(self):
        claim = public_claim(NETWORK, "Alignment fixture")
        with self.connect_as("rm_human") as human:
            proposal = self.propose(human, self.packet(claim["claim_id"], unresolved=True), claim)
            packet_id, claim_id, _version, digest = proposal
            self.assertEqual(human.execute(
                "SELECT status FROM rm.claims WHERE id=%s", (claim_id,)
            ).fetchone(), ("draft",))
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                human.execute("SELECT rm.freeze_claim(%s,'researcher','bypass')", (claim_id,))
            for bad_ids in ([], ["H2", "goal"], ["H2", "H2"], None):
                with self.subTest(bad_ids=bad_ids):
                    with self.assertRaises(self.errors.CheckViolation), human.transaction():
                        self.confirm(human, proposal, bad_ids)
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.confirm(human, proposal, ["H2"], digest="f" * 64)
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.confirm(human, proposal, ["H2"], version=2)

        with self.connect_as("rm_scheduler") as scheduler:
            with self.assertRaises(self.errors.InsufficientPrivilege), scheduler.transaction():
                self.confirm(scheduler, proposal, ["H2"])
            with self.assertRaises(self.errors.CheckViolation), scheduler.transaction():
                self.enqueue(scheduler, claim_id, digest, {})
        with self.connect_as("rm_human") as human:
            confirmed = self.confirm(human, proposal, ["H2"])
            self.assertEqual(self.confirm(human, proposal, ["H2"]), confirmed)
            _claim_id, contract = confirmed
            self.assertEqual(human.execute(
                "SELECT status, contract_snapshot FROM rm.claims WHERE id=%s", (claim_id,)
            ).fetchone(), ("open", claim))
            self.assertEqual(human.execute(
                "SELECT approved, proof_ready FROM rm.formalization_status WHERE id=%s", (packet_id,)
            ).fetchone(), (True, True))
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.propose(human, self.packet(claim["claim_id"]), previous=packet_id)
        with self.connect_as("rm_scheduler") as scheduler:
            with self.assertRaises(self.errors.CheckViolation), scheduler.transaction():
                self.enqueue(scheduler, claim_id, contract, {})
            with self.assertRaises(self.errors.CheckViolation), scheduler.transaction():
                self.enqueue(scheduler, claim_id, contract, {"formalization_id": str(packet_id),
                                                             "formalization_sha256": "f" * 64})
            payload = {"formalization_id": str(packet_id), "formalization_sha256": digest}
            job_id = self.enqueue(scheduler, claim_id, contract, payload)
            self.assertEqual(self.enqueue(scheduler, claim_id, contract, payload), job_id)

    def test_revision_invalidates_preview_without_confirming_open_target_as_proof(self):
        with self.connect_as("rm_human") as human:
            original = self.propose(human, self.packet(unresolved=True))
            current = self.propose(human, self.packet(), previous=original[0])
            self.assertEqual(current[2], 2)
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.confirm(human, original, ["H2"])
            self.assertEqual(self.confirm(human, current, []), (None, None))
            self.assertEqual(human.execute("""
                SELECT approved, superseded, proof_ready FROM rm.formalization_status WHERE id IN (%s,%s) ORDER BY version
            """, (original[0], current[0])).fetchall(), [(False, True, False), (True, False, False)])
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                # A new signed review must leave the previous claim and packet untouched.
                self.propose(human, self.packet(), previous=current[0])

    def test_pending_clarification_blocks_queue_and_resolves_without_refreezing(self):
        claim = public_claim(NETWORK, "Clarification fixture")
        with self.connect_as("rm_human") as human:
            proposal = self.propose(human, self.packet(claim["claim_id"]), claim)
            subject, contract = self.confirm(human, proposal, [])
        packet_id, _subject, _version, digest = proposal
        payload = {"formalization_id": str(packet_id), "formalization_sha256": digest}
        with self.connect_as("rm_scheduler") as scheduler:
            job_id = self.enqueue(scheduler, subject, contract, payload)
            with self.assertRaises(self.errors.InvalidParameterValue), scheduler.transaction():
                scheduler.execute("SELECT rm.request_clarification(%s,'unknown','Question','Impact')", (packet_id,))
            question = "SELECT rm.request_clarification(%s,'H2','Does real include arbitrary rational input?','Scope could change')"
            request_id = scheduler.execute(question, (packet_id,)).fetchone()[0]
            self.assertEqual(scheduler.execute(question, (packet_id,)).fetchone(), (request_id,))
            pending = scheduler.execute("""
                SELECT approved, pending_clarifications, proof_ready FROM rm.formalization_status WHERE id=%s
            """, (packet_id,)).fetchone()
            self.assertEqual(pending, (True, 1, False))
            with self.assertRaises(self.errors.CheckViolation), scheduler.transaction():
                self.enqueue(scheduler, subject, contract, payload, key="blocked-proof")
            with self.assertRaises(self.errors.CheckViolation), scheduler.transaction():
                scheduler.execute("SELECT id FROM rm.claim_job('prove','worker',60,%s)", (job_id,))
            with self.assertRaises(self.errors.InsufficientPrivilege), scheduler.transaction():
                scheduler.execute("SELECT rm.resolve_clarification(%s,'not-human','bypass')", (request_id,))
        with self.connect_as("rm_human") as human:
            self.assertEqual(human.execute("""
                SELECT rm.resolve_clarification(%s,'researcher','Yes, the real domain is unchanged')
            """, (request_id,)).fetchone(), (request_id,))
            self.assertEqual(human.execute("""
                SELECT contract_sha256 FROM rm.claims WHERE id=%s
            """, (subject,)).fetchone(), (contract,))
            self.assertEqual(human.execute("""
                SELECT resolved FROM rm.formalization_clarification_status WHERE id=%s
            """, (request_id,)).fetchone(), (True,))
        with self.connect_as("rm_scheduler") as scheduler:
            self.assertEqual(scheduler.execute(
                "SELECT id FROM rm.claim_job('prove','worker',60,%s)", (job_id,)
            ).fetchone(), (job_id,))
            self.assertEqual(scheduler.execute(question, (packet_id,)).fetchone(), (request_id,))
            self.assertEqual(scheduler.execute("""
                SELECT pending_clarifications, proof_ready FROM rm.formalization_status WHERE id=%s
            """, (packet_id,)).fetchone(), (0, True))

    def test_running_worker_cannot_submit_from_a_stale_snapshot(self):
        claim = public_claim(NETWORK, "Stale attempt fixture")
        with self.connect_as("rm_human") as human:
            proposal = self.propose(human, self.packet(claim["claim_id"]), claim)
            subject, contract = self.confirm(human, proposal, [])
        packet_id, _claim_id, _version, digest = proposal
        owner = "test-worker"
        with self.connect_as("rm_scheduler") as scheduler:
            payload = {"formalization_id": str(packet_id), "formalization_sha256": digest}
            job_id = self.enqueue(scheduler, subject, contract, payload)
            generation = scheduler.execute(
                "SELECT lease_generation FROM rm.claim_job('prove',%s,60,%s)", (owner, job_id)
            ).fetchone()[0]
        with self.connect(DSN) as agent:
            agent.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            agent.execute("SET SESSION AUTHORIZATION rm_agent")
            agent.execute("SELECT count(*) FROM pg_class").fetchone()
            with self.connect_as("rm_scheduler") as scheduler:
                scheduler.execute("""
                    SELECT rm.request_clarification(%s,'H2','Unclear domain','Changes the feasible set')
                """, (packet_id,))
            body = {
                "job_id": str(job_id), "subject": {"claim_id": str(subject), "contract_sha256": contract},
                "generation": generation, "role": "prove", "context_sha256": "a" * 64,
                "harness_version": "fixture", "finding": "exact_proof",
                "artifact": {"uri": "test:proof", "sha256": "a" * 64},
            }
            with self.assertRaises(self.errors.SerializationFailure), agent.transaction():
                agent.execute("SELECT rm.submit_attempt(%s,%s,%s,%s)", (job_id, owner, generation, self.json(body)))
