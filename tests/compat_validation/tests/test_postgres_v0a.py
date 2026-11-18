"""Run against a disposable PostgreSQL database with pgvector available.

Set RM_TEST_DSN to a superuser connection for the isolated test database. The
tests apply migrations and use separate session authorization for role coverage.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from uuid import uuid4


DSN = os.environ.get("RM_TEST_DSN")
DIGEST = "a" * 64


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.types.json import Jsonb

        from research_multiplier.storage import migrate

        cls.connect = staticmethod(psycopg.connect)
        cls.json = staticmethod(Jsonb)
        cls.errors = psycopg.errors
        migrate.upgrade(DSN)
        head = migrate.migration_head()
        with cls.connect(DSN) as connection:
            assert connection.execute(
                "SELECT version_num FROM public.rm_alembic_version"
            ).fetchone() == (head,)
            assert connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()

    def connect_as(self, role):
        from psycopg import sql

        connection = self.connect(DSN)
        connection.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role)))
        return connection

    def create_and_freeze(self, domain="test"):
        claim_id = uuid4()
        payload = {
            "format_version": 1,
            "claim_id": str(claim_id),
            "kind": "researcher_claim",
            "origin": "human",
            "domain": domain,
            "title": "Database contract fixture",
            "statement": "A fixture checks its own contract.",
            "sensitivity": "public",
            "formal_skeleton": {},
            "small_case_spec": {},
            "hypotheses": [{"label": "H1", "statement": "fixture", "formal": None}],
            "conclusion": {
                "statement": "fixture result",
                "quantifiers": [],
                "parameters": [],
                "constant_dependencies": {},
            },
            "definition_ids": [],
        }
        with self.connect_as("rm_human") as connection:
            subject = connection.execute(
                "SELECT rm.create_claim(%s)", (self.json(payload),)
            ).fetchone()[0]
            self.assertEqual(subject, claim_id)
            digest = connection.execute(
                "SELECT rm.freeze_claim(%s, %s, %s)", (subject, "test-human", "Test signed freeze")
            ).fetchone()[0]
            snapshot, contract_bytes = connection.execute(
                "SELECT contract_snapshot, contract_bytes FROM rm.claims WHERE id = %s",
                (subject,),
            ).fetchone()
            self.assertEqual(snapshot, payload)
            from hashlib import sha256
            self.assertEqual(sha256(contract_bytes.encode()).hexdigest(), digest)
        return subject, digest

    def enqueue(self, connection, subject, kind="prove", budget=None, payload=None):
        claim_id, digest = subject
        return connection.execute(
            "SELECT id FROM rm.enqueue_job(%s, %s, %s, %s, %s)",
            (claim_id, digest, kind, self.json(payload or {}),
             self.json(budget or {"wall_seconds": 60, "max_attempts": 2})),
        ).fetchone()[0]

    def claim(self, connection, owner, job_id, kind="prove"):
        return connection.execute(
            "SELECT id, lease_generation FROM rm.claim_job(%s, %s, 60, %s)",
            (kind, owner, job_id),
        ).fetchone()

    def submission(self, job_id, subject, generation=1, role="prove", model_family="first"):
        claim_id, digest = subject
        return {
            "job_id": str(job_id),
            "subject": {"claim_id": str(claim_id), "contract_sha256": digest},
            "generation": generation,
            "role": role,
            "context_sha256": DIGEST,
            "harness_version": "test",
            "finding": "audit_report" if role == "audit" else "exact_proof",
            "artifact": {"uri": f"test:{role}", "sha256": DIGEST},
            "model": {
                "provider": "test",
                "id": model_family,
                "family": model_family,
                "parameters": {},
                "prompt_sha256": DIGEST,
            },
        }

    def submit(self, connection, job_id, owner, submission):
        return connection.execute(
            "SELECT id FROM rm.submit_attempt(%s, %s, %s, %s)",
            (job_id, owner, submission["generation"], self.json(submission)),
        ).fetchone()[0]

    def test_role_boundary(self):
        denied = {
            "rm_human": (
                "SELECT rm.issue_evidence('{}')",
                "UPDATE rm.claims SET status = 'open'",
                "INSERT INTO rm.decisions DEFAULT VALUES",
            ),
            "rm_scheduler": (
                "SELECT * FROM rm.attempts",
                "SELECT rm.freeze_claim(gen_random_uuid(), 'x', 'x')",
                "SELECT rm.submit_attempt(gen_random_uuid(), 'x', 1, '{}')",
                "UPDATE rm.jobs SET status = 'failed'",
            ),
            "rm_agent": (
                "SELECT * FROM rm.claims",
                "SELECT * FROM rm.attempts",
                "SELECT * FROM rm.jobs",
                "SELECT * FROM rm.evidence",
                "SELECT rm.issue_evidence('{}')",
                "SELECT rm.insert_claim('{}', 'working_lemma')",
                "INSERT INTO rm.attempts DEFAULT VALUES",
            ),
            "rm_verifier": (
                "INSERT INTO rm.evidence DEFAULT VALUES",
                "SELECT rm.review_evidence(gen_random_uuid(), 'approve', 'x', 'x')",
                "SELECT rm.freeze_claim(gen_random_uuid(), 'x', 'x')",
            ),
        }
        for role, statements in denied.items():
            with self.connect_as(role) as connection:
                for statement in statements:
                    with self.subTest(role=role, statement=statement):
                        with self.assertRaises(self.errors.InsufficientPrivilege), connection.transaction():
                            connection.execute(statement)

    def test_task_claim_concurrency_and_superseded_submission(self):
        subject = self.create_and_freeze()
        with self.connect_as("rm_scheduler") as scheduler:
            first_job = self.enqueue(scheduler, subject)
            second_job = self.enqueue(scheduler, subject)

        owner_one, owner_two = uuid4().hex, uuid4().hex
        with self.connect_as("rm_scheduler") as first, self.connect_as("rm_scheduler") as second:
            self.assertNotEqual(
                first.execute("SELECT pg_backend_pid()").fetchone(),
                second.execute("SELECT pg_backend_pid()").fetchone(),
            )
            second.execute("SET LOCAL statement_timeout = '2s'")
            self.assertEqual(self.claim(first, owner_one, first_job), (first_job, 1))
            # First transaction has neither committed nor rolled back. The
            # second connection must skip the leased row without blocking.
            self.assertIsNone(self.claim(second, owner_two, first_job))
            self.assertEqual(self.claim(second, owner_two, second_job), (second_job, 1))

        with self.connect(DSN) as admin:
            admin.execute(
                "UPDATE rm.jobs SET lease_until = clock_timestamp() - interval '1 second' WHERE id = %s",
                (first_job,),
            )

        with self.connect_as("rm_agent") as agent:
            with self.assertRaises(self.errors.CheckViolation), agent.transaction():
                self.submit(agent, first_job, owner_one, self.submission(first_job, subject))

        replacement = uuid4().hex
        with self.connect_as("rm_scheduler") as scheduler:
            self.assertEqual(self.claim(scheduler, replacement, first_job), (first_job, 2))
            self.assertFalse(scheduler.execute(
                "SELECT rm.checkpoint_job(%s, %s, 1, '{}')", (first_job, owner_one)
            ).fetchone()[0])
        with self.connect_as("rm_agent") as agent:
            response = self.submission(first_job, subject, generation=2)
            for owner, body in (
                (owner_one, self.submission(first_job, subject)),
                (owner_one, response),
                (replacement, {**response, "subject": {"claim_id": str(subject[0]), "contract_sha256": "b"*64}}),
            ):
                with self.assertRaises(self.errors.CheckViolation), agent.transaction():
                    self.submit(agent, first_job, owner, body)
            saved = self.submit(agent, first_job, replacement, response)
            self.assertEqual(self.submit(agent, first_job, replacement, response), saved)
            with self.assertRaises(self.errors.CheckViolation), agent.transaction():
                self.submit(agent, first_job, replacement, {**response, "route_key": "overwritten"})

    def test_stage_key_is_idempotent_and_binds_the_original_request(self):
        claim_id, contract_hash = self.create_and_freeze()
        statement = "SELECT id FROM rm.enqueue_stage(%s, %s, 'prove', %s, %s, 'prove')"
        budget = self.json({"wall_seconds": 60, "max_attempts": 2})
        params = (claim_id, contract_hash, self.json({}), budget)
        ready = Barrier(2, timeout=5)

        def enqueue_stage():
            with self.connect_as("rm_scheduler") as scheduler:
                scheduler.execute("SET LOCAL statement_timeout = '5s'")
                ready.wait()
                return scheduler.execute(statement, params).fetchone()[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(enqueue_stage)
            second = pool.submit(enqueue_stage)
            job_id = first.result()
            self.assertEqual(second.result(), job_id)
        with self.connect_as("rm_scheduler") as scheduler:
            self.assertEqual(scheduler.execute(statement, params).fetchone()[0], job_id)
            with self.assertRaises(self.errors.CheckViolation), scheduler.transaction():
                scheduler.execute(statement, (claim_id, contract_hash, self.json({"different": True}), budget))

    def test_numeric_observation_cannot_receive_a_proof_grade(self):
        subject = self.create_and_freeze()
        owner = uuid4().hex
        with self.connect_as("rm_scheduler") as scheduler:
            job_id = self.enqueue(scheduler, subject, kind="small_case")
            self.claim(scheduler, owner, job_id, kind="small_case")
        submission = {**self.submission(job_id, subject, role="small_case"), "finding": "numeric_observation"}
        del submission["model"]
        with self.connect_as("rm_agent") as agent:
            attempt_id = self.submit(agent, job_id, owner, submission)
        receipt = {
            "subject": submission["subject"],
            "attempt_id": str(attempt_id),
            "kind": "exact_certificate",
            "verdict": "verified",
            "diff_status": "exact",
            "audit_mode": "none",
            "artifact": submission["artifact"],
            "verifier": {"name": "exact-flow-cut", "version": "v0a-2"},
            "provenance": {"code_revision": DIGEST, "environment_sha256": DIGEST},
            "result": {},
        }
        with self.connect_as("rm_verifier") as verifier:
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                verifier.execute("SELECT rm.issue_evidence(%s)", (self.json(receipt),))

    def test_verified_exact_receipt_and_human_review_required(self):
        subject = self.create_and_freeze(domain="fixed-directed-max-flow")
        owner = uuid4().hex
        with self.connect_as("rm_scheduler") as scheduler:
            job_id = self.enqueue(scheduler, subject)
            self.claim(scheduler, owner, job_id)
        submission = self.submission(job_id, subject)
        with self.connect_as("rm_agent") as agent:
            proof_attempt = self.submit(agent, job_id, owner, submission)

        audit_owner, wrong_audit_owner, verifier_owner = [uuid4().hex for _ in range(3)]
        with self.connect_as("rm_scheduler") as scheduler:
            payload = {"candidate_attempt_id": str(proof_attempt), "candidate": submission["artifact"]}
            with self.assertRaises(self.errors.CheckViolation), scheduler.transaction():
                self.enqueue(scheduler, subject, kind="verify", payload={**payload, "candidate": None})
            audit_job = self.enqueue(scheduler, subject, kind="audit", payload=payload)
            wrong_audit = self.enqueue(scheduler, subject, kind="audit", payload=payload)
            verify_payload = {
                **payload,
                "checker_name": "exact-flow-cut",
                "checker_version": "v0a-2",
                "checker_revision": DIGEST,
            }
            verify_job = self.enqueue(scheduler, subject, kind="verify", payload=verify_payload)
            self.claim(scheduler, audit_owner, audit_job, kind="audit")
            self.claim(scheduler, wrong_audit_owner, wrong_audit, kind="audit")
            self.claim(scheduler, verifier_owner, verify_job, kind="verify")
        with self.connect_as("rm_agent") as agent:
            audit_attempt = self.submit(
                agent, audit_job, audit_owner,
                self.submission(audit_job, subject, role="audit", model_family="independent")
            )
            same_family = self.submit(
                agent, wrong_audit, wrong_audit_owner,
                self.submission(wrong_audit, subject, role="audit", model_family=" FIRST ")
            )

        verified = self.submission(verify_job, subject, role="verify")
        del verified["model"]
        with self.connect_as("rm_agent") as agent:
            with self.assertRaises(self.errors.InsufficientPrivilege), agent.transaction():
                self.submit(agent, verify_job, verifier_owner, verified)
        with self.connect_as("rm_verifier") as verifier:
            verification_attempt = self.submit(verifier, verify_job, verifier_owner, verified)

        receipt = {
            "subject": submission["subject"],
            "attempt_id": str(proof_attempt),
            "audit_attempt_id": str(audit_attempt),
            "kind": "exact_certificate",
            "verdict": "verified",
            "diff_status": "exact",
            "audit_mode": "cross_family",
            "artifact": submission["artifact"],
            "verifier": {"name": "exact-flow-cut", "version": "v0a-2"},
            "provenance": {"code_revision": DIGEST, "environment_sha256": DIGEST},
            "result": {
                "verification_attempt_id": str(verification_attempt),
                "verification_artifact": verified["artifact"],
            },
        }
        with self.connect_as("rm_verifier") as verifier:
            for invalid in (
                {"audit_attempt_id": str(same_family)},
                {"attempt_id": str(verification_attempt), "artifact": verified["artifact"]},
                {"result": {}},
                {"result": {**receipt["result"], "verification_artifact": submission["artifact"]}},
                {"kind": "lean"},
                {"kind": "audited_proof"},
                {"kind": "counterexample"},
                {"provenance": {**receipt["provenance"], "code_revision": "b" * 64}},
            ):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                        verifier.execute("SELECT rm.issue_evidence(%s)", (self.json({**receipt, **invalid}),))
            issue_sql = "SELECT rm.issue_evidence(%s)"
            evidence_id = verifier.execute(issue_sql, (self.json(receipt),)).fetchone()[0]
            self.assertEqual(verifier.execute(issue_sql, (self.json(receipt),)).fetchone()[0], evidence_id)
            with self.assertRaises(self.errors.UniqueViolation), verifier.transaction():
                divergent = {**receipt, "result": {**receipt["result"], "other": "different body"}}
                verifier.execute(issue_sql, (self.json(divergent),))
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                verifier.execute("SELECT rm.promote_claim(%s, 'fixture')", (evidence_id,))
        with self.connect_as("rm_human") as human:
            human.execute("SELECT rm.review_evidence(%s, 'approve', 'test-human', 'fixture')", (evidence_id,))
            self.assertEqual(human.execute(
                "SELECT authenticated_role::text FROM rm.decisions WHERE evidence_id = %s",
                (evidence_id,),
            ).fetchone()[0], "rm_human")
        with self.connect(DSN) as verifier:
            verifier.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            verifier.execute("SET SESSION AUTHORIZATION rm_verifier")
            verifier.execute("SELECT status FROM rm.claims WHERE id = %s", (subject[0],)).fetchone()
            with self.connect_as("rm_human") as human:
                human.execute("SELECT rm.review_evidence(%s, 'return', 'test-human', 'retract')", (evidence_id,))
            with self.assertRaises(self.errors.SerializationFailure), verifier.transaction():
                verifier.execute("SELECT rm.promote_claim(%s, 'old snapshot')", (evidence_id,))
        with self.connect_as("rm_scheduler") as scheduler:
            self.assertIsNone(scheduler.execute(
                "SELECT id FROM rm.shared_evidence WHERE id = %s", (evidence_id,)
            ).fetchone())
        with self.connect_as("rm_verifier") as verifier:
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                verifier.execute("SELECT rm.promote_claim(%s, 'returned')", (evidence_id,))
        with self.connect_as("rm_human") as human:
            human.execute("SELECT rm.review_evidence(%s, 'approve', 'test-human', 'revised review')", (evidence_id,))
        with self.connect_as("rm_verifier") as verifier:
            verifier.execute("SELECT rm.promote_claim(%s, 'fixture')", (evidence_id,))
            self.assertEqual(verifier.execute(
                "SELECT status, decisive_evidence_id FROM rm.claims WHERE id = %s", (subject[0],)
            ).fetchone(), ("proved", evidence_id))
