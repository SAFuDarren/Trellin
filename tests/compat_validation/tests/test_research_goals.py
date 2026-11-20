from contextlib import contextmanager
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities


DSN = os.environ.get("RM_TEST_DSN")


class FixedExplorationAdapter:
    capabilities = ModelCapabilities()

    def complete(self, request):
        payload = json.loads(request["messages"][1]["content"])
        assert set(payload) == {"domain", "objective", "basis"}
        basis, = payload["basis"]
        assert set(basis) == {"version_id", "kind", "statement", "formal"}
        proposals = [
            {"statement": statement, "formal": {"lean": {"proposition": statement}},
             "basis_version_ids": [basis["version_id"]]}
            for statement in ("True", "True ∧ True")
        ]
        return AdapterReply(text=json.dumps({"proposals": proposals}), model_id="fixed-alternatives-v1")


def fixed_exploration_adapter(config):
    return FixedExplorationAdapter()


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ResearchGoalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.types.json import Jsonb
        from research_multiplier.storage.migrate import upgrade

        upgrade(DSN)
        cls.connect = staticmethod(psycopg.connect)
        cls.json = staticmethod(Jsonb)
        cls.errors = psycopg.errors

    @contextmanager
    def as_role(self, role="rm_human"):
        from psycopg import sql

        with self.connect(DSN) as db:
            db.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role)))
            yield db

    def hypothesis(self, db, proposition):
        body = {"kind": "hypothesis", "natural": proposition, "formal": {"lean": {"proposition": proposition}}}
        version, checksum = db.execute(
            "SELECT id,version_sha256 FROM rm.propose_formal_item(%s,%s)",
            ("fixture:" + str(uuid4()), self.json(body)),
        ).fetchone()
        db.execute("SELECT rm.approve_formal_item(%s,%s,'researcher','Reviewed hypothesis')", (version, checksum))
        return version

    def frame(self, db, versions, sensitivity="public"):
        body = {"title": "Examples in propositional logic", "domain": "lean-core", "sensitivity": sensitivity,
                "objective": "Compare different results using the same hypotheses",
                "basis_version_ids": [str(version) for version in versions]}
        return db.execute("SELECT id,frame_sha256 FROM rm.create_research_goal(%s)", (self.json(body),)).fetchone()

    def approve(self, db, frame):
        return db.execute(
            "SELECT rm.confirm_research_goal(%s,%s,'researcher','Reviewed basis')", frame,
        ).fetchone()[0]

    def conjecture(self, db, goal, proposition, **extra):
        body = {"statement": proposition, "formal": {"lean": {"proposition": proposition}},
                "provenance": {"kind": "human"}, **extra}
        return db.execute(
            "SELECT id,conjecture_sha256 FROM rm.propose_research_conjecture(%s,%s)",
            (goal, self.json(body)),
        ).fetchone()

    def select(self, db, candidate):
        return db.execute(
            "SELECT packet_id,claim_id FROM rm.select_research_conjecture(%s,%s,'researcher','Reviewed conclusion')",
            candidate,
        ).fetchone()

    def concern(self, db, goal, proposition, kind):
        return db.execute("SELECT id FROM rm.request_research_condition(%s,%s)", (
            goal, self.json({"kind": kind, "statement": proposition, "formal": {"lean": {"proposition": proposition}},
                             "question": f"Does the original problem entail {proposition}?",
                             "impact": "It changes which examples are included."}),
        )).fetchone()[0]

    def decide(self, db, concern, action, version=None):
        return db.execute(
            "SELECT revised_goal_id FROM rm.decide_research_condition(%s,%s,'researcher','Reviewed condition',%s)",
            (concern, action, version),
        ).fetchone()[0]

    def test_multiple_candidates_preserve_exact_basis_and_require_human_freeze(self):
        with self.as_role() as db:
            original = self.hypothesis(db, "True")
            other_versions = [self.hypothesis(db, f"{index} = {index}") for index in range(2, 11)]
            frame = self.frame(db, [original, *other_versions])
            with self.assertRaises(self.errors.CheckViolation), db.transaction():
                self.conjecture(db, frame[0], "True")
            self.approve(db, frame)
            first = self.conjecture(db, frame[0], "True")
            self.assertEqual(self.conjecture(db, frame[0], "True"), first)
            second = self.conjecture(db, frame[0], "True ∧ True")
            self.assertNotEqual(first, second)
            with self.assertRaises(self.errors.InvalidParameterValue), db.transaction():
                self.conjecture(db, frame[0], "False", basis_version_ids=[])
            with self.assertRaises(self.errors.CheckViolation), db.transaction():
                self.select(db, (second[0], "0" * 64))
        with self.as_role("rm_scheduler") as scheduler:
            with self.assertRaises(self.errors.InsufficientPrivilege), scheduler.transaction():
                self.select(scheduler, second)
        with self.as_role() as db:
            packet, claim = self.select(db, second)
            self.assertEqual(self.select(db, second), (packet, claim))
            self.assertEqual(db.execute("""
                SELECT item_version_id FROM rm.get_packet_basis(%s) WHERE packet_label='H1'
            """, (packet,)).fetchone(), (original,))
            frozen = db.execute("SELECT contract_snapshot FROM rm.claims WHERE id=%s", (claim,)).fetchone()[0]
            self.assertEqual(frozen["formal_skeleton"]["target"]["lean"]["proposition"], "True ∧ True")
            self.assertEqual(frozen["formal_skeleton"]["candidate_id"], str(second[0]))
            with self.assertRaises(self.errors.CheckViolation), db.transaction():
                self.select(db, first)
            db.execute("SELECT rm.retire_formal_item(%s,'Withdraw the premise')", (original,))
            self.assertEqual(db.execute("""
                SELECT basis_current FROM rm.research_goal_status WHERE id=%s
            """, (frame[0],)).fetchone(), (False,))

    def test_revising_a_condition_creates_a_new_frame_without_editing_frozen_claim(self):
        with self.as_role() as db:
            original = self.hypothesis(db, "True")
            additional = self.hypothesis(db, "0 = 0")
            frame = self.frame(db, [original])
            self.approve(db, frame)
            candidate = self.conjecture(db, frame[0], "True")
            issue = self.concern(db, frame[0], "True", "interpretation")
            with self.assertRaises(self.errors.CheckViolation), db.transaction():
                self.select(db, candidate)
            self.decide(db, issue, "reaffirm", original)
            convenient = self.concern(db, frame[0], "0 = 0", "route_condition")
            with self.assertRaises(self.errors.CheckViolation), db.transaction():
                self.decide(db, convenient, "revise", additional)
            self.decide(db, convenient, "reject")
            packet, claim = self.select(db, candidate)
            surfaced = self.concern(db, frame[0], "0 = 0", "interpretation")
            self.assertEqual(db.execute("""
                SELECT proof_ready FROM rm.formalization_status WHERE id=%s
            """, (packet,)).fetchone(), (False,))
            with self.assertRaises(self.errors.CheckViolation), db.transaction():
                self.decide(db, surfaced, "reaffirm", additional)
            revised = self.decide(db, surfaced, "revise", additional)
            state = db.execute("SELECT frame_sha256,body FROM rm.research_goal_status WHERE id=%s",
                               (revised,)).fetchone()
            self.assertEqual(state[1]["basis_version_ids"], [str(original), str(additional)])
            self.approve(db, (revised, state[0]))
            next_conjecture = self.conjecture(db, revised, "True")
            next_packet, next_claim = self.select(db, next_conjecture)
            self.assertNotEqual(next_packet, packet)
            self.assertEqual(db.execute("""
                SELECT kind FROM rm.claim_edges WHERE source_id=%s AND target_id=%s
            """, (next_claim, claim)).fetchone(), ("supersedes",))
            self.assertEqual(db.execute("""
                SELECT jsonb_array_length(contract_snapshot->'hypotheses') FROM rm.claims WHERE id=%s
            """, (claim,)).fetchone(), (1,))
            later_route = self.concern(db, revised, "1 = 1", "route_condition")
            with self.assertRaises(self.errors.InsufficientPrivilege), db.transaction():
                db.execute("SELECT rm.record_research_derivation(%s,%s)", (later_route, uuid4()))
        with self.as_role("rm_verifier") as verifier:
            with self.assertRaises(self.errors.CheckViolation), verifier.transaction():
                verifier.execute("SELECT rm.record_research_derivation(%s,%s)", (later_route, uuid4()))

    def test_fixed_adapter_saves_public_conjectures_with_provenance_and_no_selection(self):
        from psycopg.rows import dict_row
        from research_multiplier.workflows.goal_suggestions import suggest

        with self.as_role() as db, TemporaryDirectory() as directory:
            original = self.hypothesis(db, "True")
            public = self.frame(db, [original])
            restricted = self.frame(db, [original], "restricted")
            self.approve(db, public)
            self.approve(db, restricted)

            class SessionStore:
                json = staticmethod(self.json)

                def query(inner, role, statement, *values):
                    self.assertEqual(role, "human")
                    with db.cursor(row_factory=dict_row) as cursor:
                        return cursor.execute(statement, values).fetchall()

            with patch.dict(os.environ, {
                "RM_ARTIFACT_ROOT": directory,
                "RESEARCH_MULTIPLIER_EXPLORE_PROVIDER": "fixed-test-adapter",
                "RESEARCH_MULTIPLIER_EXPLORE_MODEL": "fixed",
                "RESEARCH_MULTIPLIER_EXPLORE_FAMILY": "fixed-test",
                "RESEARCH_MULTIPLIER_EXPLORE_ADAPTER": __name__ + ":fixed_exploration_adapter",
            }):
                with self.assertRaisesRegex(ValueError, "confirmed public"):
                    suggest(SessionStore(), restricted[0])
                with self.assertRaisesRegex(self.errors.CheckViolation, "restricted or unclassified"), db.transaction():
                    suggest(SessionStore(), public[0])
                self.assertEqual(list(Path(directory).iterdir()), [])
                checksum, = db.execute("SELECT version_sha256 FROM rm.formal_item_status WHERE id = %s",
                                       (original,)).fetchone()
                db.execute("SELECT rm.release_formal_item(%s,%s,'researcher','Reviewed public theorem text')",
                           (original, checksum))
                result = suggest(SessionStore(), public[0])
            self.assertEqual(len(result["conjectures"]), 2)
            for alternative in result["conjectures"]:
                provenance = alternative["body"]["provenance"]
                self.assertEqual(provenance["model"]["id"], "fixed-alternatives-v1")
                self.assertEqual(provenance["basis_version_ids"], [str(original)])
                self.assertTrue((Path(directory) / provenance["transcript"]["sha256"]).is_file())
            self.assertEqual(db.execute("SELECT conjecture_id FROM rm.research_goal_status WHERE id=%s",
                                        (public[0],)).fetchone(), (None,))
