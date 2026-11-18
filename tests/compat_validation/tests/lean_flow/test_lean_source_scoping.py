"""Exercise source-local proof scopes and audit declarations used in a theorem type."""

import unittest

import test_final_local_lemmas as fixtures
import test_lean_flow as database


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanSourceScopingTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.external = self.case.fixture.workspace.parent

    def check_source(self, anchor, declaration, source, *, expect=0):
        path = self.external / "Ordinary.lean"
        path.write_text(source, encoding="utf-8")
        return self.case.cli("lean", "check-source", anchor["id"], path, "--declaration", declaration, expect=expect)

    def test_choose_one_namespaced_theorem_with_independently_audited_local_helpers(self):
        choices = [
            {"kind": "hypothesis", "natural": "Truth is given.",
             "formal": {"lean": {"proposition": "True"}}},
        ]
        anchor, _, _ = self.case.anchor("namespaced-native-proof", "True ∧ True", choices)
        source = """import Lean
namespace ManyProofs
theorem helper : True := True.intro
theorem another : True := (fun (_ : (0 : Nat) ≤ 0) => True.intro) (Nat.le_refl 0)
section Goal
open Nat
variable (n : Nat)
theorem reflexive : n = n := rfl
open Nat in
theorem checked (h : True) : True ∧ True := ⟨h, helper⟩
end Goal
end ManyProofs
"""
        result = self.check_source(anchor, "ManyProofs.checked", source)
        self.assertTrue(result["accepted"] and result["kernel_rechecked"])
        self.assertIn("ManyProofs.helper", result["dependencies"]["direct_constants"])
        self.assertNotIn("ManyProofs.another", result["dependencies"]["constants"])
        self.assertNotIn("Nat.le_refl", result["dependencies"]["constants"])
        rejected = self.check_source(anchor, "ManyProofs.checked", source.replace("⟨h, helper⟩", "⟨h, another⟩"),
                                     expect=2)
        self.assertTrue(rejected["scope_matches"])
        self.assertIn("Nat.le_refl", rejected["dependencies"]["unapproved"])
        absent = self.check_source(anchor, "checked", source, expect=2)
        self.assertIn("selected fully qualified theorem is not declared", absent)
        duplicate = source + "namespace ManyProofs\ntheorem checked : True := True.intro\nend ManyProofs\n"
        self.assertIn("more than once", self.check_source(anchor, "ManyProofs.checked", duplicate, expect=2))
        effect = self.external / "side-effect"
        wrapped = source + f'open Nat in\n#eval IO.FS.writeFile "{effect}" "ran"\n'
        disallowed = self.check_source(anchor, "ManyProofs.checked", wrapped, expect=2)
        self.assertIn("supported scoping commands", disallowed)
        self.assertFalse(effect.exists())

    def test_unapproved_core_fact_in_definitional_equal_declaration_type_is_reported(self):
        frozen = "(⟨0, Nat.le.refl⟩ : {n : Nat // n ≤ n}) = ⟨0, Nat.le.refl⟩"
        anchor, _, _ = self.case.anchor("native-proof-declaration-type-input", frozen, [])
        result = self.check_source(anchor, "checked", """import Lean
theorem checked :
    (⟨0, Nat.le_refl 0⟩ : {n : Nat // n ≤ n}) = ⟨0, Nat.le_refl 0⟩ :=
  @Eq.refl {n : Nat // n ≤ n} ⟨0, Nat.le.refl⟩
""", expect=2)
        self.assertTrue(result["scope_matches"] and result["kernel_rechecked"])
        dependencies = result["dependencies"]
        self.assertNotIn("Nat.le_refl", dependencies["direct_constants"])
        self.assertIn("Nat.le_refl", dependencies["type_constants"])
        self.assertIn("Nat.le_refl", dependencies["unapproved"])
        self.assertEqual(self.case.cli("lean", "show", anchor["id"])["checks"], [])
