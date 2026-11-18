"""Keep one discovered Mathlib environment while rechecking its artifacts per proof."""

import os
import shutil
import subprocess
import unittest
from unittest.mock import patch
from uuid import uuid4

from research_multiplier import general_lean, lean_mathlib, local_lemmas

import test_final_local_lemmas as final_fixtures
import test_lean_flow as lean_fixtures


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class MathlibPlanRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.case = final_fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.project = self.case.fixture.output / "research-lean"
        build = self.project / ".lake" / "build" / "lib" / "lean"
        build.mkdir(parents=True)
        (self.project / ".lake" / "packages").symlink_to(
            lean_fixtures.PROJECT / ".lake" / "packages", target_is_directory=True)
        for name in ("lean-toolchain", "lake-manifest.json", "lakefile.toml"):
            shutil.copyfile(lean_fixtures.PROJECT / name, self.project / name)
        module = "RMPlanRuntime"
        source = self.project / f"{module}.lean"
        self.compiled = build / f"{module}.olean"
        self.changed = self.project / "changed.olean"
        executable = general_lean._executable(None, self.project)
        env = lean_mathlib._environment(executable, self.project, os.pathsep.join(map(
            str, lean_mathlib._build_roots(self.project))))
        for index, filename in enumerate((self.compiled, self.changed)):
            source.write_text(f"import Mathlib.Data.Real.Basic\ndef planMarker : Nat := {index}\n", encoding="utf-8")
            subprocess.run([str(executable), "-o", str(filename), source.name], cwd=self.project,
                           env=env, check=True, capture_output=True, timeout=30)
        label = f"One Mathlib plan environment {uuid4()}"
        with patch.object(lean_fixtures, "PROJECT", self.project):
            self.anchor, self.scope, _ = self.case.anchor(
                label, "((True ∧ True) ∧ True) ∧ True",
                [{"kind": "hypothesis", "natural": f"The reviewed premise for {label} is True.",
                  "formal": {"lean": {"proposition": "True"}}}], module=module)
        def and_(left, right):
            return {"and": [left, right]}

        hypothesis = {"use": 0}
        self.ordinary_goal = final_fixtures.intro(
            1, and_(and_(and_(hypothesis, hypothesis), hypothesis), hypothesis))
        first = final_fixtures.apply({"node_ref": "L1"}, hypothesis)
        second = final_fixtures.apply({"node_ref": "L2"}, hypothesis)
        self.plan = {
            "format_version": 1, "nodes": [
                {"name": "L1", "proposition": "True ∧ True",
                 "proof": final_fixtures.intro(1, and_(hypothesis, hypothesis))},
                {"name": "L2", "proposition": "(True ∧ True) ∧ True",
                 "proof": final_fixtures.intro(1, and_(first, hypothesis))},
            ],
            "goal_proof": final_fixtures.intro(1, and_(second, hypothesis)),
        }

    def derive(self):
        return local_lemmas.derive(self.anchor["id"], self.plan, mathlib_project=self.project,
                                   output=self.case.fixture.output)

    def test_discovers_once_and_revalidates_a_cached_final(self):
        with patch.dict(os.environ, self.case.fixture.env), \
                patch.object(lean_mathlib, "_closure", wraps=lean_mathlib._closure) as discoveries, \
                patch.object(lean_mathlib.LocalMathlib, "run", autospec=True,
                             side_effect=lean_mathlib.LocalMathlib.run) as checks:
            result = self.derive()
            self.assertEqual(discoveries.call_count, 1)
            self.assertEqual(checks.call_count, 3)
            self.assertEqual(result["goal"]["local_lemma_ids"], [str(result["lemmas"][1]["id"])])
            discoveries.reset_mock()
            checks.reset_mock()

            recovered = self.derive()
            self.assertEqual(recovered["goal"]["evidence_id"], result["goal"]["evidence_id"])
            self.assertEqual(discoveries.call_count, 1)
            checks.assert_not_called()
            discoveries.reset_mock()

            prior = general_lean.check(
                self.anchor["id"], {"format_version": 1, "proof": self.ordinary_goal},
                self.case.fixture.output, mathlib_project=self.project)
            discoveries.reset_mock()
            checks.reset_mock()
            with patch.dict(self.plan, {"goal_proof": self.ordinary_goal}):
                ordinary = self.derive()
            self.assertTrue(ordinary["goal"]["kernel_rechecked"])
            self.assertNotIn("local_lemma_ids", ordinary["goal"])
            self.assertEqual(ordinary["goal"]["evidence_id"], prior["evidence_id"])
            self.assertEqual(discoveries.call_count, 1)
            checks.assert_not_called()
            discoveries.reset_mock()
            checks.reset_mock()

            derive_node = local_lemmas._derive

            def change_before_recovered_final(*args, **kwargs):
                row = derive_node(*args, **kwargs)
                if row["id"] == result["lemmas"][1]["id"]:
                    self.changed.replace(self.compiled)
                return row

            with patch.object(local_lemmas, "_derive", side_effect=change_before_recovered_final):
                with self.assertRaisesRegex(ValueError, "compiled Lean module changed"):
                    self.derive()
            self.assertEqual(discoveries.call_count, 1)
            checks.assert_not_called()

    def test_a_changed_module_stops_the_next_unchecked_lemma(self):
        derive_node = local_lemmas._derive

        def change_after_first_check(*args, **kwargs):
            row = derive_node(*args, **kwargs)
            self.changed.replace(self.compiled)
            return row

        with patch.dict(os.environ, self.case.fixture.env), \
                patch.object(lean_mathlib, "_closure", wraps=lean_mathlib._closure) as discoveries, \
                patch.object(local_lemmas, "_derive", side_effect=change_after_first_check):
            with self.assertRaisesRegex(ValueError, "Local theorem L2: .*Mathlib module changed"):
                self.derive()
        self.assertEqual(discoveries.call_count, 1)
        self.assertEqual(len(self.case.cli("lean", "local-theorems", self.anchor["id"])), 1)
        self.assertEqual(self.case.cli("research", "evidence", self.scope["claim_id"])["evidence"], [])
