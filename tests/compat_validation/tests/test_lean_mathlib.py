"""Check native Mathlib manifest and captured artifacts without Lean installed."""

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_multiplier import lean_mathlib
from research_multiplier.proof.lean_runner import TOOLCHAIN


class MathlibProjectTests(unittest.TestCase):
    def test_validates_manifest_and_safe_module_names(self):
        manifest = {"format_version": 1, "project_sha256": "a" * 64, "modules": ["Research.Theorems"]}
        self.assertEqual(lean_mathlib.validate_manifest(manifest), manifest)
        for fields in ({"project_sha256": "a" * 63}, {"format_version": True},
                       {"modules": ["Research\n#eval IO.println"]},
                       {"modules": ["Mathlib", "RMExpected.Bad"]},
                       {"modules": ["Mathlib", "Mathlib"]}, {"modules": []}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                lean_mathlib.validate_manifest({**manifest, **fields})

    def prepare(self, root):
        executable = root / "toolchain" / "bin" / "lean"
        executable.parent.mkdir(parents=True)
        executable.touch()
        core = executable.parent.parent / "lib" / "lean"
        core.mkdir(parents=True)
        (core / "Lean.olean").write_bytes(b"core")
        project = root / "project"
        module = project / ".lake" / "build" / "lib" / "lean" / "Mathlib" / "Fixture.olean"
        module.parent.mkdir(parents=True)
        module.write_bytes(b"the exact compiled file")
        (project / "lean-toolchain").write_text(TOOLCHAIN + "\n", encoding="utf-8")
        (project / "lake-manifest.json").write_text('{"packages":[]}', encoding="utf-8")
        return executable, core / "Lean.olean", project, module

    def test_freezes_compiled_transitive_content_and_rejects_changes(self):
        with TemporaryDirectory() as directory:
            executable, core, project, module = self.prepare(Path(directory))
            paths = {Path("Mathlib/Fixture.olean"): module, Path("Lean.olean"): core}
            with patch.object(lean_mathlib, "_closure", return_value=paths):
                first = lean_mathlib.LocalMathlib(project, ["Research.Module"], executable=executable)
                (module.parent / "Unrelated.olean").write_bytes(b"outside the import closure")
                unchanged = lean_mathlib.LocalMathlib(project, ["Research.Module"], executable=executable)
                self.assertEqual(first.manifest, unchanged.manifest)
                module.write_bytes(b"the revised compiled file")
                changed = lean_mathlib.LocalMathlib(project, ["Research.Module"], executable=executable)
            self.assertNotEqual(first.manifest["project_sha256"], changed.manifest["project_sha256"])
            with patch.object(lean_mathlib, "_run") as launched, self.assertRaisesRegex(ValueError, "changed before"):
                first.run({"Audit.lean": b"script"}, [["--run", "Audit.lean"]])
            launched.assert_not_called()

    def test_runs_from_temporary_closure_without_ambient_config(self):
        with TemporaryDirectory() as directory:
            executable, core, project, module = self.prepare(Path(directory))
            with patch.object(lean_mathlib, "_closure", return_value={
                Path("Mathlib/Fixture.olean"): module, Path("Lean.olean"): core,
            }):
                backend = lean_mathlib.LocalMathlib(project, ["Research.Module"], executable=executable)

            def run(binary, cwd, env, command, timeout):
                self.assertEqual(binary, executable.resolve())
                self.assertTrue(cwd.is_dir())
                self.assertFalse(cwd.is_relative_to(project))
                self.assertFalse({"RM_VERIFIER_DSN", "LEAN_SRC_PATH"} & env.keys())
                self.assertEqual(Path(env["HOME"]), cwd)
                work, library = map(Path, env["LEAN_PATH"].split(os.pathsep))
                self.assertEqual(work, cwd)
                self.assertEqual((library / "Mathlib/Fixture.olean").read_bytes(), module.read_bytes())
                self.assertEqual((cwd / "Audit.lean").read_bytes(), b"script")
                self.assertGreater(timeout, 0)
                text = "Lean (version 4.19.0, native, commit 6caaee842e94, Release)"
                if command != ["--version"]:
                    text = '{"verdict":"checked"}'
                return subprocess.CompletedProcess(command, 0, text, "")

            with patch.dict(os.environ, {"RM_VERIFIER_DSN": "ignored", "LEAN_SRC_PATH": "ambient"}), \
                    patch.object(lean_mathlib, "_run", side_effect=run):
                self.assertEqual(backend.run({"Audit.lean": b"script"}, [["--run", "Audit.lean"]]),
                                 (0, '{"verdict":"checked"}'))
