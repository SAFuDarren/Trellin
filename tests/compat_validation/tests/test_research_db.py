"""Check reusable verifier facts and human-selected goals in PostgreSQL."""

from contextlib import contextmanager
from copy import deepcopy
import os
import unittest

from research_multiplier.domain_packages import flow_alignment
from research_multiplier import flow_claim, verification


DSN = os.environ.get("RM_TEST_DSN")
NETWORK = {"vertices": ["s", "t"], "source": "s", "sink": "t", "edges": [["s", "t", 7]], "target_value": 7}
BUDGET = {"wall_seconds": 60, "max_attempts": 1}
ARTIFACT = {"uri": "urn:sha256:" + "a" * 64, "sha256": "a" * 64}
CHECK_ARTIFACT = {"uri": "urn:sha256:" + "b" * 64, "sha256": "b" * 64}


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ResearchDbTests(unittest.TestCase):
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

    def propose(self, conn, packet, claim=None, previous=None):
        return conn.execute("""
            SELECT id, version, packet_sha256 FROM rm.propose_formalization(%s,%s,%s)
        """, (self.json(packet), self.json(claim) if claim else None, previous)).fetchone()

    def confirm(self, conn, packet):
        return conn.execute("""
            SELECT * FROM rm.confirm_formalization(%s,%s,%s,ARRAY[]::text[],'human','Reviewed the stated scope')
        """, packet).fetchone()

    def attempt(self, subject, kind, payload, artifact):
        claim_id, contract = subject
        owner = "scratch-worker"
        with self.as_role("rm_scheduler") as scheduler:
            job_id = scheduler.execute("SELECT id FROM rm.enqueue_stage(%s,%s,%s,%s,%s,%s)",
                (claim_id, contract, kind, self.json(payload), self.json(BUDGET), kind)).fetchone()[0]
            generation = scheduler.execute("SELECT lease_generation FROM rm.claim_job(%s,%s,60,%s)",
                (kind, owner, job_id)).fetchone()[0]
        body = {
            "job_id": str(job_id), "generation": generation, "role": kind,
            "subject": {"claim_id": str(claim_id), "contract_sha256": contract},
            "context_sha256": ARTIFACT["sha256"], "harness_version": "test",
            "finding": "exact_proof", "artifact": artifact,
        }
        with self.as_role("rm_agent" if kind == "prove" else "rm_verifier") as worker:
            return worker.execute("SELECT id FROM rm.submit_attempt(%s,%s,%s,%s)",
                (job_id, owner, generation, self.json(body))).fetchone()[0]

    def test_checked_flow_facts_are_idempotent_and_scoped(self):
        claim = flow_claim.public_claim(NETWORK, "Exact derived facts")
        with self.as_role("rm_human") as human:
            packet = self.propose(human, flow_alignment.build_packet(claim), claim)
            subject = self.confirm(human, packet)
        packet_id, _version, packet_hash = packet
        proof_id = self.attempt(subject, "prove", {"formalization_id": str(packet_id),
                                                  "formalization_sha256": packet_hash}, ARTIFACT)
        checker = verification.checker_identity()
        verify_id = self.attempt(subject, "verify", {"candidate_attempt_id": str(proof_id),
                                                    "candidate": ARTIFACT, **checker}, CHECK_ARTIFACT)
        result = verification.check(claim, {"certificate": {"flows": [["s", "t", 7]], "source_side": ["s"]}},
                                    checker)["verification"]
        statement = """
            SELECT id, item_key, formal, premise_item_ids, artifact_sha256, verification_sha256
            FROM rm.register_flow_derivations(%s,%s,%s)
        """
        with self.as_role("rm_agent") as agent:
            with self.assertRaises(self.errors.InsufficientPrivilege), agent.transaction():
                agent.execute(statement, (packet_id, verify_id, self.json(result)))
        with self.as_role("rm_verifier") as verifier:
            for attempt_id, checked in (
                (proof_id, result), (verify_id, {**result, "flow_value": 1}),
                (verify_id, {**result, "source_side": ["t"]}),
            ):
                with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                    verifier.execute(statement, (packet_id, attempt_id, self.json(checked)))
            facts = verifier.execute(statement, (packet_id, verify_id, self.json(result))).fetchall()
            self.assertEqual(facts, verifier.execute(statement, (packet_id, verify_id, self.json(result))).fetchall())
            self.assertEqual({row[1]: row[2]["quantifier"] for row in facts},
                             {"cut_upper_bound": "forall", "flow_attains_target": "exists"})
            self.assertTrue(all(row[3:] == (["H1", "H2", "H3"], ARTIFACT["sha256"], CHECK_ARTIFACT["sha256"])
                                for row in facts))
        with self.as_role("rm_scheduler") as scheduler:
            self.assertEqual(scheduler.execute("SELECT count(*) FROM rm.verified_derivations WHERE packet_id=%s",
                                               (packet_id,)).fetchone(), (2,))
        retired_fact, *_ = next(row for row in facts if row[1] == "cut_upper_bound")
        with self.as_role("rm_human") as human:
            version = human.execute("""
                SELECT item_version_id FROM rm.get_packet_basis(%s) WHERE body #>> '{source,derivation_id}' = %s
            """, (packet_id, str(retired_fact))).fetchone()[0]
            human.execute("SELECT rm.retire_formal_item(%s,'Withdrawn for review')", (version,))
        with self.as_role("rm_scheduler") as scheduler:
            self.assertEqual(scheduler.execute("SELECT item_key FROM rm.verified_derivations WHERE packet_id=%s",
                                               (packet_id,)).fetchall(), [("flow_attains_target",)])
            scheduler.execute("SELECT rm.request_clarification(%s,'H2','Which reals?','Scope could change')",
                              (packet_id,))
            self.assertEqual(scheduler.execute("SELECT count(*) FROM rm.verified_derivations WHERE packet_id=%s",
                                               (packet_id,)).fetchone(), (0,))
        with self.as_role("rm_verifier") as verifier:
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                verifier.execute(statement, (packet_id, verify_id, self.json(result)))

    def test_exploration_choice_reuses_approved_premises_and_stays_draft(self):
        claim = flow_claim.public_claim(NETWORK, "Chosen exact goal")
        goal = flow_alignment.build_packet(claim)
        basis = {
            "format_version": 1, "mode": "exploration", "domain": claim["domain"],
            "sensitivity": "public", "title": "Inspect the network",
            "network": {key: value for key, value in NETWORK.items() if key != "target_value"},
            "items": [item for item in goal["items"] if item["kind"] != "goal"],
        }
        choose = "SELECT id, claim_id FROM rm.choose_exploration(%s,%s,%s,%s,%s)"
        with self.as_role("rm_human") as human:
            basis_ref = self.propose(human, basis)
            args = (*basis_ref, self.json(goal), self.json(claim))
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                human.execute(choose, args)
            self.assertEqual(self.confirm(human, basis_ref), (None, None))
            wrong = deepcopy(goal)
            wrong["items"][1]["natural"] = "Another network"
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                human.execute(choose, (*basis_ref, self.json(wrong), self.json(claim)))
            goal_id, claim_id = human.execute(choose, args).fetchone()
            self.assertEqual(str(claim_id), claim["claim_id"])
            retried_claim = flow_claim.public_claim(NETWORK, claim["title"])
            retried_goal = flow_alignment.build_packet(retried_claim)
            retry_args = (*basis_ref, self.json(retried_goal), self.json(retried_claim))
            self.assertEqual(human.execute(choose, retry_args).fetchone(), (goal_id, claim_id))
            self.assertEqual(human.execute("""
                SELECT s.approved, s.proof_ready, s.basis_packet_id, s.basis_packet_sha256, c.status
                FROM rm.formalization_status s JOIN rm.claims c ON c.id = s.claim_id WHERE s.id = %s
            """, (goal_id,)).fetchone(), (False, False, basis_ref[0], basis_ref[2], "draft"))
            with self.assertRaises(self.errors.CheckViolation), human.transaction():
                self.propose(human, wrong, previous=goal_id)
            revised_goal = deepcopy(goal)
            revised_goal["items"][0].update(status="needs_confirmation", question="Is the goal fixed?")
            amended = self.propose(human, revised_goal, previous=goal_id)
            self.assertEqual(human.execute(choose, retry_args).fetchone(), (amended[0], claim_id))
