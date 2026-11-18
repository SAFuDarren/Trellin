"""Exercise missing-basis review and local proof provenance with native PostgreSQL and Lean."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workflows import formalpedia_review


def source_identity():
    return {"snapshot": "a" * 64, "environment": "c5ea003", "theorem": "Sample.pair", "solution_sha256": "b" * 64,
            "statement_sha256": "c" * 64, "index_entry_sha256": "d" * 64, "solution_file_sha256": "e" * 64}


def source_provenance(candidate):
    def artifact(digest):
        return {"sha256": digest, "uri": f"urn:sha256:{digest}"}

    return {"author": "Author", "path": "Solutions/Sol_Sample_pair.lean",
            "exported_statement": artifact(candidate["statement_sha256"]),
            "exported_proof_body": artifact(candidate["solution_sha256"]),
            "exported_solution_file_sha256": candidate["solution_file_sha256"],
            "index_entry_sha256": candidate["index_entry_sha256"]}


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FormalpediaReviewTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli

    def test_exact_missing_inputs_are_deduplicated_and_follow_frozen_goal_verification(self):
        anchor, scope, _ = self.case.anchor("external-basis-" + uuid4().hex, "True ∧ (True ∧ True)", [])
        statement_id = anchor["id"]
        claim_id = scope["claim_id"]
        candidate = source_identity()
        daily = {**candidate, "snapshot": "f" * 64, "index_entry_sha256": "f" * 64}
        missing = [{"kind": "premise", "proposition": "False"},
                   {"kind": "declaration", "declaration": "Sample.source", "proposition": "True"}]
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            request_id = formalpedia_review.record(store, statement_id, candidate, missing)
            repeated = formalpedia_review.record(store, statement_id, daily, missing[::-1] + missing[:1])
            self.assertEqual(request_id, repeated)
            self.assertEqual(formalpedia_review.provenance(store, statement_id), [])
            with self.assertRaises(DatabaseOperationError):
                formalpedia_review.record(store, statement_id, candidate, [{"kind": "toolchain", "proposition": "False"}])
            with self.assertRaises(DatabaseOperationError):
                formalpedia_review.accept(store, statement_id, uuid4(), candidate, source_provenance(candidate))
            with self.assertRaises(DatabaseOperationError):
                store.query("agent", "SELECT rm.record_formalpedia_application_review(%s,%s,%s)", statement_id,
                            store.json(candidate), store.json(missing))
            with self.assertRaises(DatabaseOperationError):
                store.query("agent", "SELECT * FROM rm.formalpedia_checked_sources(%s)", statement_id)
        report = self.cli("research", "resume", claim_id)
        note, = [row for row in report["decisions"]["items"] if row["kind"] == "review_formalpedia_basis"]
        self.assertEqual(note["reference"]["item_id"], str(request_id))
        self.assertEqual(note["reference"]["statement_id"], statement_id)
        self.assertEqual(note["candidate"], candidate)
        self.assertCountEqual(note["obligations"]["items"], missing)
        self.assertTrue(report["scope"]["proof_ready"])
        rendered = self.cli("research", "resume", claim_id, "--format", "text")
        self.assertIn("Required proposition · False", rendered)
        self.assertIn("Unapproved declaration · Sample.source", rendered)
        project = self.cli("research", "resume", "--limit", 20)
        self.assertEqual(sum(row["reference"]["item_id"] == str(request_id)
                             for row in project["decisions"]["items"]), 1)

        plan = self.case.document("independently-checkable-pair", {"format_version": 1, "nodes": [
            {"name": "Pair", "proposition": "True ∧ True", "proof": {"and": [{"trivial": True}, {"trivial": True}]}},
        ], "goal_proof": {"and": [{"trivial": True}, {"node_ref": "Pair"}]}})
        result = self.cli("lean", "derive", statement_id, plan)
        lemma, = result["lemmas"]
        self.assertEqual(lemma["status"], "verified")
        evidence_id = result["goal"]["evidence_id"]
        report = self.cli("research", "resume", claim_id)
        self.assertNotIn("review_formalpedia_basis", {row["kind"] for row in report["decisions"]["items"]})
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            source = source_provenance(candidate)
            formalpedia_review.accept(store, statement_id, lemma["id"], candidate, source)
            equivalent = {**source, "local_proposition": "True ∧ True", "type_comparison_checker_sha256": "f" * 64}
            formalpedia_review.accept(store, statement_id, lemma["id"], candidate, equivalent)
            saved, = formalpedia_review.provenance(store, statement_id)
            self.assertEqual(str(saved["lemma_id"]), lemma["id"])
            self.assertEqual(saved["provenance"], source)
            self.assertEqual(saved["status"], "verified")
            self.assertEqual(saved["binding"], "source_files_bound")
            self.assertIsNone(formalpedia_review.record(store, statement_id, daily, missing))
            with self.assertRaises(DatabaseOperationError):
                formalpedia_review.accept(store, statement_id, lemma["id"], candidate, {**source, "author": "Altered"})
            with self.assertRaises(DatabaseOperationError):
                formalpedia_review.accept_goal(store, statement_id, uuid4(), candidate, source)
            formalpedia_review.accept_goal(store, statement_id, evidence_id, candidate, source)
            direct, = [row for row in formalpedia_review.provenance(store, statement_id, include_goals=True) if row["evidence_id"]]
            self.assertEqual(str(direct["evidence_id"]), evidence_id)
            self.assertIsNone(direct["lemma_id"])
            self.assertEqual(direct["status"], "verified")
        self.cli("review-evidence", evidence_id, "return", "--actor", "Researcher",
                 "--reason", "Withdraw the previous exact goal receipt.")
        report = self.cli("research", "resume", claim_id)
        reopened, = [row for row in report["decisions"]["items"] if row["kind"] == "review_formalpedia_basis"]
        self.assertEqual(reopened["reference"]["item_id"], str(request_id))
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            direct, = [row for row in formalpedia_review.provenance(store, statement_id, include_goals=True) if row["evidence_id"]]
            self.assertEqual(direct["status"], "needs_recheck")

    def test_whole_goal_provenance_rolls_back_with_its_receipt(self):
        from research_multiplier import composed_lean, final_lean_local
        from research_multiplier.policy.lean_search_scope import load_local_search_scope

        anchor, scope, _ = self.case.anchor("atomic-formalpedia-" + uuid4().hex, "True", [])
        statement_id = anchor["id"]
        claim_id = scope["claim_id"]
        candidate = source_identity()
        source = source_provenance(candidate)
        proof = {"format_version": 1, "proof": {"trivial": True}}

        def record(store, receipt):
            formalpedia_review.accept_goal(store, statement_id, receipt["evidence_id"], candidate, source)

        def interrupt(store, receipt):
            record(store, receipt)
            raise RuntimeError("interrupted receipt transaction")

        with patch.dict(os.environ, self.case.fixture.env):
            with Store() as store:
                statement, _, _ = load_local_search_scope(store, statement_id, allow_restricted=True)
                checked = composed_lean.select_runtime(store, statement)
            with self.assertRaisesRegex(RuntimeError, "interrupted receipt transaction"):
                final_lean_local.check(statement_id, proof, self.case.fixture.output,
                                       checked_runtime=checked, on_receipt=interrupt)
            with Store() as store:
                self.assertEqual(formalpedia_review.provenance(store, statement_id), [])
            before = self.cli("research", "resume", claim_id)
            self.assertNotIn("review_evidence", {row["kind"] for row in before["decisions"]["items"]})
            accepted = final_lean_local.check(statement_id, proof, self.case.fixture.output,
                                              checked_runtime=checked, on_receipt=record)
            with Store() as store:
                linked, = formalpedia_review.provenance(store, statement_id, include_goals=True)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(str(linked["evidence_id"]), str(accepted["evidence_id"]))
        self.assertEqual(self.cli("lean", "formalpedia-sources", statement_id), [])
        cli_link, = self.cli("lean", "formalpedia-sources", statement_id, "--include-goals")
        self.assertEqual(cli_link["evidence_id"], accepted["evidence_id"])
