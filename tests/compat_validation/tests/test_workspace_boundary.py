"""Verify that user-facing paths stay within the selected project."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import research_multiplier
from research_multiplier.storage import paths


class WorkspaceBoundaryTests(unittest.TestCase):
    def setUp(self):
        location = TemporaryDirectory()
        self.addCleanup(location.cleanup)
        self.root = Path(location.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("RM_")}
        self.env.update(HOME=str(self.home), USERPROFILE=str(self.home), LOCALAPPDATA=str(self.home),
                        XDG_DATA_HOME=str(self.home), PYTHONPATH=str(Path(research_multiplier.__file__).resolve().parents[1]))
        self.input = self.root / "problem.json"
        self.input.write_text(json.dumps({"kind": "open", "title": "Boundary fixture", "question": "Which path?",
                                          "success": "One checked destination."}), encoding="utf-8")

    def cli(self, *arguments, expect=0, cwd=None, env=None):
        result = subprocess.run([sys.executable, "-m", "research_multiplier", *map(str, arguments)],
                                cwd=cwd or self.root, env={**self.env, **(env or {})}, capture_output=True,
                                text=True, check=False, timeout=20)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return result

    def workspace(self, name):
        location = self.root / name
        self.cli("research", "init-workspace", location)
        return location

    def test_missing_selection_stops_before_external_writes_or_model(self):
        output = self.root / "unselected output"
        error = self.cli("run", self.input, "--out", output, "--model", "unused", "--base-url",
                         "http://127.0.0.1:9/v1", expect=2)
        self.assertIn("research init-workspace PATH", error.stderr)
        self.cli("run", self.input, expect=2, env={"RM_DATA_ROOT": str(output)})
        self.assertFalse(output.exists())
        self.assertEqual(list(self.home.iterdir()), [])
        self.cli("--help")

    def test_initialized_selection_uses_categories_and_standalone_record_can_replay(self):
        workspace = self.workspace("selected project")
        self.assertIsNotNone(paths.workspace_id(workspace))
        draft = workspace / "inputs" / "draft.json"
        self.cli("--workspace", workspace, "init", draft)
        self.assertEqual(json.loads(draft.read_bytes())["kind"], "open")
        self.cli("--workspace", workspace, "init", workspace / "draft.json", expect=2)
        self.cli("run", self.input, cwd=workspace / "inputs")
        record, = (workspace / "runs" / "offline").glob("run-*/run.json")
        self.cli("replay", record)
        self.cli("--workspace", workspace, "run", self.input, "--out", workspace / "runs" / "lean", expect=2)
        self.cli("--workspace", workspace, "lean", "prepare")
        self.assertTrue((workspace / "lean-project" / "lean-toolchain").is_file())

    def test_a_different_project_is_rejected_for_reads_writes_and_objects(self):
        selected = self.workspace("selected")
        other = self.workspace("other")
        other_input = other / "inputs" / "problem.json"
        other_input.write_bytes(self.input.read_bytes())
        for arguments in (("run", other_input), ("run", self.input, "--out", other / "runs" / "offline")):
            error = self.cli("--workspace", selected, *arguments, expect=2)
            self.assertIn("different research workspace", error.stderr)
        error = self.cli("--workspace", selected, "run", self.input, expect=2,
                         env={"RM_ARTIFACT_ROOT": str(other / "objects")})
        self.assertIn("objects/", error.stderr)
        self.assertEqual(list((other / "runs" / "offline").iterdir()), [])

    def test_symlink_cannot_redirect_a_run_outside_the_project(self):
        selected = self.workspace("selected")
        external = self.root / "external"
        external.mkdir()
        alias = selected / "runs" / "offline" / "linked"
        try:
            alias.symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable for this test user.")
        self.cli("--workspace", selected, "run", self.input, "--out", alias, expect=2)
        self.assertEqual(list(external.iterdir()), [])
        leaf = selected / "lean-project" / "lean-toolchain"
        leaf.symlink_to(external / "toolchain")
        self.cli("--workspace", selected, "lean", "prepare", expect=2)
        self.assertEqual(list(external.iterdir()), [])

    def test_library_and_cli_share_selected_workspace_write_boundary(self):
        selected = self.workspace("selected")
        other = self.workspace("other")
        with paths.use_workspace(selected):
            allowed = selected / "runs" / "offline" / "result.json"
            self.assertEqual(paths.validate_project_write(allowed), allowed)
            with self.assertRaisesRegex(ValueError, "different research workspace"):
                paths.validate_project_write(other / "runs" / "offline" / "result.json")
            self.assertEqual(paths.shared_artifacts(), selected / "objects")
        with paths.use_workspace(selected, strict=True):
            with self.assertRaisesRegex(ValueError, "different research workspace"):
                paths.shared_artifacts(other / "runs" / "offline")

    def test_a_separate_program_checkout_is_also_rejected(self):
        program = self.root / "program"
        (program / "research_multiplier").mkdir(parents=True)
        (program / "pyproject.toml").touch()
        (program / "research_multiplier" / "paths.py").touch()
        target = program / "research data"
        self.cli("research", "init-workspace", target, expect=2)
        self.assertFalse(target.exists())
