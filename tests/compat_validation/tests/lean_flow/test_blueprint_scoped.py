"""Check scoped blueprint proof execution and cross-claim dependency status."""

import unittest

import test_final_local_lemmas as fixtures
import test_lean_flow as database


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ScopedBlueprintTests(unittest.TestCase):
    def test_blueprint_uses_same_statement_lemma_and_reports_unexported_result(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        proposition = "6 = 6"
        intermediate = f"{proposition} ∧ {proposition}"

        def hypothesis(purpose):
            return {"kind": "hypothesis", "natural": purpose, "formal": {"lean": {"proposition": proposition}}}

        source, _, _ = case.anchor("blueprint-local-source", intermediate,
                                   [hypothesis("Reflexivity at six for the scoped blueprint source.")], sensitivity="public")
        successor, _, _ = case.anchor("blueprint-local-successor", f"({intermediate}) ∧ True",
                                      [hypothesis("Reflexivity at six for the separate blueprint successor.")], sensitivity="public")
        premise = {"use": 0}
        plan = case.document("local-lemma", {"format_version": 1, "nodes": [
            {"name": "L1", "proposition": proposition, "proof": fixtures.intro(1, premise)},
        ]})
        lemma, = case.cli("lean", "derive", source["id"], plan)["lemmas"]
        graph = case.document("blueprint", {"format_version": 1, "nodes": [
            {"name": "successor", "statement_id": successor["id"], "proof": fixtures.intro(1, {
                "and": [fixtures.apply({"node_ref": "source"}, premise), {"trivial": True}],
            })},
            {"name": "source", "statement_id": source["id"], "proof": fixtures.intro(1, {
                "and": [fixtures.apply({"ref": lemma["id"]}, premise), premise],
            })},
        ]})
        command = ("lean", "blueprint", graph)
        source_report, successor_report = case.cli(*command, "--work", "--out", case.fixture.output)["nodes"]
        self.assertEqual(source_report["status"], "awaiting_evidence_review")
        self.assertEqual(successor_report["status"], "waiting_for_theorems")
        evidence = source_report["evidence_id"]
        self.assertEqual(case.cli("review", "packet", evidence)["key_steps"]["local_lemma_ids"], [lemma["id"]])

        different_proof = case.document("ordinary-proof", {"format_version": 1, "proof": fixtures.intro(1, {
            "and": [premise, premise],
        })})
        alternative = case.cli("lean", "check-theory", source["id"], different_proof, "--out", case.fixture.output)
        self.assertNotEqual(alternative["evidence_id"], evidence)
        case.cli("review-evidence", evidence, "approve", "--actor", "researcher", "--reason", "Review exact local proof.")
        case.cli("promote-evidence", evidence, "--reason", "Apply the proof within the frozen claim.")

        source_report, successor_report = case.cli(*command, "--work", "--out", case.fixture.output)["nodes"]
        self.assertEqual(source_report["status"], "proved_without_export")
        self.assertEqual(source_report["evidence_id"], evidence)
        self.assertIsNone(source_report["theorem_version_id"])
        self.assertEqual(successor_report["status"], "blocked_by_unexported_theorem")
        self.assertIsNone(successor_report["evidence_id"])
        self.assertIn("portable proof", case.cli("lean", "export-theorem", evidence, expect=2))

        case.cli("research", "retire-input", "source", lemma["body"]["candidate"]["sha256"],
                 "--reason", "Withdraw the only local lemma.")
        self.assertEqual(case.cli(*command)["nodes"][0]["status"], "needs_recheck")
