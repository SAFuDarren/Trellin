import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import research_multiplier
from research_multiplier import models
from research_multiplier.storage import paths
from research_multiplier.workflows import workflow
from research_multiplier.storage.artifacts import Artifacts


def problem(kind="open", **fields):
    return {"kind": kind, "title": "Test question", "question": "Does the supplied input meet its constraints?",
            "success": "Save a reproducible decision.", **fields}


class CheckTests(unittest.TestCase):
    def test_coloring_certificate_and_refutation(self):
        edges = [["p", "q"], ["q", "r"], ["r", "p"]]
        graph = problem("graph-coloring", data={"vertices": ["p", "q", "r"], "edges": edges, "colors": 2})
        self.assertEqual(workflow.check(graph)["status"], "refuted")

        graph["data"]["colors"] = 3
        result = workflow.check(graph)
        self.assertEqual(result["status"], "verified")
        coloring = result["evidence"]["coloring"]
        self.assertEqual(set(coloring), {"p", "q", "r"})
        for left, right in edges:
            self.assertNotEqual(coloring[left], coloring[right])
        edges.append(["p", "p"])
        self.assertEqual(workflow.check(graph)["evidence"], {"self_loop": "p"})

    def test_lp_exact_feasibility_and_rejected_certificate(self):
        data = {"A": [[2]], "b": [1], "c": ["1/3"], "primal": ["1/2"], "dual": ["1/6"]}
        linear = problem("linear-program", data=data)
        result = workflow.check(linear)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["evidence"]["primal_objective"], "1/6")
        data["dual"] = ["1/8"]
        self.assertEqual(workflow.check(linear)["status"], "certificate_rejected")
        data["primal"] = [0.5]
        with self.assertRaisesRegex(ValueError, "exact numbers"):
            workflow.check(linear)

    def test_replay_checks_snapshot_and_result(self):
        record = workflow.start(problem())
        self.assertTrue(workflow.replay(record))
        self.assertEqual(record["result"]["status"], "needs_review")
        record["result"]["status"] = "verified"
        self.assertFalse(workflow.replay(record))
        record["problem"]["question"] = "Changed question"
        with self.assertRaisesRegex(ValueError, "hash"):
            workflow.replay(record)

    def test_public_cli_outputs_and_preserves_existing_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root = paths.initialize_workspace(Path(directory))
            question = root / "inputs" / "my question.json"
            output = root / "runs" / "offline"
            command = [sys.executable, "-m", "research_multiplier", "--workspace", str(root)]
            subprocess.run(command + ["init", str(question)], check=True, capture_output=True)
            template = json.loads(question.read_bytes())
            self.assertEqual(template["kind"], "open")
            self.assertEqual([template[field] for field in ("title", "question", "success")], ["", "", ""])
            question.write_text(
                json.dumps(problem(question="重复测量能否减少误差？"), ensure_ascii=False),
                encoding="utf-8",
            )
            duplicate = subprocess.run(command + ["init", str(question)], capture_output=True)
            self.assertEqual(duplicate.returncode, 2)
            subprocess.run(command + ["run", str(question), "--out", str(output)], check=True, capture_output=True)
            path, = output.iterdir()
            self.assertIn("重复测量能否减少误差？", (path / "report.md").read_text(encoding="utf-8"))
            subprocess.run(command + ["replay", str(path / "run.json")], check=True, capture_output=True)
            colored = root / "inputs" / "graph.json"
            subprocess.run(command + ["init", str(colored), "--kind", "graph-coloring"], check=True, capture_output=True)
            self.assertEqual(json.loads(colored.read_bytes())["data"], {"vertices": [], "edges": [], "colors": None})
            legacy = root / "inputs" / "legacy.json"
            subprocess.run(command + ["init", str(legacy), "--example", "open-question"], check=True, capture_output=True)
            self.assertEqual(json.loads(legacy.read_bytes()), template)

    def test_demo_requires_an_explicit_external_case_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            launch = root / "launch"
            external = root / "case bundle"
            workspace = paths.initialize_workspace(root / "project")
            case = problem(title="External case input")
            for folder in (launch, external):
                (folder / "offline").mkdir(parents=True)
                (folder / "offline" / "open-question.json").write_text(json.dumps(case), encoding="utf-8")
            command = [sys.executable, "-m", "research_multiplier", "--workspace", str(workspace),
                       "demo", "open-question"]
            env = {key: value for key, value in os.environ.items() if key != "RM_CASES_DIR"}
            env["PYTHONPATH"] = str(Path(research_multiplier.__file__).resolve().parents[1])
            missing = subprocess.run(command, cwd=launch, env=env, capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("--cases-dir or RM_CASES_DIR", missing.stderr)
            for index, (arguments, configured_case_dir) in enumerate([
                (["--cases-dir", str(external)], str(root / "invalid folder")),
                ([], str(external)),
            ]):
                output = workspace / "runs" / "offline" / str(index)
                subprocess.run(command + arguments + ["--out", str(output)], cwd=launch,
                               env={**env, "RM_CASES_DIR": configured_case_dir}, check=True, capture_output=True)
                run, = output.iterdir()
                self.assertEqual(json.loads((run / "run.json").read_bytes())["problem"], case)

    def test_default_output_uses_runtime_root_independently_of_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            launch_directory = root / "checkout"
            launch_directory.mkdir()
            data = paths.initialize_workspace(root / "research data")
            env = os.environ.copy()
            env["RM_DATA_ROOT"] = str(data)
            env["PYTHONPATH"] = str(Path(research_multiplier.__file__).resolve().parents[1])
            command = [sys.executable, "-m", "research_multiplier"]
            source = root / "input.json"
            source.write_text(json.dumps(problem()), encoding="utf-8")
            located = subprocess.check_output(command + ["research", "paths"], cwd=launch_directory, env=env)
            self.assertEqual(Path(json.loads(located)["runs"]), data / "runs")
            subprocess.run(command + ["run", str(source)], cwd=launch_directory, env=env, check=True, capture_output=True)
            run, = (data / "runs" / "offline").iterdir()
            self.assertTrue((run / "run.json").is_file())
            self.assertEqual(list(launch_directory.iterdir()), [])

    def test_named_workspace_keeps_two_projects_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project_a, project_b = root / "First project", root / "Second project"
            env = {key: value for key, value in os.environ.items()
                   if key not in {"RM_DATA_ROOT", "RM_ARTIFACT_ROOT"}}
            env["PYTHONPATH"] = str(Path(research_multiplier.__file__).resolve().parents[1])
            command = [sys.executable, "-m", "research_multiplier"]
            for project in (project_a, project_b):
                subprocess.check_output(command + ["research", "init-workspace", str(project)], cwd=root, env=env)
                self.assertTrue((project / "inputs").is_dir())
                self.assertTrue((project / "backups").is_dir())
            selected = ["--workspace", str(project_b)]
            source = project_b / "inputs" / "question.json"
            source.write_text(json.dumps(problem()), encoding="utf-8")
            subprocess.run(command + selected + ["run", str(source)], cwd=project_a / "inputs", env=env,
                           check=True, capture_output=True)
            dispatch = command + ["research", "paths"]
            automatic = json.loads(subprocess.check_output(dispatch, cwd=project_a / "inputs", env=env))
            self.assertEqual(Path(automatic["objects"]), project_a / "objects")
            self.assertFalse(any(path.is_file() for path in (project_a / "runs").rglob("*")))
            self.assertEqual(len(list((project_b / "runs" / "offline").iterdir())), 1)

    def test_explicit_old_run_resolves_its_project_object_store(self):
        with tempfile.TemporaryDirectory() as directory:
            project = paths.initialize_workspace(Path(directory) / "project")
            other = paths.initialize_workspace(Path(directory) / "other")
            run = project / "runs" / "claims" / "claim"
            with patch.dict(os.environ, {"RM_DATA_ROOT": "", "RM_ARTIFACT_ROOT": ""}):
                with paths.use_workspace(project):
                    reference = Artifacts(run / "artifacts").put_bytes(b"research data")
                self.assertTrue((project / "objects" / reference["sha256"]).is_file())
                self.assertEqual(Artifacts(run / "artifacts").read(reference), b"research data")
                with paths.use_workspace(other):
                    self.assertEqual(Artifacts(run / "artifacts").read(reference), b"research data")


class ModelTests(unittest.TestCase):
    def test_loopback_request_keeps_model_output_separate(self):
        calls = []

        class ChatEndpoint(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append((self.path, request, self.headers.get("Authorization")))
                body = json.dumps({
                    "model": "local-test",
                    "choices": [{"message": {"content": "Compare the two methods on paired trials."}}],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), ChatEndpoint) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_port}/v1"
                with tempfile.TemporaryDirectory() as directory:
                    project = paths.initialize_workspace(Path(directory))
                    output = project / "runs" / "offline"
                    source = project / "inputs" / "problem.json"
                    source.write_text(json.dumps(problem()), encoding="utf-8")
                    with patch.dict(os.environ, {
                        "RESEARCH_MULTIPLIER_API_KEY": "new-test-key",
                        "RESEARCH_WORKBENCH_API_KEY": "old-test-key",
                    }):
                        run = subprocess.run([
                            sys.executable, "-m", "research_multiplier", "--workspace", str(project), "run", str(source),
                            "--model", "requested", "--base-url", base_url, "--out", str(output),
                        ], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    path, = output.iterdir()
                    saved = json.loads((path / "run.json").read_text(encoding="utf-8"))
                    self.assertEqual(saved["result"]["status"], "needs_review")
                    self.assertIn("paired trials", (path / "model-suggestions.md").read_text(encoding="utf-8"))
            finally:
                server.shutdown()
                worker.join()
        request_path, request, authorization = calls[0]
        self.assertEqual(authorization, "Bearer new-test-key")
        self.assertEqual(request_path, "/v1/chat/completions")
        self.assertEqual(request["model"], "requested")
        self.assertEqual(json.loads(request["messages"][1]["content"])["recorded_check"]["status"], "needs_review")

    def test_remote_http_endpoint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            models.suggest({}, {}, "fake", "http://example.org/v1")


if __name__ == "__main__":
    unittest.main()
