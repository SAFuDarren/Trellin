"""Run native Lean source and built Lake declarations through the CLI and live database."""

from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanSourceTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli
        self.external = self.case.fixture.workspace.parent

    def run_lean(self, *command, cwd=None):
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=100)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def write_proof(self, name, source):
        file = self.external / name
        file.write_text(source, encoding="utf-8")
        return file

    def assert_local_verification(self, statement):
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            attempts = store.query("verifier", """
                SELECT a.model_provider, j.budget ->> 'max_model_calls' AS model_budget
                FROM rm.attempts a JOIN rm.jobs j ON j.id = a.job_id
                WHERE j.payload ->> 'lean_statement_id' = %s
                  AND j.payload ? 'lean_source'
            """, statement)
        self.assertTrue(attempts)
        self.assertTrue(all(row["model_provider"] is None and row["model_budget"] == "0" for row in attempts))

    def test_plain_source_is_checked_against_frozen_consequent_and_all_premises(self):
        choices = [
            {"kind": "hypothesis", "natural": "The first premise is true.",
             "formal": {"lean": {"proposition": "True"}}},
            {"kind": "hypothesis", "natural": "The second premise is zero equal to itself.",
             "formal": {"lean": {"proposition": "(0 : Nat) = 0"}}},
        ]
        anchor, scope, _ = self.case.anchor("plain-native-proof", "True ∧ True", choices)
        select = ("lean", "check-source", anchor["id"])
        declare = ("--declaration", "αproof")
        theorem = "theorem αproof (used : True) (unused : (0 : Nat) = 0)"
        original = self.write_proof("Plain.lean", f"{theorem} : True ∧ True := by\n  exact ⟨used, used⟩\n")
        wrong = self.write_proof("Wrong.lean", f"{theorem} : True := by\n  exact used\n")
        extra = self.write_proof("Extra.lean", f"{theorem} (extra : False) : True ∧ True := False.elim extra\n")
        core_fact = self.write_proof(
            "UnapprovedCoreFact.lean", f"{theorem} : True ∧ True :=\n"
            "  (fun (_ : (0 : Nat) ≤ 0) => And.intro used used) (Nat.le_refl 0)\n",
        )
        for file in (original, wrong, extra, core_fact):
            self.run_lean("lean", file)
        for file in (wrong, extra):
            with self.subTest(file=file.name):
                rejected = self.cli(*select, file, *declare, expect=2)
                self.assertTrue(rejected["compiled"] and rejected["kernel_rechecked"])
                self.assertFalse(rejected["scope_matches"])
                self.assertNotIn("evidence_id", rejected)
        rejected = self.cli(*select, core_fact, *declare, expect=2)
        self.assertTrue(rejected["scope_matches"] and rejected["kernel_rechecked"])
        self.assertIn("Nat.le_refl", rejected["dependencies"]["unapproved"])
        executable = self.write_proof(
            "Executable.lean", "import Lean\n"
            '#eval IO.FS.writeFile "' + str(self.external / "effect") + '" "ran"\n'
            + original.read_text(encoding="utf-8"),
        )
        self.assertIsInstance(self.cli(*select, executable, *declare, expect=2), str)
        self.assertFalse((self.external / "effect").exists())
        self.assertEqual(self.cli("lean", "show", anchor["id"])["checks"], [])

        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            good = self.cli(*select, original, *declare)
            self.assertTrue(good["accepted"] and good["scope_matches"] and good["kernel_rechecked"])
            self.assertEqual(good["expected_type"], good["actual_type"])
            self.assertEqual([row["occurs_in_proof_term"] for row in good["dependencies"]["premise_usage"]],
                             [True, False])
            checksum = sha256(original.read_bytes()).hexdigest()
            self.assertEqual(good["lean_source"]["candidate_sha256"], checksum)
            self.assertEqual(good["direct_basis_version_ids"], [])
            self.assertEqual(self.cli(*select, original, *declare)["evidence_id"], good["evidence_id"])
            self.assert_local_verification(anchor["id"])
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human
        self.assertEqual(len(self.cli("lean", "show", anchor["id"])["checks"]), 1)
        packet = self.cli("review", "packet", good["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
        self.assertEqual(packet["key_steps"]["source_declaration"], "αproof")

    @unittest.skipUnless(database.PROJECT.is_dir() and (database.PROJECT / ".lake" / "packages").is_dir(),
                         "set RM_TEST_LEAN_PROJECT to a prepared Lean 4.19 Mathlib project")
    def test_built_lake_declaration_and_file_share_the_reviewed_basis(self):
        project = self.external / "existing-lake"
        project.mkdir()
        for name in ("lean-toolchain", "lake-manifest.json"):
            shutil.copyfile(database.PROJECT / name, project / name)
        config = (database.PROJECT / "lakefile.toml").read_text(encoding="utf-8")
        (project / "lakefile.toml").write_text(config + '\n[[lean_lib]]\nname = "NativeProofs"\n', encoding="utf-8")
        (project / ".lake").mkdir()
        (project / ".lake" / "packages").symlink_to(database.PROJECT / ".lake" / "packages", target_is_directory=True)
        (project / "NativeProofs.lean").write_text(
            "import Mathlib.Data.Real.Basic\n"
            "namespace NativeProofs\n"
            "theorem square (x : Real) : 0 ≤ x ^ 2 := sq_nonneg x\n"
            "theorem spare (x : Real) : 0 ≤ x ^ 2 := square x\n"
            "axiom extra : False\n"
            "theorem checked (h : ∀ x : Real, x ≤ x) : ∀ x : Real, 0 ≤ x ^ 2 ∧ x ≤ x :=\n"
            "  fun x => ⟨square x, h x⟩\n"
            "theorem shortcut (h : ∀ x : Real, x ≤ x) : ∀ x : Real, 0 ≤ x ^ 2 ∧ x ≤ x :=\n"
            "  fun x => ⟨spare x, h x⟩\n"
            "theorem withAxiom (_ : ∀ x : Real, x ≤ x) : ∀ x : Real, 0 ≤ x ^ 2 ∧ x ≤ x :=\n"
            "  False.elim extra\n"
            "end NativeProofs\n", encoding="utf-8",
        )
        self.run_lean("lake", "build", "NativeProofs", cwd=project)
        choices = [
            {"kind": "hypothesis", "natural": "The original premise is reflexivity on the real numbers.",
             "formal": {"lean": {"proposition": "∀ x : Real, x ≤ x"}}},
            {"kind": "theorem", "natural": "Every real square is nonnegative.",
             "formal": {"lean": {"proposition": "∀ x : Real, 0 ≤ x ^ 2", "declaration": "NativeProofs.square"}}},
        ]
        with patch.object(database, "PROJECT", project):
            anchor, scope, options = self.case.anchor(
                "lake-native-proof", "∀ x : Real, 0 ≤ x ^ 2 ∧ x ≤ x", choices, module="NativeProofs")
        versions = self.cli("research", "basis", anchor["packet_id"])
        theorem, = [row["id"] for row in versions if row["body"]["kind"] == "theorem"]
        command = ("lean", "check-source", anchor["id"], *options)
        select_module = (*command, "--module", "NativeProofs", "--declaration")
        for name, category, used in (
            ("shortcut", "unapproved", "NativeProofs.spare"),
            ("withAxiom", "unauthorized_axioms", "NativeProofs.extra"),
        ):
            with self.subTest(name=name):
                rejected = self.cli(*select_module, f"NativeProofs.{name}", expect=2)
                self.assertTrue(rejected["scope_matches"] and rejected["kernel_rechecked"])
                self.assertIn(used, rejected["dependencies"][category])
        self.assertEqual(self.cli("lean", "show", anchor["id"])["checks"], [])
        built = self.cli(*select_module, "NativeProofs.checked")
        raw = self.write_proof("Raw.lean",
                               "import NativeProofs\n"
                               "theorem usesLake (h : ∀ x : Real, x ≤ x) : ∀ x : Real, 0 ≤ x ^ 2 ∧ x ≤ x :=\n"
                               "  fun x => ⟨NativeProofs.square x, h x⟩\n")
        plain = self.cli("lean", "check-source", anchor["id"], raw, *options, "--declaration", "usesLake")
        for report, mode in ((built, "module"), (plain, "file")):
            self.assertTrue(report["scope_matches"] and report["kernel_rechecked"])
            self.assertEqual(report["lean_source"]["mode"], mode)
            self.assertEqual(report["direct_basis_version_ids"], [theorem])
            self.assertEqual(report["basis_version_ids"], [theorem])
            self.assertEqual(report["expanded_basis_version_ids"], [theorem])
            self.assertIn("NativeProofs.square", report["dependencies"]["direct_constants"])
            self.assertIn("sq_nonneg", report["dependencies"]["constants"])
            self.assertTrue(report["dependencies"]["premise_usage"][0]["occurs_in_proof_term"])
        self.assert_local_verification(anchor["id"])
        evidence = built["evidence_id"]
        self.cli("review-evidence", evidence, "approve", "--actor", "researcher", "--reason", "Review the used basis.")
        self.cli("promote-evidence", evidence, "--reason", "Adopt the independently checked proof.")
        self.assertEqual(self.cli("review", "packet", evidence)["claim_status"], "proved")
        self.cli("research", "retire-item", theorem, "--reason", "Withdraw the exact basis version.")
        for report in (built, plain):
            current = self.cli("review", "packet", report["evidence_id"])
            self.assertEqual(current["claim_id"], scope["claim_id"])
            self.assertEqual(current["claim_status"], "needs_recheck")
            self.assertTrue(current["verification"]["needs_recheck"])
