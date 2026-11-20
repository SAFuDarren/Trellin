"""Exercise paired Lean search, actual kernel receipts and workspace isolation."""

import os
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_final_local_lemmas as final_fixtures
import test_lean_flow as lean_fixtures


BASELINE_INSTALL = os.environ.get("RM_TEST_LEAN_BASELINE_WHEEL_INSTALL")
CANDIDATE_INSTALL = os.environ.get("RM_TEST_LEAN_CANDIDATE_WHEEL_INSTALL")


ADAPTER = """\
import json

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities

class Adapter:
    capabilities = ModelCapabilities(output_token_limit_enforced=True, token_usage_available=True)

    def complete(self, request):
        context = json.loads(request["messages"][1]["content"])
        assert context["antecedents"] == []
        assert context["consequent"] == "∀ n : Nat, n = n"
        assert context["approved_theorems"] == []
        proof = {"action": "proof", "proof": {"intro": {"count": 1, "body": {"rfl": True}}}}
        if request["model_id"] == "extra-hypothesis":
            proof["hypotheses"] = ["False"]
        return AdapterReply(text=json.dumps(proof), model_id=request["model_id"], input_tokens=120,
                            output_tokens=20, cached_input_tokens=10, cost_usd="0.00123")

def factory(config):
    return Adapter()
"""


