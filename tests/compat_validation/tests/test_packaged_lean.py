"""Exercise pinned Lean source resolution from the checkout and installed CLI."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import research_multiplier
from research_multiplier.proof import packaged_lean
from research_multiplier.storage import paths
from research_multiplier.proof.lean_runner import digest


CHECKSUMS = packaged_lean.SETUP_SOURCES
MANIFEST = "lake-manifest.json"
PINNED_LEAN, _ = packaged_lean.read()
CASES_DIR = os.environ.get("RM_TEST_CASES_DIR")


class PackagedLeanTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.workspace = paths.initialize_workspace(self.root / "project")
        self.output = self.workspace / "lean-project"

    def prepare(self, output, source=None):
        package_root = Path(research_multiplier.__file__).resolve().parents[1]
        command = [sys.executable, "-m", "research_multiplier", "--workspace", str(self.workspace), "lean", "prepare"]
        if source is not None:
            command.append(str(source))
        command.extend(["--project-dir", str(output)])
        return subprocess.run(command, cwd=self.root, env={**os.environ, "PYTHONPATH": str(package_root)},
                              capture_output=True, text=True, timeout=20)

    def copy_sources(self, output):
        for name in CHECKSUMS:
            destination = output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((PINNED_LEAN / name).read_bytes())

    def assert_pinned_project(self, prepared, output, checksums=CHECKSUMS):
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(Path(json.loads(prepared.stdout)["project_dir"]), output)
        actual = {path.relative_to(output).as_posix(): digest(path.read_bytes())
                  for path in output.rglob("*") if path.is_file()}
        self.assertEqual(actual, checksums)

    def test_default_prepare_ignores_unrelated_lean_directory(self):
        shadow = self.root / "lean"
        self.copy_sources(shadow)
        (shadow / MANIFEST).write_bytes(b"unreviewed Lake lockfile\n")
        output = self.output
        self.assert_pinned_project(self.prepare(output), output)
        self.assert_pinned_project(self.prepare(output), output)

    def test_explicit_source_is_checked_without_fallback(self):
        source = self.root / "my-reviewed-source"
        self.copy_sources(source)
        output = self.output
        self.assert_pinned_project(self.prepare(output, source), output)
        (source / MANIFEST).write_bytes(b"changed after review\n")
        for selected, message in ((source, MANIFEST),
                                  (self.root / "missing", "Cannot read pinned Lean project input lean-toolchain")):
            with self.subTest(source=selected):
                failed_output = self.output / "explicit-failure-output"
                result = self.prepare(failed_output, selected)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(message, result.stderr)
                self.assertFalse(failed_output.exists())

    def test_default_skips_a_candidate_with_the_wrong_pinned_bytes(self):
        invalid = self.root / "candidate"
        self.copy_sources(invalid)
        (invalid / MANIFEST).write_bytes(b"wrong lockfile\n")
        with patch.object(packaged_lean, "_candidate_roots", return_value=iter((invalid, PINNED_LEAN))):
            directory, contents = packaged_lean.read()
        self.assertEqual(directory, PINNED_LEAN)
        self.assertEqual({name: digest(content) for name, content in contents.items()}, CHECKSUMS)

    @unittest.skipUnless(CASES_DIR, "set RM_TEST_CASES_DIR to the external flow case bundle")
    def test_legacy_project_requires_an_explicit_source(self):
        legacy = Path(CASES_DIR) / "flow" / "lean"
        output = self.output
        self.assert_pinned_project(self.prepare(output, legacy), output, packaged_lean.LEGACY_SOURCES)
        with patch.object(packaged_lean, "_candidate_roots", return_value=iter((legacy, PINNED_LEAN))):
            directory, contents = packaged_lean.read()
        self.assertEqual(directory, PINNED_LEAN)
        self.assertEqual(set(contents), set(CHECKSUMS))
