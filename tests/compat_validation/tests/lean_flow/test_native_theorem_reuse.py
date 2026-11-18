"""Replay audited native proofs against new Lean scopes using the regular CLI."""

import os
import subprocess
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier.storage.postgres import Store


def hypothesis(proposition, description):
    return {"kind": "hypothesis", "natural": description, "formal": {"lean": {"proposition": proposition}}}


LAKE_PREMISE = "∀ n : Nat, ∃ m : Nat, n + m = n"
LAKE_GOAL = f"ReplayFacts.marker ≤ 1 ∧ ({LAKE_PREMISE})"


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class NativeTheoremReuseTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli
        self.external = self.case.fixture.workspace.parent

    def promote(self, proof):
        evidence = proof["evidence_id"]
        self.cli("review-evidence", evidence, "approve", "--actor", "researcher", "--reason", "Review the verified scope.")
        self.cli("promote-evidence", evidence, "--reason", "Use the verified theorem.")

    def exported(self, proof):
        version, = [row for row in self.cli("lean", "theorems") if row["evidence_id"] == proof["evidence_id"]]
        return version

    def without_human(self, *args, expect=0):
        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            return self.cli(*args, expect=expect)
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human

    def assert_deterministic(self, receipt):
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            attempt, = store.query("verifier", """
                SELECT a.model_provider, j.budget ->> 'max_model_calls' AS budget
                FROM rm.attempts a JOIN rm.jobs j ON j.id = a.job_id WHERE a.id = %s
            """, receipt["attempt_id"])
        self.assertEqual(attempt, {"model_provider": None, "budget": "0"})

    def test_plain_file_helper_replays_with_a_projected_premise(self):
        source, _, _ = self.case.anchor(
            "native-replay-plain-source", "(1 : Nat) = 0",
            [hypothesis("(0 : Nat) = 1", "The source equality places zero on the left.")],
            statement_text="Prove the reverse of the original zero-to-one equality.",
        )
        path = self.external / "native-file.lean"
        path.write_text(
            "namespace NativeFile\n"
            "theorem flipEq (h : (0 : Nat) = 1) : (1 : Nat) = 0 := Eq.symm h\n"
            "theorem original (h : (0 : Nat) = 1) : (1 : Nat) = 0 := flipEq h\n"
            "end NativeFile\n",
            encoding="utf-8",
        )
        initial = self.cli("lean", "check-source", source["id"], path, "--declaration", "NativeFile.original")
        self.assertTrue(initial["accepted"] and initial["kernel_rechecked"])
        self.promote(initial)
        version = self.exported(initial)
        self.assertEqual(version["body"]["formal"]["lean"]["antecedents"], ["(0 : Nat) = 1"])
        self.assertEqual(self.cli("lean", "export-theorem", initial["evidence_id"])["id"], version["id"])

        # The destination has a stronger premise, and the source file is no
        # longer present. The verified helper and implication come from the archive.
        path.unlink()
        destination, scope, _ = self.case.anchor(
            "native-replay-plain-destination", "(1 : Nat) = 0",
            [hypothesis("((0 : Nat) = 1) ∧ True", "The destination supplies the equality in a conjunction.")],
            statement_text="Establish the reverse equality using the destination conjunction.",
        )
        replay = ("lean", "reuse-theorem", destination["id"], version["id"])
        success = self.without_human(*replay)
        self.assertTrue(success["accepted"] and success["kernel_rechecked"] and success["scope_matches"])
        self.assertIn(version["id"], success["basis_version_ids"])
        self.assertEqual(self.without_human(*replay)["evidence_id"], success["evidence_id"])
        self.assert_deterministic(success)
        packet = self.cli("review", "packet", success["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
        self.promote(success)

        missing, _, _ = self.case.anchor(
            "native-replay-plain-missing", "(1 : Nat) = 0",
            [hypothesis("True", "The comparison case assumes only truth.")],
            statement_text="Check whether the reverse equality follows under a weaker review.",
        )
        blocked = self.without_human("lean", "reuse-theorem", missing["id"], version["id"], expect=2)
        self.assertIs(blocked["accepted"], False)
        self.assertEqual(blocked["block_reason"], "missing_requirements")
        self.assertNotIn("evidence_id", blocked)
        premise, = blocked["missing"]
        self.assertEqual((premise["kind"], premise["index"]), ("frozen_premise", 1))
        self.assertRegex(premise["proposition"], r"0.*1")
        self.assertEqual(self.cli("lean", "show", missing["id"])["checks"], [])

        distinct, _, _ = self.case.anchor(
            "native-replay-plain-other-conclusion", "(2 : Nat) = 0",
            [hypothesis("True ∧ ((0 : Nat) = 1)", "The comparison case retains the premise with another conclusion.")],
            statement_text="Check a different conclusion with the old premise available.",
        )
        mismatch = self.without_human("lean", "reuse-theorem", distinct["id"], version["id"], expect=2)
        self.assertIs(mismatch["accepted"], False)
        self.assertEqual(mismatch["block_reason"], "destination_not_derived")
        self.assertIn("source_consequent", mismatch["missing"][0])
        self.assertEqual(self.cli("lean", "show", distinct["id"])["checks"], [])

        second_version = self.exported(success)
        next_claim, _, _ = self.case.anchor(
            "native-replay-plain-next-claim", "((1 : Nat) = 0) ∧ True",
            [hypothesis("True ∧ (((0 : Nat) = 1) ∧ True)", "The next claim contains the middle theorem's premise.")],
            statement_text="Reuse the destination theorem and pair the result with truth.",
        )
        next_proof = self.without_human("lean", "reuse-theorem", next_claim["id"], second_version["id"])
        self.assertTrue(next_proof["accepted"] and next_proof["kernel_rechecked"] and next_proof["scope_matches"])
        self.assertIn(second_version["id"], next_proof["basis_version_ids"])
        self.promote(next_proof)

        self.cli("research", "retire-item", version["id"], "--reason", "Withdraw the source theorem version.")
        for proof in (success, next_proof):
            stale = self.cli("review", "packet", proof["evidence_id"])
            self.assertEqual(stale["claim_status"], "needs_recheck")
            self.assertTrue(stale["verification"]["needs_recheck"])

    def lake_project(self, directory, *, source, mark=0):
        root = self.external / directory
        root.mkdir()
        (root / "lean-toolchain").write_text("leanprover/lean4:v4.19.0\n", encoding="utf-8")
        configuration = 'name = "native_reuse_fixture"\n[[lean_lib]]\nname = "ReplayFacts"\n'
        if source:
            configuration += '[[lean_lib]]\nname = "ReplayOrigin"\n'
        (root / "lakefile.toml").write_text(configuration, encoding="utf-8")
        (root / "ReplayFacts.lean").write_text(
            f"namespace ReplayFacts\ndef marker : Nat := {mark}\n"
            "theorem trusted : marker ≤ 1 := by decide\n"
            "theorem alternate : marker ≤ 1 := by decide\n"
            f"theorem supplyPremise : {LAKE_PREMISE} := fun n => ⟨0, Nat.add_zero n⟩\n"
            "end ReplayFacts\n",
            encoding="utf-8",
        )
        if source:
            (root / "ReplayOrigin.lean").write_text(
                "import ReplayFacts\nnamespace ReplayOrigin\n"
                f"theorem original (h : {LAKE_PREMISE}) : {LAKE_GOAL} :=\n"
                "  ⟨ReplayFacts.trusted, h⟩\nend ReplayOrigin\n",
                encoding="utf-8",
            )
        result = subprocess.run(["lake", "build", "ReplayOrigin" if source else "ReplayFacts"],
                                cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root

    def test_lake_replay_requires_current_fact_and_preserves_semantics(self):
        source_project = self.lake_project("lake-origin", source=True)
        destination_project = self.lake_project("lake-destination", source=False)
        changed_project = self.lake_project("lake-changed-definition", source=False, mark=1)
        fact = {"kind": "theorem", "natural": "The native reuse fixture's marker is at most one.",
                "formal": {"lean": {"proposition": "ReplayFacts.marker ≤ 1", "declaration": "ReplayFacts.trusted"}}}
        premise_theorem = {"kind": "theorem", "natural": "Every natural number can be preserved by adding some natural.",
                           "formal": {"lean": {"proposition": LAKE_PREMISE, "declaration": "ReplayFacts.supplyPremise"}}}
        with patch.object(database, "PROJECT", source_project):
            source, origin, runtime = self.case.anchor(
                "native-replay-lake-source", LAKE_GOAL,
                [hypothesis(LAKE_PREMISE, "The Lake source takes the addition witness property as a premise."), fact],
                module="ReplayOrigin", statement_text="Check the source project bound and the addition witness property.",
            )
        approved, = [row for row in self.cli("research", "basis", source["packet_id"])
                     if row["body"]["kind"] == "theorem"]
        initial = self.cli("lean", "check-source", source["id"], "--module", "ReplayOrigin",
                           "--declaration", "ReplayOrigin.original", *runtime)
        self.assertEqual(initial["basis_version_ids"], [approved["id"]])
        self.promote(initial)
        version = self.exported(initial)

        # The destination imports the required basis module but does not
        # contain the original candidate module or its source tree.
        with patch.object(database, "PROJECT", destination_project):
            destination, target, options = self.case.anchor(
                "native-replay-lake-destination", LAKE_GOAL, [premise_theorem],
                module="ReplayFacts", statement_text="Check the shared bound with a proof of the original addition premise.",
            )
        premise_version, = [row for row in self.cli("research", "basis", destination["packet_id"])
                            if row["body"]["kind"] == "theorem"]
        self.assertNotEqual(source["body"]["environment"]["sha256"], destination["body"]["environment"]["sha256"])
        replay = ("lean", "reuse-theorem", destination["id"], version["id"], *options)
        gap = self.without_human(*replay, expect=2)
        self.assertIs(gap["accepted"], False)
        self.assertEqual(gap["block_reason"], "missing_requirements")
        self.assertNotIn("evidence_id", gap)
        self.assertEqual(gap["missing"][0]["requirement"],
                         {"version_id": approved["id"], "declaration": "ReplayFacts.trusted"})
        alternative = self.case.document("native-replay-alternative-lemma", {
            "kind": "theorem", "natural": "An independently checked proof supplies the same marker bound.",
            "formal": {"lean": {"proposition": "ReplayFacts.marker ≤ 1", "declaration": "ReplayFacts.alternate"}},
        })
        replacement = self.cli("research", "propose-item", "native-replay/alternate-lemma", alternative)
        self.cli("research", "approve-item", replacement["id"], "--hash", replacement["version_sha256"],
                 "--actor", "researcher", "--reason", "Confirm the alternate proof for the marker bound.")
        self.cli("research", "attach-item", destination["packet_id"], replacement["id"],
                 "--reason", "Use the already confirmed alternative for the original prerequisite.")
        success = self.without_human(*replay)
        self.assertTrue(success["accepted"] and success["kernel_rechecked"] and success["scope_matches"])
        self.assertCountEqual(success["basis_version_ids"], [version["id"], replacement["id"], premise_version["id"]])
        self.assert_deterministic(success)
        self.promote(success)

        with patch.object(database, "PROJECT", changed_project):
            changed, _, changed_options = self.case.anchor(
                "native-replay-lake-changed-definition", LAKE_GOAL,
                [hypothesis("True", "The comparison project reviews the changed marker.")],
                module="ReplayFacts", statement_text="Check the same printed bound in a project with a different marker.",
            )
        for item in (replacement, premise_version):
            self.cli("research", "attach-item", changed["packet_id"], item["id"],
                     "--reason", "Test declarations with the same printed types.")
        wrong = self.without_human("lean", "reuse-theorem", changed["id"], version["id"], *changed_options, expect=2)
        self.assertIs(wrong["accepted"], False)
        self.assertEqual(wrong["block_reason"], "dependencies_need_review")
        self.assertNotIn("evidence_id", wrong)
        self.assertIn({"declaration": "ReplayFacts.marker", "reason": "meaning_changed"}, wrong["missing"])
        self.assertEqual(self.cli("lean", "show", changed["id"])["checks"], [])

        self.cli("research", "retire-item", approved["id"], "--reason", "Withdraw the exact shared foundation.")
        for proof, scope in ((initial, origin), (success, target)):
            packet = self.cli("review", "packet", proof["evidence_id"])
            self.assertEqual(packet["claim_id"], scope["claim_id"])
            self.assertEqual(packet["claim_status"], "needs_recheck")
            self.assertTrue(packet["verification"]["needs_recheck"])
        self.assertNotIn(version["id"], [row["id"] for row in self.cli("lean", "theorems")])
