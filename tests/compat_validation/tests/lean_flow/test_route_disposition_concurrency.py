"""Exercise an older verifier snapshot against current route decisions."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
from test_lean_flow import DSN

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class RouteDispositionConcurrencyTests(unittest.TestCase):
    def test_older_snapshots_cannot_append_competing_dispositions(self):
        import psycopg

        fixture = fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        premise = "17423 = 17423"
        proposition = f"{premise} ∧ {premise}"
        anchor, scope, _ = fixture.anchor("Route disposition fences", f"({proposition}) ∧ {premise}", [{
            "kind": "hypothesis", "natural": "The original premise is reflexivity at 17423.",
            "formal": {"lean": {"proposition": premise}},
        }])
        alternatives = fixture.document("alternative-local-proofs", {"format_version": 1, "nodes": [
            {"name": "first", "proposition": proposition,
             "proof": fixtures.intro(1, {"and": [{"use": 0}, {"use": 0}]})},
            {"name": "second", "proposition": proposition,
             "proof": fixtures.intro(1, {"and": [{"rfl": True}, {"use": 0}]})},
        ]})
        first, second = cli("lean", "derive", anchor["id"], alternatives)["lemmas"]
        witness, _, _ = fixture.anchor("Independent exact verification", proposition, [])
        witness_document = fixture.document("independent-proof", {
            "format_version": 1, "proof": {"and": [{"rfl": True}, {"rfl": True}]},
        })
        evidence = cli("lean", "check-theory", witness["id"], witness_document,
                       "--out", fixture.fixture.output)["evidence_id"]

        def request(name):
            document = fixture.document(name, {
                "kind": "route_condition", "statement": proposition,
                "formal": {"lean": {"proposition": proposition}},
                "question": f"Can this proof route use the checked intermediate claim? {name}",
                "impact": "Applies only to this route.",
            })
            return cli("goal", "condition", scope["goal_id"], document)["id"]

        def assert_read_write_conflict(role, operation, parameters, winner):
            dsn = fixture.fixture.env[f"RM_{role.upper()}_DSN"]
            with psycopg.connect(dsn) as stale:
                stale.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                stale.execute("SELECT pg_current_snapshot()").fetchone()
                committed = winner()
                with self.assertRaises(psycopg.errors.SerializationFailure):
                    stale.execute(operation, parameters).fetchone()
                stale.rollback()
                return committed

        def local(condition, lemma, *, expect=0):
            return cli("goal", "record-local-derivation", condition, lemma, expect=expect)

        local_sql = "SELECT * FROM rm.record_route_condition_local_lemma(%s,%s)"
        legacy_sql = "SELECT * FROM rm.record_research_derivation(%s,%s)"
        human_sql = "SELECT * FROM rm.decide_research_condition(%s,'reject','reviewer','Keep the proof route closed')"

        same = request("idempotent-retry")
        first_record = assert_read_write_conflict(
            "verifier", local_sql, (same, first["id"]), lambda: local(same, first["id"]),
        )
        self.assertEqual(local(same, first["id"]), first_record)

        different = request("competing-local-proof")
        assert_read_write_conflict(
            "verifier", local_sql, (different, second["id"]), lambda: local(different, first["id"]),
        )
        self.assertIn("another current local lemma", local(different, second["id"], expect=2))

        local_wins = request("local-before-legacy")
        assert_read_write_conflict(
            "verifier", legacy_sql, (local_wins, evidence), lambda: local(local_wins, first["id"]),
        )
        self.assertIn("already has a current local lemma",
                      cli("goal", "record-derivation", local_wins, evidence, expect=2))

        legacy_wins = request("legacy-before-local")
        assert_read_write_conflict(
            "verifier", local_sql, (legacy_wins, first["id"]),
            lambda: cli("goal", "record-derivation", legacy_wins, evidence),
        )
        self.assertIn("separate decision", local(legacy_wins, first["id"], expect=2))

        human_reconsiders = request("local-before-researcher")
        assert_read_write_conflict(
            "human", human_sql, (human_reconsiders,), lambda: local(human_reconsiders, first["id"]),
        )
        cli("goal", "decide-condition", human_reconsiders, "reject",
            "--actor", "reviewer", "--reason", "Keep the proof route closed")
        state = cli("goal", "show", scope["goal_id"])
        self.assertTrue(state["frame"]["proof_ready"])
        by_id = {condition["id"]: condition for condition in state["conditions"]}
        self.assertEqual(by_id[human_reconsiders]["action"], "reject")
        self.assertEqual(by_id[human_reconsiders]["local_lemma_id"], first["id"])
        with patch.dict(os.environ, fixture.fixture.env), Store() as store:
            history = store.query("human", """
                SELECT request_id,count(*) AS total FROM rm.route_condition_local_derivations
                WHERE request_id = ANY(%s::uuid[]) GROUP BY request_id
            """, [same, different, local_wins, legacy_wins, human_reconsiders])
        self.assertEqual({str(row["request_id"]): row["total"] for row in history},
                         {key: 1 for key in (same, different, local_wins, human_reconsiders)})
