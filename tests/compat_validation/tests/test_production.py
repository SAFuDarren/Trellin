"""Installed command and recovery against a disposable PostgreSQL database."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from research_multiplier.storage import paths
from research_multiplier import production
from research_multiplier.workflows import research_plans, research_progress
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store


DSN = os.environ.get("RM_TEST_DSN")
FLOW_INSTANCE = {
    "vertices": ["s", "u", "v", "t"],
    "source": "s",
    "sink": "t",
    "edges": [["s", "u", 12], ["s", "v", 17], ["u", "t", 12], ["v", "t", 11]],
    "target_value": 23,
}
PROOF = {
    "proof_markdown": "The displayed flow is feasible and has value 23. "
                      "The displayed cut has capacity 23 and bounds every feasible flow.",
    "certificate": {
        "flows": [["s", "u", 12], ["s", "v", 11], ["u", "t", 12], ["v", "t", 11]],
        "source_side": ["s", "u", "v"],
    },
}
ALIGNED_PROOF = {**PROOF, "basis_items": ["H1", "H2", "H3"]}
AUDIT = {"verdict": "verified", "diff_status": "exact", "reason": "Scope and proof checked."}


class ArtifactTests(unittest.TestCase):
    def test_interrupted_write_leaves_no_partial_digest_and_retries(self):
        with TemporaryDirectory() as root:
            artifacts = Artifacts(root)
            with patch("research_multiplier.storage.artifacts.os.fsync", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    artifacts.put("exact", raw=True)
            self.assertEqual(list(Path(root).iterdir()), [])
            received = artifacts.put("exact", raw=True)
            self.assertEqual(artifacts.read(received), b"exact")
            self.assertEqual(artifacts.put("exact", raw=True), received)


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ProductionTests(unittest.TestCase):
    dsn = DSN

    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        from research_multiplier.storage.migrate import upgrade

        upgrade(cls.dsn)
        cls.connect = staticmethod(psycopg.connect)
        cls.env = {}
        suffix = uuid4().hex[:12]
        for group in ("human", "scheduler", "agent", "verifier"):
            role, password = f"rm_test_{group}_{suffix}", uuid4().hex
            with cls.connect(cls.dsn) as db:
                db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)))
                db.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(f"rm_{group}"), sql.Identifier(role)))
            cls.addClassCleanup(cls.drop_role, role)
            cls.env[f"RM_{group.upper()}_DSN"] = make_conninfo(cls.dsn, user=role, password=password)

    @classmethod
    def drop_role(cls, role):
        from psycopg import sql

        with cls.connect(cls.dsn) as db:
            db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def setUp(self):
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.workspace = paths.initialize_workspace(Path(root.name) / "project")
        self.env = {**self.env, "RM_DATA_ROOT": str(self.workspace)}
        with self.connect(self.dsn) as db:
            database, fixture_id = db.execute("SELECT * FROM rm.current_workspace_binding()").fetchone()
            if fixture_id is None:
                fixture_id = paths.ensure_workspace_id(self.workspace)
                db.execute("SELECT rm.bind_research_workspace(%s,%s)", (fixture_id, database))
            else:
                # This disposable test database backs one logical project across fixture runs.
                (self.workspace / paths.WORKSPACE_ID_FILE).write_text(str(fixture_id) + "\n", encoding="ascii")
            paths.bind_database(self.workspace, database)
        self.root = self.workspace / "runs" / "claims"
        self.root.mkdir(exist_ok=True)
        self.network_input = self.workspace / "inputs" / "flow.json"
        self.network_input.write_text(json.dumps(FLOW_INSTANCE), encoding="utf-8")
        self.calls = []
        self.model_inputs = []

    @contextmanager
    def models(self, proof=PROOF, audit=AUDIT):
        calls = self.calls
        inputs = self.model_inputs

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model = data["model"]
                calls.append(model)
                inputs.append(json.loads(data["messages"][-1]["content"]))
                result = proof if model == "test-prover" else audit
                if callable(result):
                    result = result()
                if isinstance(result, int):
                    self.send_error(result)
                    return
                body = json.dumps({"model": model, "choices": [
                    {"message": {"content": json.dumps(result)}, "finish_reason": "stop"},
                ]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/v1"
            env = {**self.env, "NO_PROXY": "*"}
            for role, family in (("PROVER", "fixed_proof_family"), ("AUDITOR", "fixed_audit_family")):
                prefix = f"RESEARCH_MULTIPLIER_{role}_"
                env.update({prefix + name: value for name, value in {
                    "PROVIDER": "openai-compatible", "BASE_URL": url,
                    "MODEL": f"test-{role.lower()}", "FAMILY": family,
                }.items()})
            try:
                with patch.dict(os.environ, env):
                    yield
            finally:
                server.shutdown()
                thread.join()

    def cli(self, *args, expect=0):
        entry = shutil.which("research-multiplier", path=str(Path(sys.executable).parent))
        command = [entry] if entry else [sys.executable, "-m", "research_multiplier"]
        if args[0] != "--workspace":
            command += ["--workspace", str(self.workspace)]
        result = subprocess.run([*command, *map(str, args)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def start(self, expect=0):
        self.cli("v0a", self.network_input, "--title", "Public flow fixture",
                 "--record-freeze-as", "test researcher", "--out", self.root, expect=expect)
        run, = self.root.iterdir()
        return run

    def record(self, directory):
        return json.loads((directory / "run.json").read_bytes())

    def draft_formalization(self, title, *, clarify=None):
        args = ("--clarify", clarify) if clarify else ()
        return json.loads(self.cli("formalization", "draft", self.network_input, "--title", title, *args).stdout)

    def confirm_formalization(self, draft, *, resolution=None):
        args = ("--resolve", resolution) if resolution else ()
        self.cli("formalization", "confirm", draft["id"], "--hash", draft["packet_sha256"],
                 "--version", draft["version"], "--actor", "test researcher", "--reason", "Checked scope", *args)

    def test_installed_cli_full_flow_and_human_decision(self):
        with self.models():
            directory = self.start()
            outcome = self.record(directory)
            evidence = outcome["evidence_id"]
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])
            self.assertEqual(outcome["human_review"], "pending")
            self.assertEqual(outcome["claim_status"], "open")
            self.assertEqual(json.loads((directory / "verification.json").read_bytes())["value"], 23)
            self.cli("replay", directory / "run.json")
            self.cli("promote-evidence", evidence, "--reason", "before human", expect=2)
            self.cli("review-evidence", evidence, "approve", "--actor", "test", "--reason", "checked")
            self.cli("promote-evidence", evidence, "--reason", "approved")
            self.cli("resume", directory)
            self.assertEqual(self.record(directory)["claim_status"], "proved")
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])

    def test_project_model_budget_has_one_shared_reservation_per_dispatch(self):
        from concurrent.futures import ThreadPoolExecutor
        from research_multiplier.model_adapters import ModelConfig, complete_structured
        from research_multiplier.models.model_errors import ModelFailure

        with self.models():
            self.cli("research", "model-budget", "limit", "0.50", "--actor", "researcher",
                     "--reason", "Bound the monthly fixture spending.")
            try:
                self.cli("research", "model-budget", "tariff", "PROVER", "0.25",
                         "--max-input-bytes", "4096", "--max-output-tokens", "64",
                         "--actor", "researcher", "--reason", "Maximum charge for one proof proposal.")
                config = ModelConfig.from_env("PROVER")
                with self.assertRaisesRegex(ModelFailure, "approved maximum charge"):
                    complete_structured(ModelConfig.from_env("AUDITOR"), "audit", "Review.", {}, 64, 10)
                with self.assertRaisesRegex(ModelFailure, "exceeds the approved model rate envelope"):
                    complete_structured(config, "prove", "Prove.", {}, 65, 10)
                calls = []

                def respond(endpoint, payload, headers, timeout):
                    calls.append(payload["model"])
                    return {"model": config.model, "choices": [
                        {"message": {"content": "{}"}, "finish_reason": "stop"},
                    ]}

                def propose(service):
                    try:
                        run = complete_structured(config, service, "Use the frozen fixture.", {}, 64, 10,
                                                  _post_override=respond)
                    except ModelFailure as error:
                        return error.code
                    return run.transcript["execution"]["project_budget_reservation"]

                with ThreadPoolExecutor(max_workers=3) as workers:
                    attempts = list(workers.map(propose, ("prove", "audit-shadow", "explore-goal")))
                self.assertEqual(attempts.count("project_budget_exhausted"), 1)
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(set(attempts)), 3)
                status = json.loads(self.cli("research", "model-budget", "status").stdout)
                self.assertEqual(status["month"]["reserved_usd"], "0.500000")
            finally:
                self.cli("research", "model-budget", "limit", "--unlimited", "--actor", "researcher",
                         "--reason", "Restore the fixture's default.")

    def test_exact_optimization_certificates_in_the_same_production_workflow(self):
        problems = (
            ("linear-program", {"A": [[1]], "b": [2], "c": [1], "target_value": 3},
             {"primal": [2], "dual": [1]}, {"primal": [2], "dual": [0]}, "refuted"),
            ("semidefinite-program",
             {"A": [[[1, 0], [0, 1]]], "b": [1], "C": [[1, 0], [0, 0]], "target_value": 1},
             {"primal": [[1, 0], [0, 0]], "dual": [1]}, {"primal": [[1, 1], [1, 0]], "dual": [1]}, "proved"),
        )
        for domain, problem, correct, wrong, final in problems:
            with self.subTest(domain=domain), patch.dict(os.environ, self.env):
                instance = self.root / (domain + ".json")
                witness = self.root / (domain + "-witness.json")
                instance.write_text(json.dumps(problem), encoding="utf-8")
                draft = json.loads(self.cli("formalization", "draft", instance, "--domain", domain,
                                            "--title", "Exact optimization fixture").stdout)
                self.confirm_formalization(draft)
                witness.write_text(json.dumps(wrong), encoding="utf-8")
                self.cli("formalization", "prove", draft["id"], "--out", self.root, "--certificate", witness,
                         expect=2)
                witness.write_text(json.dumps(correct), encoding="utf-8")
                with patch.dict(os.environ, {"RM_HUMAN_DSN": ""}):
                    outcome = json.loads(self.cli("formalization", "prove", draft["id"], "--out", self.root,
                                                  "--certificate", witness).stdout)
                receipt = outcome["evidence_id"]
                self.cli("review-evidence", receipt, "approve", "--actor", "test researcher",
                         "--reason", "The frozen optimum and exact witness agree with the receipt.")
                self.cli("promote-evidence", receipt, "--reason", "Apply the reviewed exact receipt.")
                evidence = json.loads(self.cli("research", "evidence", draft["claim_id"]).stdout)
                self.assertEqual(evidence["claim"][0]["status"], final)
                self.cli("replay", Path(outcome["directory"]) / "run.json")

    def test_named_workspace_detects_a_wrong_database(self):
        other = paths.initialize_workspace(self.workspace.parent / "other")
        paths.bind_database(other, uuid4())
        project = self.workspace
        with patch.dict(os.environ, self.env):
            recorded = json.loads(self.cli("--workspace", project, "research", "bind-workspace").stdout)
            self.assertEqual(paths.database_id(project), UUID(recorded["database_id"]))
            self.cli("--workspace", project, "research", "components")
            rejected = self.cli("--workspace", other, "research", "components", expect=2)
            self.assertIn("different research database", rejected.stderr)

    def test_registered_domains_issue_exact_proofs_and_refutations(self):
        graph = {"vertices": ["a", "b", "c"], "edges": [["a", "b"], ["b", "c"], ["a", "c"]]}
        network = {**FLOW_INSTANCE, "target_value": 24}
        cases = (
            ("graph-coloring", {**graph, "colors": 3}, "exact_certificate", "proved"),
            ("graph-coloring", {**graph, "colors": 2}, "counterexample", "refuted"),
            ("fixed-directed-max-flow", network, "counterexample", "refuted"),
        )
        input_file = self.root / "domain-input.json"
        with patch.dict(os.environ, self.env):
            for domain, data, kind, final_state in cases:
                with self.subTest(domain=domain, kind=kind):
                    input_file.write_text(json.dumps(data), encoding="utf-8")
                    draft = json.loads(self.cli(
                        "formalization", "draft", input_file, "--domain", domain, "--title", domain,
                    ).stdout)
                    self.confirm_formalization(draft)
                    self.cli("formalization", "prove", draft["id"], "--out", self.root)
                    directory = self.root / draft["claim_id"]
                    receipt = self.record(directory)["evidence_id"]
                    self.cli("replay", directory / "run.json")
                    with self.connect(self.dsn) as db:
                        row = db.execute("SELECT kind, audit_mode FROM rm.evidence WHERE id = %s", (receipt,)).fetchone()
                    self.assertEqual(row, (kind, "none"))
                    self.cli("promote-evidence", receipt, "--reason", "before review", expect=2)
                    self.cli("review-evidence", receipt, "approve", "--actor", "test", "--reason", "checked")
                    self.cli("promote-evidence", receipt, "--reason", "approved")
                    self.cli("resume", directory)
                    saved = self.record(directory)
                    self.assertEqual(saved["claim_status"], final_state)
                    self.assertEqual(saved["evidence_id"], receipt)

    def test_durable_plan_respects_priority_budget_and_human_review(self):
        with patch.dict(os.environ, self.env):
            packet = self.draft_formalization("Deterministic proof plan")
            self.confirm_formalization(packet)
            plan = research_plans.create({
                "title": "Check and review", "max_parallel": 2, "budget_seconds": 180,
                "steps": [
                    {"key": "proof", "action": "prove", "packet_id": packet["id"], "priority": 10},
                    {"key": "review", "action": "await-review", "packet_id": packet["id"], "depends_on": ["proof"]},
                    {"key": "record", "action": "snapshot", "packet_id": packet["id"], "depends_on": ["review"]},
                ],
            })
            plan_id = plan["id"]
            first = research_plans.work(plan_id, 1)
            by_key = {step["step_key"]: step for step in first["steps"]}
            self.assertEqual(by_key["proof"]["status"], "succeeded")
            self.assertEqual(by_key["review"]["status"], "waiting_review")
            self.assertEqual(by_key["record"]["status"], "waiting_dependency")
            self.assertEqual(research_plans.work(plan_id, 2)["attempts_started"], 0)
            receipt = by_key["proof"]["result"]["evidence_id"]
            self.cli("review-evidence", receipt, "approve", "--actor", "test", "--reason", "Checked")
            latest = research_plans.work(plan_id, 4)
            self.assertEqual(latest["attempts_started"], 2)
            self.assertTrue(all(step["status"] == "succeeded" for step in latest["steps"]))
            self.assertTrue((self.root / packet["claim_id"] / "run.json").exists())
            recorded = next(step for step in latest["steps"] if step["step_key"] == "record")
            self.assertEqual(recorded["result"]["status"], "open")
            self.cli("review-evidence", receipt, "return", "--actor", "test", "--reason", "Reopened review")
            returned = {step["step_key"]: step["status"] for step in research_plans.show(plan_id)["steps"]}
            self.assertEqual(returned, {"proof": "succeeded", "review": "needs_recheck", "record": "needs_recheck"})

    def test_signed_progress_worker_waits_resumes_and_measures_exact_flow(self):
        with patch.dict(os.environ, self.env):
            packet = self.draft_formalization("Automatically advance a permitted flow claim")
            self.confirm_formalization(packet)
            policy = research_progress.propose({
                "packet_id": packet["id"], "metric": "flow-interval-width",
                "actions": ["prove", "await-review", "snapshot"],
            })
            identity = policy["id"]
            self.assertEqual(research_progress.advance_cycle("combined", 5)["attempts_started"], 0)
            with self.assertRaisesRegex(ValueError, "policy hash"):
                research_progress.approve(identity, "0" * 64, "Test researcher", "Wrong revision")
            research_progress.approve(identity, policy["policy_sha256"], "Test researcher", "Approve exact scope")
            with patch.dict(os.environ, {"RM_HUMAN_DSN": "", "OPENAI_API_KEY": "model-must-not-be-used"}):
                work = research_progress.advance_cycle("combined", 5)
            self.assertEqual(work["attempts_started"], 1)
            self.assertFalse(work["errors"], work)
            report = research_progress.status(identity)
            self.assertEqual(report["status"]["state"], "waiting_review")
            self.assertEqual(report["status"]["progress"], {"lower": 0, "upper": 29, "width": 29})
            receipt = report["status"]["current_evidence_id"]
            self.assertIsNone(report["status"]["best_evidence_id"])
            self.assertEqual(research_progress.advance_cycle("combined", 5)["attempts_started"], 0)
            with Store() as store:
                with self.assertRaisesRegex(ValueError, "permission denied"):
                    store.query("scheduler", "SELECT rm.record_research_progress(%s)", identity)
            self.cli("review-evidence", receipt, "approve", "--actor", "Test researcher", "--reason", "Scope verified")
            with patch.dict(os.environ, {"RM_HUMAN_DSN": "", "RM_VERIFIER_DSN": ""}):
                work = research_progress.advance_cycle("scheduler", 5)
            self.assertEqual(work["attempts_started"], 2)
            self.assertFalse(work["errors"], work)
            self.assertEqual(research_progress.status(identity)["status"]["state"], "waiting_verifier")
            observer = research_progress.propose({"packet_id": packet["id"], "metric": "exact-closure", "actions": []})
            research_progress.approve(observer["id"], observer["policy_sha256"], "Test researcher", "Observe the receipt")
            with patch.dict(os.environ, {"RM_HUMAN_DSN": "", "RM_SCHEDULER_DSN": "", "RM_AGENT_DSN": ""}):
                observed = research_progress.advance_cycle("verifier", 5, [identity])
            self.assertEqual(observed["observations"], 1)
            self.assertFalse(observed["errors"], observed)
            self.assertEqual(research_progress.status(observer["id"])["status"]["state"], "waiting_verifier")
            research_progress.stop(observer["id"], "Verification scope checked.")
            report = research_progress.status(identity)
            self.assertEqual(report["status"]["state"], "resolved")
            self.assertEqual(report["status"]["disposition"], "proved")
            self.assertEqual(report["status"]["best_evidence_id"], receipt)
            self.assertEqual(report["status"]["progress"], {"lower": 23, "upper": 23, "width": 0, "initial_width": 29})
            self.assertEqual(len(report["observations"]), 1)
            self.assertEqual(research_progress.advance_cycle("combined", 5)["observations"], 0)
            premise = next(item["id"] for item in json.loads(self.cli("research", "basis", packet["id"]).stdout)
                           if item["label"] == "H1")
            self.cli("research", "retire-item", premise, "--reason", "Withdraw this frozen definition")
            stale = research_progress.status(identity)
            self.assertIsNone(stale["status"]["best_evidence_id"])
            self.assertEqual(stale["status"]["state"], "scope_unavailable")
            self.assertEqual(stale["status"]["progress"]["width"], 29)
            self.assertEqual(len(stale["observations"]), 1)
            self.assertEqual(research_progress.advance_cycle("combined", 5)["attempts_started"], 0)
            research_progress.stop(identity, "Program follow-up finished")
            self.assertEqual(research_progress.status(identity)["status"]["state"], "stopped")

    def test_advance_reports_negative_resolution_without_promoting_the_claim(self):
        with patch.dict(os.environ, self.env):
            spec = {**FLOW_INSTANCE}
            spec["target_value"] = 22
            wrong = self.root / "wrong-target.json"
            wrong.write_text(json.dumps(spec), encoding="utf-8")
            packet = json.loads(self.cli("formalization", "draft", wrong, "--title", "An incorrect target").stdout)
            self.confirm_formalization(packet)
            policy = research_progress.propose({
                "packet_id": packet["id"], "metric": "exact-closure",
                "actions": ["prove", "await-review", "snapshot"],
            })
            identity = policy["id"]
            research_progress.approve(identity, policy["policy_sha256"], "Test researcher", "Check exact target")
            self.assertFalse(research_progress.advance_cycle("combined", 5)["errors"])
            verified = research_progress.status(identity)["status"]
            self.assertEqual(verified["state"], "waiting_review")
            self.assertEqual(verified["progress"], {"resolved": False})
            self.cli("review-evidence", verified["current_evidence_id"], "approve", "--actor", "Test researcher",
                     "--reason", "Verified a refutation of the stated value")
            self.assertFalse(research_progress.advance_cycle("combined", 5)["errors"])
            result = research_progress.status(identity)["status"]
            self.assertEqual(result["state"], "resolved")
            self.assertEqual(result["disposition"], "refuted")
            with Store() as store:
                claim, = store.query("verifier", "SELECT status FROM rm.claim_foundation_status WHERE id=%s",
                                     result["claim_id"])
            self.assertEqual(claim["status"], "open")

    def test_durable_plan_rejects_cycles_and_fences_capacity_and_cancellation(self):
        with patch.dict(os.environ, self.env):
            packet = self.draft_formalization("Schedule snapshot fixtures")
            self.confirm_formalization(packet)
            steps = [
                {"key": key, "action": "snapshot", "packet_id": packet["id"], "priority": priority}
                for key, priority in (("low", 2), ("high", 30), ("middle", 9))
            ]
            request = {"title": "Priority order", "max_parallel": 2, "budget_seconds": 180, "steps": steps}
            with self.assertRaisesRegex(ValueError, "acyclic"):
                research_plans.create({**request, "steps": [
                    {**steps[0], "depends_on": ["high"]}, {**steps[1], "depends_on": ["low"]}, steps[2],
                ]})
            with self.assertRaisesRegex(ValueError, "plan budget"):
                research_plans.create({**request, "budget_seconds": 179})
            plan = research_plans.create(request)
            self.assertEqual(research_plans.create(request)["id"], plan["id"])
            with Store() as store:
                owners = [uuid4(), uuid4(), uuid4()]
                lease = "SELECT * FROM rm.lease_research_plan_step(%s,%s)"
                complete = "SELECT * FROM rm.finish_research_plan_step(%s,%s,%s,%s,%s,%s)"
                first, = store.query("scheduler", lease, plan["id"], owners[0])
                second, = store.query("scheduler", lease, plan["id"], owners[1])
                self.assertEqual((first["step_key"], second["step_key"]), ("high", "middle"))
                self.assertFalse(store.query("scheduler", lease, plan["id"], owners[2]))
                with self.assertRaisesRegex(ValueError, "no longer current"):
                    store.query("scheduler", complete, plan["id"], "high", owners[1],
                                first["generation"], "completed", store.json({}))
                research_plans.cancel(plan["id"], "Changed directions")
                with self.assertRaisesRegex(ValueError, "no longer current"):
                    store.query("scheduler", complete, plan["id"], "high", owners[0],
                                first["generation"], "completed", store.json({}))
                self.assertFalse(store.query("scheduler", lease, plan["id"], owners[2]))

    def test_cross_plan_worker_ranks_its_scope_and_serializes_the_same_proof(self):
        with patch.dict(os.environ, self.env):
            packet = self.draft_formalization("Control the scope of competing proof workers")
            self.confirm_formalization(packet)

            def make_plan(title, action, priority):
                return research_plans.create({
                    "title": title, "max_parallel": 1, "budget_seconds": 60,
                    "steps": [{"key": "step", "action": action, "packet_id": packet["id"], "priority": priority}],
                })["id"]

            high = make_plan("High proof", "prove", 10)
            duplicate = make_plan("Overlapping lower proof", "prove", 8)
            independent = make_plan("Independent snapshot", "snapshot", 1)
            excluded = make_plan("A plan outside this worker scope", "snapshot", 100)
            owner = uuid4()
            scope = [duplicate, high, independent]
            with Store() as store:
                lease = "SELECT * FROM rm.lease_research_plan_group(%s,%s)"
                with self.assertRaisesRegex(ValueError, "permission denied"):
                    store.query("verifier", lease, scope, owner)
                original, = store.query("scheduler", lease, scope, owner)
                self.assertEqual(original["plan_id"], high)
                self.assertFalse(store.query("scheduler", lease, [duplicate], uuid4()))
            with patch.dict(os.environ, {"RM_HUMAN_DSN": "", "RM_AGENT_DSN": "", "RM_VERIFIER_DSN": ""}):
                worker = json.loads(self.cli(
                    "plan", "worker", "--plan", duplicate, "--plan", independent, "--limit", 2,
                ).stdout)
            self.assertEqual(worker, {"plans_visited": 1, "attempts_started": 1, "worker_errors": []})
            self.assertEqual(research_plans.show(excluded)["steps"][0]["generation"], 0)
            self.assertEqual(research_plans.show(duplicate)["steps"][0]["generation"], 0)
            research_plans.cancel(high, "Allow the other plan to continue.")
            self.assertEqual(research_plans.work_group([duplicate], 1, 1)["attempts_started"], 1)
            first = research_plans.show(duplicate)["steps"][0]["result"]["evidence_id"]
            reused = make_plan("Reuse the current receipt in a following route", "prove", 4)
            with patch.dict(os.environ, {"RM_HUMAN_DSN": "", "RM_AGENT_DSN": "", "RM_VERIFIER_DSN": ""}):
                worker = research_plans.work_group([reused], 1, 1)
            self.assertEqual(worker["attempts_started"], 1)
            self.assertFalse(worker["worker_errors"])
            self.assertEqual(research_plans.show(reused)["steps"][0]["result"]["evidence_id"], first)

    def test_cross_plan_lease_rechecks_scope_after_its_initial_snapshot(self):
        with patch.dict(os.environ, self.env):
            packet = self.draft_formalization("Check a stale plan lease candidate")
            self.confirm_formalization(packet)
            plan = research_plans.create({
                "title": "Race two independent schedulers", "max_parallel": 2, "budget_seconds": 60,
                "steps": [{"key": "snapshot", "action": "snapshot", "packet_id": packet["id"]}],
            })["id"]
            capacity = research_plans.create({
                "title": "Fill the remaining slots during an earlier read", "max_parallel": 2, "budget_seconds": 240,
                "steps": [
                    {"key": key, "action": "snapshot", "packet_id": packet["id"], "priority": priority,
                     "depends_on": dependencies}
                    for key, priority, dependencies in (
                        ("gate", 100, []), ("first", 10, ["gate"]), ("second", 9, ["gate"]), ("late", 1, []),
                    )
                ],
            })["id"]
            lease = "SELECT step_key, generation, lease_owner FROM rm.lease_research_plan_group(%s,%s)"
            barrier = "hashtext('rm-test-hold-plan-snapshot'), hashtext(%s::text)"
            with self.connect(self.dsn, autocommit=True) as observer:
                view, = observer.execute("SELECT pg_get_viewdef('rm.plan_work_status'::regclass, false)").fetchone()
                original = view.rstrip().rstrip(";")
                observer.execute("""
                    CREATE FUNCTION rm.test_pause_plan_snapshot(plan_id uuid) RETURNS boolean
                    LANGUAGE plpgsql SET search_path = pg_catalog AS $$
                    BEGIN
                        IF current_setting('rm.test_stale_plan', true) = plan_id::text THEN
                            PERFORM pg_advisory_xact_lock(hashtext('rm-test-hold-plan-snapshot'), hashtext(plan_id::text));
                        END IF;
                        RETURN true;
                    END $$
                """)
                try:
                    # Suspend the first SELECT after its snapshot was taken and
                    # before it can take the plan lock. Only this session pauses.
                    observer.execute(f"""
                        CREATE OR REPLACE VIEW rm.plan_work_status AS
                        SELECT original.* FROM ({original}) original
                        WHERE rm.test_pause_plan_snapshot(original.plan_id)
                    """)
                    scheduler = self.env["RM_SCHEDULER_DSN"]
                    with (self.connect(scheduler, autocommit=True) as stale,
                          self.connect(scheduler, autocommit=True) as winner,
                          ThreadPoolExecutor(max_workers=1) as pool):
                        stale.execute("SET statement_timeout = '15s'")
                        winner.execute("SET statement_timeout = '15s'")

                        @contextmanager
                        def pause_lease(plan_id):
                            stale.execute("SELECT set_config('rm.test_stale_plan', %s, false)", (str(plan_id),))
                            observer.execute(f"SELECT pg_advisory_lock({barrier})", (plan_id,))
                            result = pool.submit(lambda: stale.execute(lease, ([plan_id], uuid4())).fetchall())
                            try:
                                deadline = monotonic() + 5
                                while not observer.execute("""
                                    SELECT EXISTS (SELECT 1 FROM pg_locks
                                        WHERE pid=%s AND locktype='advisory' AND NOT granted)
                                """, (stale.info.backend_pid,)).fetchone()[0]:
                                    self.assertFalse(result.done(), "The stale query finished before reaching the barrier.")
                                    self.assertLess(monotonic(), deadline, "The stale query did not reach its test barrier.")
                                    sleep(0.01)
                                yield result
                            finally:
                                observer.execute(f"SELECT pg_advisory_unlock({barrier})", (plan_id,))

                        owner = uuid4()
                        with pause_lease(plan) as result:
                            self.assertEqual(winner.execute(lease, ([plan], owner)).fetchone(), ("snapshot", 1, owner))
                        self.assertEqual(result.result(timeout=10), [])
                        self.assertEqual(winner.execute("""
                            SELECT generation, lease_owner FROM rm.research_plan_steps WHERE plan_id=%s
                        """, (plan,)).fetchone(), (1, owner))

                        self.assertEqual(winner.execute(lease, ([capacity], owner)).fetchone(), ("gate", 1, owner))
                        with pause_lease(capacity) as result:
                            winner.execute("SELECT * FROM rm.finish_research_plan_step(%s,'gate',%s,1,'completed','{}')",
                                           (capacity, owner))
                            for key in ("first", "second"):
                                self.assertEqual(winner.execute(lease, ([capacity], owner)).fetchone(), (key, 1, owner))
                        self.assertEqual(result.result(timeout=10), [])
                        self.assertEqual(winner.execute("""
                            SELECT generation, status FROM rm.research_plan_steps WHERE plan_id=%s AND step_key='late'
                        """, (capacity,)).fetchone(), (0, "queued"))
                finally:
                    observer.execute(f"CREATE OR REPLACE VIEW rm.plan_work_status AS {original}")
                    observer.execute("DROP FUNCTION rm.test_pause_plan_snapshot(uuid)")

    def test_program_first_route_and_scoped_policy_worker_skip_unapproved_and_returned_work(self):
        with patch.dict(os.environ, self.env):
            packet = self.draft_formalization("Program-first routing input")
            draft = json.loads(self.cli("advance", "route", packet["id"]).stdout)
            self.assertEqual(draft["route"], "scope_unavailable")
            self.assertIsNone(draft["suggested_policy"])
            self.confirm_formalization(packet)
            route = json.loads(self.cli("advance", "route", packet["id"]).stdout)
            self.assertEqual(route["route"], "deterministic_proof")
            witness_input = self.root / "witness-only.json"
            witness_input.write_text(json.dumps({"A": [[1]], "b": [2], "c": [1], "target_value": 2}), encoding="utf-8")
            witness_packet = json.loads(self.cli(
                "formalization", "draft", witness_input, "--domain", "linear-program", "--title", "Supply a witness",
            ).stdout)
            self.confirm_formalization(witness_packet)
            observe = json.loads(self.cli("advance", "route", witness_packet["id"]).stdout)
            self.assertEqual(observe["route"], "observe_verified_evidence")
            self.assertEqual(observe["suggested_policy"]["actions"], [])
            self.assertEqual(observe["suggested_policy"]["budget_seconds"], 0)
            selected = json.loads(self.cli("advance", "propose", "--for-packet", packet["id"]).stdout)
            parallel_packet = self.draft_formalization("Independent policy to leave idle")
            self.confirm_formalization(parallel_packet)
            other = research_progress.propose({
                **route["suggested_policy"], "packet_id": parallel_packet["id"], "priority": 30,
            })
            research_progress.approve(other["id"], other["policy_sha256"], "Researcher", "Independent task")
            self.assertEqual(research_progress.advance_cycle("scheduler", 2, [selected["id"]])["attempts_started"], 0)
            research_progress.approve(selected["id"], selected["policy_sha256"], "Researcher", "Exact deterministic task")
            started = json.loads(self.cli("advance", "worker", "--policy", selected["id"], "--stage", "scheduler").stdout)
            self.assertEqual(started["attempts_started"], 1)
            self.assertEqual(research_progress.status(other["id"])["status"]["state"], "needs_plan")
            current = research_progress.status(selected["id"])["status"]
            self.assertEqual(current["state"], "waiting_review")
            self.cli("review-evidence", current["current_evidence_id"], "return", "--actor", "Researcher",
                     "--reason", "This route is finished pending human direction.")
            self.assertEqual(research_progress.advance_cycle("scheduler", 4, [selected["id"]])["attempts_started"], 1)
            self.assertEqual(research_progress.status(selected["id"])["status"]["state"], "review_returned")
            self.assertEqual(research_progress.advance_cycle("scheduler", 4, [selected["id"]])["attempts_started"], 0)
            research_progress.stop(other["id"], "The scope test is complete.")

    def test_formalization_confirmation_reuses_exact_packet(self):
        with self.models(proof=ALIGNED_PROOF):
            draft = self.draft_formalization("Aligned flow")
            packet_id, claim_id = draft["id"], draft["claim_id"]
            definitions = {item["id"]: item for item in draft["packet"]["items"]}
            self.assertIn("real", definitions["H2"]["natural"])
            self.assertEqual(definitions["H2"]["formal"]["flow_value_type"], "real")
            self.assertTrue(all(item["status"] == "stated" for item in definitions.values()))
            self.assertTrue(all(not item.get("question") for item in definitions.values()))
            shown = json.loads(self.cli("formalization", "show", packet_id).stdout)
            self.assertEqual(shown["packet_sha256"], draft["packet_sha256"])
            self.assertEqual(shown["packet"], draft["packet"])
            self.assertFalse(shown["proof_ready"])
            self.assertEqual(self.calls, [])

            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root, expect=2)
            self.assertFalse(list(self.root.iterdir()))
            self.assertEqual(self.calls, [])

            approval = ["formalization", "confirm", packet_id, "--actor", "test researcher",
                        "--reason", "Checked the fixed network and real-valued flows"]
            self.cli(*approval, "--version", draft["version"], "--hash", "0" * 64, expect=2)
            self.cli(*approval, "--version", draft["version"] + 1, "--hash", draft["packet_sha256"], expect=2)
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root, expect=2)
            self.assertEqual(self.calls, [])

            self.cli(*approval, "--version", draft["version"], "--hash", draft["packet_sha256"])
            confirmed = json.loads(self.cli("formalization", "show", packet_id).stdout)
            self.assertTrue(confirmed["proof_ready"])
            self.assertEqual(confirmed["packet_sha256"], draft["packet_sha256"])
            self.assertEqual(self.calls, [])
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root)
            directory = self.root / claim_id
            outcome = self.record(directory)
            evidence = outcome["evidence_id"]
            self.assertEqual(outcome["human_review"], "pending")
            self.assertEqual(outcome["claim_status"], "open")
            self.assertEqual(outcome["formalization"],
                             {key: draft[key] for key in ("id", "version", "packet_sha256")})
            self.assertEqual(json.loads((directory / "verification.json").read_bytes())["value"], 23)
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root)
            self.assertEqual(self.record(directory)["evidence_id"], evidence)
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])
            alignment = self.model_inputs[0]["proof_requirements"]["formalization"]
            self.assertEqual(alignment["id"], packet_id)
            self.assertEqual(alignment["version"], draft["version"])
            self.assertEqual(alignment["packet_sha256"], draft["packet_sha256"])
            self.assertEqual(alignment["items"], [
                {key: item[key] for key in ("id", "kind", "natural", "formal")}
                for item in draft["packet"]["items"]
            ])
            with self.connect(self.dsn) as db:
                provenance = db.execute("""
                    SELECT payload ->> 'formalization_id', payload ->> 'formalization_sha256'
                    FROM rm.jobs WHERE claim_id = %s AND kind = 'prove'
                """, (claim_id,)).fetchall()
            self.assertEqual(provenance, [(packet_id, draft["packet_sha256"])])
            self.cli("promote-evidence", evidence, "--reason", "Before evidence review", expect=2)
            self.cli("review-evidence", evidence, "approve", "--actor", "test researcher", "--reason", "Checked")
            self.cli("promote-evidence", evidence, "--reason", "Evidence approved")
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root)
            self.assertEqual(self.record(directory)["claim_status"], "proved")
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])

    def test_formalization_requires_resolution_of_material_items(self):
        clarify = self.root / "clarify.json"
        question = "Should allowable flows range over real values?"
        clarify.write_text(json.dumps({"H2": question}), encoding="utf-8")
        with self.models():
            draft = self.draft_formalization("Review flow convention", clarify=clarify)
            questions = [item for item in draft["packet"]["items"] if item.get("question")]
            self.assertEqual([(item["id"], item["question"]) for item in questions], [("H2", question)])
            self.assertEqual(questions[0]["status"], "needs_confirmation")
            approval = ["formalization", "confirm", draft["id"], "--hash", draft["packet_sha256"],
                        "--version", draft["version"], "--actor", "test researcher",
                        "--reason", "Confirmed real-valued flow convention"]
            self.cli(*approval, expect=2)
            self.cli(*approval, "--resolve", "H1", expect=2)
            self.cli(*approval, "--resolve", "H2")
            confirmed = json.loads(self.cli("formalization", "show", draft["id"]).stdout)
            self.assertTrue(confirmed["proof_ready"])
            self.assertEqual(confirmed["packet_sha256"], draft["packet_sha256"])
            self.assertEqual(self.calls, [])

    def test_formalization_pauses_for_one_material_proof_question(self):
        question = {
            "item_id": "H2",
            "question": "Is the upper bound over all real-valued feasible flows?",
            "impact": "Changing the domain would change the quantified conclusion.",
        }
        responses = iter([{"clarification": question}, {"clarification": question}, ALIGNED_PROOF])
        with self.models(proof=lambda: next(responses)):
            draft = self.draft_formalization("Proof question")
            packet_id = draft["id"]
            self.confirm_formalization(draft)
            directory = self.root / draft["claim_id"]
            with patch.object(production.formalization, "request", side_effect=OSError("interrupted before clarification")):
                with self.assertRaisesRegex(OSError, "before clarification"):
                    production.run_formalized(packet_id, self.root, strategy="model")
            self.assertEqual(self.calls, ["test-prover"])
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root, "--retry", expect=2)
            self.assertEqual(self.calls, ["test-prover"])
            waiting = self.record(directory)
            self.assertEqual(waiting["human_review"], "needs_clarification")
            request_id = waiting["clarification_request_id"]
            shown = json.loads(self.cli("formalization", "show", packet_id).stdout)
            self.assertFalse(shown["proof_ready"])
            request, = shown["clarifications"]
            self.assertEqual(request["id"], request_id)
            self.assertEqual(request["question"], question["question"])
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root, "--retry", expect=2)
            self.cli("resume", directory, "--retry", expect=2)
            self.assertEqual(self.calls, ["test-prover"])
            self.cli("formalization", "resolve", request_id, "--actor", "test researcher",
                     "--reason", "Keep the confirmed upper bound over all feasible real-valued flows.")
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root, "--retry", expect=2)
            status = json.loads(self.cli("formalization", "show", packet_id).stdout)
            self.assertTrue(status["proof_ready"])
            self.assertEqual([(item["id"], item["resolved"]) for item in status["clarifications"]],
                             [(request_id, True)])
            self.cli("formalization", "prove", "--strategy", "model", packet_id, "--out", self.root, "--retry")
            completed = self.record(directory)
            self.assertEqual(completed["human_review"], "pending")
            self.assertEqual(completed["formalization"]["packet_sha256"], draft["packet_sha256"])
            self.assertEqual(self.model_inputs[1]["proof_requirements"]["reaffirmed_items"], ["H2"])
            self.assertEqual(self.calls, ["test-prover", "test-prover", "test-prover", "test-auditor"])

    def test_alignment_rejects_an_unapproved_basis(self):
        for item in ("H4", "goal"):
            with self.subTest(item=item):
                proof = {**ALIGNED_PROOF, "basis_items": [*ALIGNED_PROOF["basis_items"], item]}
                with self.models(proof=proof):
                    draft = self.draft_formalization("Unexpected proof premise")
                    self.confirm_formalization(draft)
                    self.cli("formalization", "prove", "--strategy", "model", draft["id"], "--out", self.root, expect=2)
                    directory = self.root / draft["claim_id"]
                    outcome = self.record(directory)
                    self.assertNotIn("evidence_id", outcome)
                    self.assertNotIn("audit", outcome["attempts"])
                    self.assertFalse((directory / "derivations.json").exists())
                    self.assertEqual(self.calls[-1], "test-prover")
        self.assertEqual(self.calls, ["test-prover", "test-prover"])

    def test_alignment_keeps_checked_intermediate_dependencies_across_audit_retry(self):
        audits = iter([503, AUDIT])
        with self.models(proof=ALIGNED_PROOF, audit=lambda: next(audits)):
            draft = self.draft_formalization("Checked flow dependencies")
            self.confirm_formalization(draft)
            self.cli("formalization", "prove", "--strategy", "model", draft["id"], "--out", self.root, expect=2)
            directory = self.root / draft["claim_id"]
            outcome = self.record(directory)
            self.assertEqual(outcome["human_review"], "awaiting_audit")
            self.assertNotIn("evidence_id", outcome)
            derivations = json.loads((directory / "derivations.json").read_bytes())
            self.assertEqual({item["item_key"] for item in derivations}, {"flow_attains_target", "cut_upper_bound"})
            shown = json.loads(self.cli("formalization", "show", draft["id"]).stdout)
            self.assertEqual(shown["verified_derivations"], derivations)
            self.assertTrue(all(item["id"].startswith("D:") for item in derivations))
            known = {"H1", "H2", "H3"}
            for item in derivations:
                self.assertTrue(set(item["premise_item_ids"]) <= known)
                known.add(item["id"])
            self.cli("formalization", "prove", "--strategy", "model", draft["id"], "--out", self.root, "--retry")
            recovered = self.record(directory)
            self.assertEqual(recovered["human_review"], "pending")
            self.assertEqual(recovered["attempts"]["prove"]["id"], outcome["attempts"]["prove"]["id"])
            self.assertEqual(self.model_inputs[-1]["proof_candidate"]["basis_items"], ALIGNED_PROOF["basis_items"])
            model_fields = ("id", "item_key", "natural", "formal", "premise_item_ids")
            self.assertEqual(self.model_inputs[-1]["proof_requirements"]["verified_derivations"], [
                {key: item[key] for key in model_fields} for item in derivations
            ])
            self.assertEqual(json.loads((directory / "derivations.json").read_bytes()), derivations)
            self.assertEqual(self.calls, ["test-prover", "test-auditor", "test-auditor"])
            self.cli("replay", directory / "run.json")

    def test_exploration_requires_both_basis_and_candidate_review(self):
        scope = self.root / "scope.json"
        scope.write_text(json.dumps({
            "format_version": 1,
            "domain": "fixed-directed-max-flow",
            "sensitivity": "public",
            "title": "Explore the capacity of one directed edge",
            "network": {"vertices": ["s", "t"], "source": "s", "sink": "t", "edges": [["s", "t", 7]]},
            "items": [],
        }), encoding="utf-8")
        proof = {
            "proof_markdown": "The edge carries 7, and the cut consisting of the source bounds every flow by 7.",
            "certificate": {"flows": [["s", "t", 7]], "source_side": ["s"]},
            "basis_items": ["H1", "H2", "H3"],
        }
        model_guess = {"target_value": 6, "analysis": "Check a flow that attains 6.",
                       "basis_item_ids": ["H1", "H2", "H3"]}
        responses = iter([model_guess, proof])
        with self.models(proof=lambda: next(responses)):
            basis = json.loads(self.cli("formalization", "explore-draft", scope).stdout)
            self.assertIsNone(basis["claim_id"])
            self.cli("formalization", "explore", basis["id"], expect=2)
            self.confirm_formalization(basis)
            shown = json.loads(self.cli("formalization", "show", basis["id"]).stdout)
            self.assertTrue(shown["approved"])
            self.assertFalse(shown["proof_ready"])
            self.cli("formalization", "prove", "--strategy", "model", basis["id"], "--out", self.root, expect=2)

            proposed = json.loads(self.cli("formalization", "explore", basis["id"]).stdout)
            self.assertEqual(proposed["basis"]["packet_sha256"], basis["packet_sha256"])
            candidate, = proposed["candidates"]
            self.assertEqual(candidate["id"], "maximum-flow")
            self.assertEqual(candidate["target_value"], 7)
            self.assertEqual(candidate["support"]["cut_capacity"], 7)
            self.assertEqual(set(candidate["basis_item_ids"]), {"H1", "H2", "H3"})
            self.assertEqual(self.calls, [])
            modeled = json.loads(self.cli("formalization", "explore", basis["id"], "--model").stdout)
            self.assertEqual(modeled["candidates"], proposed["candidates"])
            self.assertEqual(modeled["model_analysis"]["target_value"], 6)
            self.assertFalse(modeled["model_analysis"]["matches_cut_candidate"])
            self.assertEqual(self.calls, ["test-prover"])

            select = ["formalization", "choose", basis["id"], "--candidate", candidate["id"],
                      "--version", basis["version"]]
            self.cli(*select, "--hash", "0" * 64, expect=2)
            selected = json.loads(self.cli(*select, "--hash", basis["packet_sha256"]).stdout)
            repeated = json.loads(self.cli(*select, "--hash", basis["packet_sha256"]).stdout)
            self.assertEqual(repeated["id"], selected["id"])
            self.assertNotEqual(selected["id"], basis["id"])
            self.assertIsNotNone(selected["claim_id"])
            preview = json.loads(self.cli("formalization", "show", selected["id"]).stdout)
            self.assertEqual(preview["basis_packet_id"], basis["id"])
            self.assertEqual([item["id"] for item in preview["packet"]["items"]
                              if item["status"] == "needs_confirmation"], ["goal"])
            self.assertFalse(preview["proof_ready"])
            self.cli("formalization", "prove", "--strategy", "model", selected["id"], "--out", self.root, expect=2)
            self.assertEqual(self.calls, ["test-prover"])
            self.confirm_formalization(selected, resolution="goal")
            self.cli("formalization", "prove", "--strategy", "model", selected["id"], "--out", self.root)
            directory = self.root / selected["claim_id"]
            self.assertEqual(self.record(directory)["human_review"], "pending")
            self.assertEqual(json.loads((directory / "verification.json").read_bytes())["value"], 7)
            lineage = self.model_inputs[1]["proof_requirements"]["formalization"]["exploration_basis"]
            self.assertEqual(lineage, {"id": basis["id"], "packet_sha256": basis["packet_sha256"]})
            self.assertEqual(self.calls, ["test-prover", "test-prover", "test-auditor"])

    def test_interruption_after_database_receipt_recovers_without_model_calls(self):
        with self.models():
            real_write = production.write_json

            def interrupted(path, data):
                if path.name == "run.json" and data.get("human_review") == "pending":
                    raise OSError("interrupted after signature commit")
                real_write(path, data)

            with patch.object(production, "write_json", side_effect=interrupted):
                with self.assertRaisesRegex(OSError, "after signature"):
                    production.run_public(FLOW_INSTANCE, "Recovery", self.root, "test")
            directory, = self.root.iterdir()
            self.assertNotIn("evidence_id", self.record(directory))
            (directory / "run.json").unlink()
            self.cli("resume", directory)
            evidence = self.record(directory)["evidence_id"]
            self.cli("resume", directory)
            self.assertEqual(self.record(directory)["evidence_id"], evidence)
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])
            with self.connect(self.dsn) as db:
                self.assertEqual(db.execute(
                    "SELECT count(*) FROM rm.evidence WHERE claim_id = %s", (directory.name,)
                ).fetchone()[0], 1)

    def test_interruption_after_proof_commit_reuses_it(self):
        with self.models():
            real_write = production.write_json

            def interrupted(path, data):
                if path.name == "run.json" and "prove" in data.get("attempts", {}):
                    raise OSError("interrupted after proof commit")
                real_write(path, data)

            with patch.object(production, "write_json", side_effect=interrupted):
                with self.assertRaisesRegex(OSError, "after proof"):
                    production.run_public(FLOW_INSTANCE, "Recovery", self.root, "test")
            directory, = self.root.iterdir()
            self.assertNotIn("prove", self.record(directory)["attempts"])
            self.assertEqual(self.calls, ["test-prover"])
            self.cli("resume", directory)
            self.assertEqual(self.record(directory)["human_review"], "pending")
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])

    def test_audit_retry_does_not_recompute_a_committed_proof(self):
        results = iter([503, AUDIT])
        with self.models(audit=lambda: next(results)):
            directory = self.start(expect=2)
            failure = self.record(directory)
            self.assertEqual(failure["human_review"], "awaiting_audit")
            self.cli("resume", directory, expect=2)
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])
            self.cli("resume", directory, "--retry")
            recovered = self.record(directory)
            self.assertEqual(recovered["attempts"]["prove"]["id"], failure["attempts"]["prove"]["id"])
            self.assertEqual(recovered["attempts"]["audit"]["return"]["generation"], 2)
            self.assertEqual(self.calls, ["test-prover", "test-auditor", "test-auditor"])

    def test_invalid_certificate_stops_before_audit(self):
        wrong = deepcopy(PROOF)
        wrong["certificate"]["flows"][0][-1] = 0
        with self.models(proof=wrong):
            directory = self.start(expect=2)
            self.assertEqual(self.record(directory)["human_review"], "certificate_rejected")
            self.cli("resume", directory, "--retry", expect=2)
            self.assertEqual(self.calls, ["test-prover"])

    def test_resume_refuses_an_unrelated_frozen_statement_before_model_calls(self):
        from research_multiplier.flow_claim import public_claim
        from research_multiplier.storage.postgres import Store

        changed = public_claim(FLOW_INSTANCE, "Unrelated statement")
        changed["statement"] = "0 = 1"
        changed["conclusion"]["statement"] = "0 = 1"
        directory = self.root / changed["claim_id"]
        directory.mkdir()
        with self.models():
            with Store() as store:
                store.freeze(changed, "test")
            result = self.cli("resume", directory, expect=2)
            self.assertIn("scope", result.stderr)
            self.assertEqual(self.calls, [])

    def test_scope_mismatch_is_escalated_without_reauditing(self):
        rejected = {"verdict": "rejected", "diff_status": "different", "reason": "Different claim."}
        with self.models(audit=rejected):
            directory = self.start(expect=2)
            self.assertEqual(self.record(directory)["human_review"], "needs_human")
            self.cli("resume", directory, "--retry", expect=2)
            self.assertEqual(self.calls, ["test-prover", "test-auditor"])
