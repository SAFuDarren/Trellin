"""Check CLI database admission and worker isolation across two real projects."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import research_multiplier
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError, Store


DSN = os.environ.get("RM_TEST_DSN")


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class WorkspaceBindingTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        from sqlalchemy.engine import make_url

        from research_multiplier.storage.migrate import upgrade

        self.connect = psycopg.connect
        self.dsns = []
        self.roles = [{}, {}]
        suffix = uuid4().hex[:12]
        for index in range(2):
            database = f"rm_workspace_{suffix}_{index}"
            with psycopg.connect(DSN, autocommit=True) as db:
                db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            self.addCleanup(self.drop_database, database)
            dsn = make_url(DSN).set(database=database).render_as_string(hide_password=False)
            upgrade(dsn)
            self.dsns.append(dsn)
        with psycopg.connect(DSN, autocommit=True) as db:
            for group in ("human", "scheduler", "agent", "verifier"):
                login, password = f"rm_ws_{group}_{suffix}", uuid4().hex
                db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                    sql.Identifier(login), sql.Literal(password), sql.Identifier(f"rm_{group}")))
                self.addCleanup(self.drop_role, login)
                for index, dsn in enumerate(self.dsns):
                    self.roles[index][f"RM_{group.upper()}_DSN"] = make_conninfo(dsn, user=login, password=password)
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.workspaces = (self.root / "project-a", self.root / "project-b")

    def drop_database(self, database):
        from psycopg import sql

        with self.connect(DSN, autocommit=True) as db:
            db.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))

    def drop_role(self, role):
        from psycopg import sql

        with self.connect(DSN, autocommit=True) as db:
            db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def test_library_requires_bound_workspace_and_least_privileged_role(self):
        root, _ = self.workspaces
        paths.initialize_workspace(root)
        with paths.use_workspace(root), patch.dict(os.environ, self.roles[0]):
            with self.assertRaisesRegex(ValueError, "Bind this research workspace"):
                Store()
        self.cli("research", "bind-workspace", project=root)
        with paths.use_workspace(root), patch.dict(os.environ, self.roles[0]):
            self.assertFalse(paths.strict_workspace())
            with Store() as store:
                self.assertTrue(store.query("scheduler", "SELECT 1"))
            with patch.dict(os.environ, {"RM_SCHEDULER_DSN": self.dsns[0]}):
                with Store() as store, self.assertRaisesRegex(ValueError, "only the rm_scheduler capability group"):
                    store.query("scheduler", "SELECT 1")

    def test_transaction_keeps_application_errors_and_recognizes_database_errors(self):
        root, _ = self.workspaces
        paths.initialize_workspace(root)
        self.cli("research", "bind-workspace", project=root)
        with paths.use_workspace(root), patch.dict(os.environ, self.roles[0]), Store() as store:
            def broken_application_callback():
                raise KeyError("application defect")

            with self.assertRaisesRegex(KeyError, "application defect"):
                store.transaction("scheduler", broken_application_callback)
            with self.assertRaises(DatabaseOperationError) as failure:
                store.query("scheduler", "SELECT missing_identifier")
            self.assertEqual(failure.exception.state, "42703")
            self.assertEqual(store.query("scheduler", "SELECT 1 AS result")[0]["result"], 1)

    def cli(self, *args, project=None, database=0, overrides=None, expect=0):
        package = Path(research_multiplier.__file__).resolve().parents[1]
        env = {key: value for key, value in os.environ.items() if key not in {"RM_DATA_ROOT", "RM_ARTIFACT_ROOT"}}
        env.update(self.roles[database], PYTHONPATH=str(package))
        if overrides:
            env.update(overrides)
        command = [sys.executable, "-m", "research_multiplier"]
        if project is not None:
            command.extend(("--workspace", project))
        result = subprocess.run([*map(str, command), *map(str, args)], cwd=self.root,
                                env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout)
        return json.loads(result.stdout) if not expect else result.stderr

    def test_selected_workspace_pairing_and_mixed_worker_rejection(self):
        a, b = self.workspaces
        self.assertIn("workspace", self.cli("research", "resume", expect=2).lower())
        self.cli("research", "init-workspace", a)
        self.assertIn("bind", self.cli("research", "resume", project=a, expect=2).lower())
        selected = self.cli("research", "bind-workspace", project=a)
        self.assertEqual(self.cli("research", "bind-workspace", project=a), selected)
        self.cli("research", "init-workspace", b)
        self.assertNotEqual(paths.workspace_id(a), paths.workspace_id(b))
        mismatch = self.cli("research", "bind-workspace", project=b, expect=2)
        self.assertIn("different research workspace", mismatch)
        self.assertIsNone(paths.database_id(b))
        other = self.cli("research", "bind-workspace", project=b, database=1)
        self.assertNotEqual(selected["database_id"], other["database_id"])
        self.assertIn("different research database", self.cli("research", "resume", project=a, database=1, expect=2))
        copied = self.root / "copied-binding"
        self.cli("research", "init-workspace", copied)
        paths.bind_database(copied, paths.database_id(a))
        self.assertIn("different research workspace", self.cli("research", "resume", project=copied, expect=2))

        network = a / "inputs" / "graph.json"
        network.write_text(json.dumps({"vertices": ["a", "b"], "edges": [["a", "b"]], "colors": 2}), encoding="utf-8")
        packet = self.cli("formalization", "draft", network, "--domain", "graph-coloring",
                          "--title", "Proof belongs to project A", project=a)
        self.cli("formalization", "confirm", packet["id"], "--version", packet["version"],
                 "--hash", packet["packet_sha256"], "--actor", "researcher", "--reason", "Check graph scope.", project=a)
        plan_file = a / "inputs" / "plan.json"
        plan_file.write_text(json.dumps({
            "title": "Prove in A", "max_parallel": 1, "budget_seconds": 60,
            "steps": [{"key": "proof", "action": "prove", "packet_id": packet["id"], "timeout_seconds": 50}],
        }), encoding="utf-8")
        plan = self.cli("plan", "create", plan_file, project=a)
        mixed = {"RM_AGENT_DSN": self.roles[1]["RM_AGENT_DSN"], "RM_HUMAN_DSN": ""}
        self.assertIn("agent login points to a different research database",
                      self.cli("plan", "work", plan["id"], project=a, overrides=mixed, expect=2))
        awaiting = self.cli("plan", "show", plan["id"], project=a)
        self.assertEqual(awaiting["steps"][0]["generation"], 0)
        self.assertEqual(awaiting["steps"][0]["status"], "ready")
        self.assertFalse(any(file.is_file() for file in (a / "runs").rglob("*")))

        result = self.cli("plan", "work", plan["id"], project=a, overrides={"RM_HUMAN_DSN": ""})
        self.assertEqual(result["steps"][0]["status"], "succeeded")
        self.assertEqual(result["steps"][0]["generation"], 1)
        self.assertTrue((a / "runs" / "claims" / packet["claim_id"] / "run.json").is_file())
        self.assertFalse(any(file.is_file() for file in (b / "runs").rglob("*")))
        self.assertEqual(self.cli("research", "resume", project=b, database=1)["claims"]["items"], [])