@unittest.skipUnless(lean_fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanShadowTests(unittest.TestCase):
    def setUp(self):
        self.control_dsn = lean_fixtures.DSN
        self.source_dsn = self.create_database()
        self.enterContext(patch.object(lean_fixtures, "DSN", self.source_dsn))
        self.case = final_fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fixture = self.case.fixture
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="rm-lean-shadow-test-")))

    def create_database(self):
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        database = f"rm_lean_shadow_{uuid4().hex[:12]}"
        with psycopg.connect(self.control_dsn, autocommit=True) as db:
            db.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        self.addCleanup(self.drop_database, database)
        return make_url(self.control_dsn).set(database=database).render_as_string(hide_password=False)

    def restore_pair(self, archive, case_id=None):
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        env = self.fixture.env
        roots = {}
        self.restored_dsns = {}
        prefix = f"RM_SHADOW_{case_id.upper().replace('-', '_')}_" if case_id else "RM_SHADOW_"
        for side in ("BASELINE", "CANDIDATE"):
            dsn = self.create_database()
            self.restored_dsns[side] = dsn
            env["RM_MIGRATION_DSN"] = dsn
            path = self.root / f"{case_id or 'example'}-{side.lower()}"
            self.case.cli("research", "restore-project", archive, path)
            roots[side] = path
            for role in ("HUMAN", "SCHEDULER", "AGENT", "VERIFIER"):
                env[f"{prefix}{side}_{role}_DSN"] = make_conninfo(
                    env[f"RM_{role}_DSN"], dbname=conninfo_to_dict(dsn)["dbname"],
                )
        env.pop("RM_MIGRATION_DSN")
        return roots

    def backup(self):
        env = self.fixture.env
        env["RM_MIGRATION_DSN"] = self.source_dsn
        archive = self.root / "same-snapshot"
        self.case.cli("research", "backup-project", "--out", archive)
        env.pop("RM_MIGRATION_DSN")
        return archive

    def test_pair_verifies_exact_goal_and_fences_mixed_workspace(self):
        root = self.root
        fixture_adapter = root / "fixed_lean_shadow.py"
        fixture_adapter.write_text(ADAPTER, encoding="utf-8")
        env = self.fixture.env
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH"))))
        author = "RESEARCH_MULTIPLIER_LEAN_AUTHOR_"
        candidate = "RESEARCH_MULTIPLIER_PROFILE_SHADOWTEST_"
        for prefix, model in ((author, "extra-hypothesis"), (candidate, "exact")):
            env.update({prefix + key: value for key, value in {
                "PROVIDER": "fixed-shadow", "MODEL": model, "FAMILY": "fixed-shadow",
                "ADAPTER": "fixed_lean_shadow:factory",
            }.items()})
        label = f"Paired Lean search {uuid4().hex}"
        anchor, scope, _ = self.case.anchor(label, "∀ n : Nat, n = n", [], sensitivity="public",
                                            statement_text=f"{label}: every natural number equals itself.")
        source, _, _ = self.case.anchor("Reusable premise for paired evaluation", "True ∧ True", [], sensitivity="public")
        [working] = self.case.cli("lean", "derive", source["id"], self.case.document("shadow_corpus", {
            "format_version": 1, "nodes": [{"name": "Known", "proposition": "True",
                                            "proof": {"trivial": True}}],
        }))["lemmas"]
        workspaces = self.restore_pair(self.backup())
        command = ("eval", "shadow-lean", anchor["id"], *workspaces.values(),
                   "--candidate-profile", "SHADOWTEST", "--rounds", 1)
        self.assertIn("--confirm-public", self.case.cli(*command, expect=2))
        original = env["RM_SHADOW_CANDIDATE_VERIFIER_DSN"]
        env["RM_SHADOW_CANDIDATE_VERIFIER_DSN"] = env["RM_VERIFIER_DSN"]
        failed = self.case.cli(*command, "--confirm-public", expect=2)
        self.assertIn("different research database", failed)
        env["RM_SHADOW_CANDIDATE_VERIFIER_DSN"] = original

        # A clone with the same SQL snapshot but a changed proof object cannot start.
        relative = working["body"]["submission"]["sha256"]
        artifact = workspaces["CANDIDATE"] / "objects" / relative
        saved = artifact.read_bytes()
        artifact.write_bytes(b"changed test artifact")
        try:
            failed = self.case.cli(*command, "--confirm-public", expect=2)
            self.assertIn("proof artifact", failed)
        finally:
            artifact.write_bytes(saved)

        # The valid rerun also proves that the failed preflight left both twins pristine.
        report = self.case.cli(*command, "--confirm-public")
        baseline = report["sides"]["baseline"]["result"]
        candidate = report["sides"]["candidate"]["result"]
        self.assertEqual(baseline["outcome"], "no_result")
        self.assertEqual(candidate["outcome"], "verified")
        self.assertEqual(candidate["model"]["reservation_count"], 1)
        self.assertEqual(candidate["model"]["cached_input_tokens"]["reported_total"], 10)
        self.assertEqual(candidate["model"]["cost_usd"]["reported_total"], "0.00123")
        self.assertTrue(candidate["receipts"][0]["report"]["sha256"])
        self.assertEqual(candidate["review"]["pending_evidence_reviews"], 1)
        self.assertEqual(candidate["review"]["performed_by_researcher"], 0)
        self.assertEqual(report["starting_corpus"]["working_lemmas"], 1)
        archived = self.fixture.workspace / "evaluation" / "private" / "objects"
        self.assertTrue((archived / candidate["receipts"][0]["report"]["sha256"]).is_file())
        fetched = self.case.cli("eval", "shadow-lean-status", report["report"]["sha256"])
        self.assertEqual(fetched, {key: value for key, value in report.items() if key != "report"})
        self.assertEqual(self.case.cli("research", "tasks", scope["claim_id"])["tasks"], [])
        original_claim = self.case.cli("research", "resume", scope["claim_id"])
        self.assertEqual(original_claim["status"], "open")
        self.assertIsNone(original_claim["conclusion"])

    def test_counterbalanced_zero_model_campaign_keeps_ordered_steps(self):
        propositions = ["∀ n : Nat, (n = n) ∧ True", "∀ q : Nat, (q = q) ∨ False", "True"]
        anchors = [self.case.anchor(f"Zero campaign {index}", proposition, [], sensitivity="public")[0]
                   for index, proposition in enumerate(propositions)]
        archive = self.backup()
        cases = []
        for name, selected in (("trajectory", anchors[:2]), ("control", anchors[2:])):
            twins = self.restore_pair(archive, case_id=name)
            cases.append({"id": name, "baseline_workspace": str(twins["BASELINE"]),
                          "candidate_workspace": str(twins["CANDIDATE"]),
                          "statements": [item["id"] for item in selected],
                          "expected": ["verified"] * len(selected)})
        manifest = self.case.document("paired_campaign", {"format_version": 1, "split": "holdout", "cases": cases})
        command = ("eval", "shadow-lean-campaign", manifest, "--baseline-strategy", "legacy",
                   "--candidate-strategy", "ranked")
        failure = self.case.cli(*command, "--rounds", 1, "--confirm-public", "--max-model-calls", 5, expect=2)
        self.assertIn("aggregate campaign allowance", failure)
        report = self.case.cli(*command, "--rounds", 0, "--max-model-calls", 0)
        self.assertTrue(report["source_unchanged"])
        self.assertEqual(report["budget"]["planned_max_model_calls"], 0)
        self.assertEqual([case["order"] for case in report["cases"]],
                         [["baseline", "candidate"], ["candidate", "baseline"]])
        steps = report["cases"][0]["steps"]
        self.assertEqual([step["statement_id"] for step in steps], cases[0]["statements"])
        self.assertEqual(steps[1]["order"], ["candidate", "baseline"])
        for side in ("baseline", "candidate"):
            self.assertEqual(steps[0]["sides"][side]["result"]["ending_corpus"],
                             steps[1]["sides"][side]["result"]["starting_corpus"])
            self.assertEqual(report["totals"][side]["reserved_calls"], 0)
        candidate_first = steps[0]["sides"]["candidate"]["result"]
        candidate_second = steps[1]["sides"]["candidate"]["result"]
        self.assertGreater(candidate_first["ending_corpus"]["working_lemmas"],
                           candidate_first["starting_corpus"]["working_lemmas"])
        self.assertTrue(any(attempt.get("source_lemma_id") and attempt.get("status") == "lemma_verified"
                            for worker_pass in candidate_second["passes"]
                            for attempt in worker_pass.get("program_obligations", [])))
        self.assertEqual(report["totals"]["candidate"]["correct"], 3)
        readback = self.case.cli("eval", "shadow-lean-status", report["report"]["sha256"])
        self.assertEqual(readback, {key: value for key, value in report.items() if key != "report"})

    @unittest.skipUnless(BASELINE_INSTALL and CANDIDATE_INSTALL, "supply two trusted installed Lean worker builds")
    def test_campaign_pins_installed_builds_and_common_schema(self):
        import psycopg

        anchors = [self.case.anchor(f"Installed build comparison {index}", proposition, [], sensitivity="public")[0]
                   for index, proposition in enumerate(("True", "True ∧ True"))]
        twins = self.restore_pair(self.backup(), case_id="builds")
        manifest = self.case.document("paired_installed_campaign", {
            "format_version": 1, "split": "holdout", "cases": [{
                "id": "builds", "baseline_workspace": str(twins["BASELINE"]),
                "candidate_workspace": str(twins["CANDIDATE"]),
                "statements": [anchor["id"] for anchor in anchors], "expected": ["verified", "verified"],
            }],
        })
        command = ("eval", "shadow-lean-campaign", manifest, "--rounds", 0, "--baseline-install", BASELINE_INSTALL)
        failed = self.case.cli(*command, expect=2)
        self.assertIn("Select both", failed)
        failed = self.case.cli(*command, "--candidate-install", BASELINE_INSTALL, expect=2)
        self.assertIn("identical implementation", failed)
        altered = self.root / "tampered-install"
        copytree(CANDIDATE_INSTALL, altered)
        package = altered / "research_multiplier"
        path = package / "evaluations" / "lean_shadow.py"
        if not path.is_file():
            path = package / "lean_shadow.py"
        path.write_bytes(path.read_bytes() + b"\n# tampered\n")
        failed = self.case.cli(*command, "--candidate-install", altered, expect=2)
        self.assertIn("does not match its recorded files", failed)
        command += ("--candidate-install", CANDIDATE_INSTALL)
        failed = self.case.cli(*command, "--candidate-strategy", "legacy", expect=2)
        self.assertIn("Change exactly one experimental variable", failed)
        with psycopg.connect(self.restored_dsns["CANDIDATE"]) as db:
            actual, = db.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
            db.execute("UPDATE public.rm_alembic_version SET version_num = 'different'")
        try:
            failed = self.case.cli(*command, expect=2)
            self.assertIn("same PostgreSQL schema revision", failed)
        finally:
            with psycopg.connect(self.restored_dsns["CANDIDATE"]) as db:
                db.execute("UPDATE public.rm_alembic_version SET version_num = %s", (actual,))
        report = self.case.cli(*command)
        self.assertEqual(report["comparison_mode"], "installed_builds")
        self.assertFalse(report["activation_eligible"])
        self.assertTrue(report["source_unchanged"])
        self.assertEqual(report["database_schema_revision"], actual)
        self.assertEqual(len({value["implementation_sha256"] for value in report["comparison"].values()}), 2)
        self.assertEqual(len({value["installation"]["installation_sha256"] for value in report["comparison"].values()}), 2)
        for side in ("baseline", "candidate"):
            self.assertEqual(report["totals"][side]["reserved_calls"], 0)
            self.assertEqual(report["totals"][side]["correct"], 2,
                             [step["sides"][side] for step in report["cases"][0]["steps"]])
            for step in report["cases"][0]["steps"]:
                run = step["sides"][side]
                self.assertEqual(run["implementation_sha256"], report["comparison"][side]["implementation_sha256"])
                self.assertTrue(run["receipt_artifacts"])
        review = self.case.document("build_comparison_notes", {"format_version": 1, "cases": []})
        denied = self.case.cli("eval", "lean-activate", report["report"]["sha256"], review,
                               "--actor", "independent reviewer", "--reason", "check diagnostic limit", expect=2)
        self.assertIn("cross-build Lean report is diagnostic", denied)
        readback = self.case.cli("eval", "shadow-lean-status", report["report"]["sha256"])
        self.assertEqual(readback, {key: value for key, value in report.items() if key != "report"})

    def drop_database(self, name):
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.control_dsn, autocommit=True) as db:
            db.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
