"""Exercise the real Lean checker and PostgreSQL admission through the CLI."""

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from research_multiplier.proof import packaged_lean
from research_multiplier.storage import paths


PROJECT_PATH = os.environ.get("RM_TEST_LEAN_PROJECT")
PROJECT = Path(PROJECT_PATH).resolve() if PROJECT_PATH else packaged_lean.read()[0]
DSN = os.environ.get("RM_TEST_DSN")
CASES_DIR = os.environ.get("RM_TEST_CASES_DIR")
FLOW_CASE = Path(CASES_DIR).resolve() / "flow" / "clrs-flow-23.json" if CASES_DIR else None
FLOW_PROJECT = (Path(os.environ.get("RM_TEST_FLOW_PROJECT", FLOW_CASE.parent / "lean")).resolve()
                if FLOW_CASE else None)
FLOW_SETUP = "set RM_TEST_CASES_DIR to the external flow case bundle and prepare its Lean project"


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanFlowIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        from research_multiplier.storage.migrate import upgrade

        upgrade(DSN)
        self.env = os.environ.copy()
        suffix = uuid4().hex[:12]
        with psycopg.connect(DSN) as db:
            for role in ("human", "scheduler", "agent", "verifier"):
                name, password = f"rm_lean_{role}_{suffix}", uuid4().hex
                db.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                    sql.Identifier(name), sql.Literal(password), sql.Identifier(f"rm_{role}")))
                self.addCleanup(self.drop_role, name)
                self.env[f"RM_{role.upper()}_DSN"] = make_conninfo(DSN, user=name, password=password)
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.workspace = paths.initialize_workspace(Path(root.name) / "project")
        self.output = self.workspace / "runs" / "lean"
        self.output.mkdir(exist_ok=True)
        self.env.pop("RM_ARTIFACT_ROOT", None)
        self.env["RM_DATA_ROOT"] = str(self.workspace)
        with psycopg.connect(DSN) as db:
            database, fixture_id = db.execute("SELECT * FROM rm.current_workspace_binding()").fetchone()
            if fixture_id is None:
                fixture_id = paths.ensure_workspace_id(self.workspace)
                db.execute("SELECT rm.bind_research_workspace(%s,%s)", (fixture_id, database))
            else:
                # Each test uses a fresh directory for the same disposable database project.
                (self.workspace / paths.WORKSPACE_ID_FILE).write_text(str(fixture_id) + "\n", encoding="ascii")
            paths.bind_database(self.workspace, database)

    @staticmethod
    def drop_role(name):
        import psycopg
        from psycopg import sql

        with psycopg.connect(DSN) as db:
            db.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))

    def cli(self, *args, expect=0):
        result = subprocess.run([sys.executable, "-m", "research_multiplier", *map(str, args)],
                                env=self.env, capture_output=True, text=True, timeout=200)
        self.assertEqual(result.returncode, expect, result.stderr or result.stdout[:3000])
        output = result.stdout.strip()
        return json.loads(output) if output.startswith(("{", "[")) else output or result.stderr

    def verify(self, statement, mode, *, expect=0):
        return self.cli("lean", "verify", statement, "--candidate", mode, "--project-dir", FLOW_PROJECT,
                        "--out", self.output, expect=expect)

    @unittest.skipUnless(FLOW_CASE, FLOW_SETUP)
    def test_real_proof_scope_dependencies_and_postgres_admission(self):
        packet = self.cli("formalization", "draft", FLOW_CASE,
                          "--title", "CLRS public maximum flow")
        self.cli("formalization", "confirm", packet["id"], "--version", packet["version"],
                 "--hash", packet["packet_sha256"], "--actor", "test researcher",
                 "--reason", "Reviewed real flow and network.")
        statement = self.cli("lean", "draft", packet["id"], "--project-dir", FLOW_PROJECT)
        self.assertIn("→ Real", statement["body"]["theorem_type"])
        self.assertIn("Confirm", self.verify(statement["id"], "real", expect=2))
        self.cli("lean", "confirm", statement["id"], "--version", statement["version"],
                 "--hash", statement["statement_sha256"], "--actor", "test researcher",
                 "--reason", "Reviewed exact theorem and its definitions.")

        integer = self.verify(statement["id"], "integer", expect=2)
        self.assertIsInstance(integer, dict, integer)
        self.assertTrue(integer["compiled"])
        self.assertTrue(integer["kernel_rechecked"])
        self.assertFalse(integer["scope_matches"])
        self.assertFalse(integer["accepted"])
        self.assertIn("→ Int", integer["actual_type"])
        self.assertEqual(integer["dependencies"]["unapproved"], [])
        self.assertNotIn("check_id", integer)
        self.assertEqual(self.cli("lean", "show", statement["id"])["checks"], [])

        axiom = self.verify(statement["id"], "axiom", expect=2)
        self.assertTrue(axiom["kernel_rechecked"])
        self.assertTrue(axiom["scope_matches"])
        self.assertIn("RMFlow.InvalidProof.shortcut", axiom["dependencies"]["unauthorized_axioms"])
        helper = self.verify(statement["id"], "helper", expect=2)
        self.assertTrue(helper["scope_matches"])
        self.assertTrue(any(name.endswith("undisclosedHelper") for name in helper["dependencies"]["unapproved"]))
        self.assertEqual(helper["dependencies"]["unauthorized_axioms"], [])
        extra = self.verify(statement["id"], "extra", expect=2)
        self.assertTrue(extra["kernel_rechecked"])
        self.assertFalse(extra["scope_matches"])

        real = self.verify(statement["id"], "real")
        self.assertTrue(real["kernel_rechecked"])
        self.assertTrue(real["scope_matches"])
        self.assertTrue(real["accepted"])
        actual = real["dependencies"]
        self.assertIn("RMFlow.RealProof.upperBound", actual["direct_constants"])
        self.assertIn("RMFlow.RealProof.attainment", actual["direct_constants"])
        self.assertIn("RMFlow.Certificate.cutBalance", actual["approved_derivations_used"])
        self.assertEqual({row["item"] for row in actual["premise_usage"] if row["in_proof"]},
                         {"H1", "H2", "H3"})
        used = {row["declaration"]: row for row in actual["derivation_local_hypotheses"]}
        for lemma, projection in (("cutBalance", "conservation"), ("upperBound", "bounds")):
            row = used[f"RMFlow.Certificate.{lemma}"]
            self.assertIn({"name": "feasible", "occurs_in_body": True}, row["binders"])
            self.assertIn(f"RMFlow.Expected.Feasible.{projection}", row["direct_constants"])
        receipt, = self.cli("lean", "show", statement["id"])["checks"]
        self.assertEqual(receipt["id"], real["check_id"])
        self.assertEqual(receipt["review_status"], "pending")
        self.cli("lean", "review", receipt["id"], "approve", "--actor", "test researcher",
                 "--reason", "Reviewed accepted proof and actual dependencies.")
        self.assertEqual(self.cli("lean", "show", statement["id"])["checks"][0]["review_status"], "approved")
        self.cli("lean", "review", receipt["id"], "return", "--actor", "test researcher",
                 "--reason", "Return the original receipt.")
        self.assertEqual(self.cli("lean", "show", statement["id"])["checks"][0]["review_status"], "returned")

    @unittest.skipUnless(FLOW_CASE, FLOW_SETUP)
    def test_foundation_reuses_and_invalidates_verified_lean_lemmas(self):
        packet = self.cli("formalization", "draft", FLOW_CASE,
                          "--title", "CLRS versioned dependencies")
        self.cli("formalization", "confirm", packet["id"], "--version", packet["version"],
                 "--hash", packet["packet_sha256"], "--actor", "test researcher",
                 "--reason", "Reviewed proof scope.")
        deterministic = self.cli("formalization", "prove", packet["id"])
        reports = self.cli("research", "evidence", packet["claim_id"])["evidence"]
        receipt = next(item for item in reports if item["id"] == deterministic["evidence_id"])
        self.assertEqual(receipt["kind"], "exact_certificate")
        self.assertIsNone(receipt["audit_attempt_id"])
        facts = self.cli("research", "basis", packet["id"])
        self.assertEqual(len([item for item in facts if item["body"].get("format") == "certificate"]), 2)

        statement = self.cli("lean", "draft", packet["id"], "--project-dir", FLOW_PROJECT)
        self.cli("lean", "confirm", statement["id"], "--version", statement["version"],
                 "--hash", statement["statement_sha256"], "--actor", "test researcher",
                 "--reason", "Reviewed the Lean target.")

        upper = self.cli("lean", "submit", statement["id"], "--target", "upper_bound",
                         "--project-dir", FLOW_PROJECT, "--out", self.output)
        (self.output / "fixed_lean_author.py").write_text(
            """
import json
import os
from research_multiplier.model_adapters import AdapterReply, ModelCapabilities

class Adapter:
    capabilities = ModelCapabilities()

    def complete(self, request):
        context = json.loads(request["messages"][1]["content"])
        assert context["approved_goal"]
        assert {"H1", "H2", "H3"} <= {row["label"] for row in context["frozen_basis"]}
        target = os.environ["RM_TEST_AUTHOR_TARGET"]
        answer = {"format_version": 1, "target": target, "proof": {"cut": ["a", "c", "d", "s"]}}
        return AdapterReply(text=json.dumps(answer), model_id="fixed-model")

def factory(config):
    return Adapter()
""", encoding="utf-8")
        python_path = self.env.get("PYTHONPATH")
        self.env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(self.output), python_path)))
        self.env.update(
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="fixed-author",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL="fixed-model",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="fixed-family",
            RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER="fixed_lean_author:factory",
            RM_TEST_AUTHOR_TARGET="goal",
        )
        author = ("lean", "submit", statement["id"], "--target", "upper_bound",
                  "--model", "--project-dir", FLOW_PROJECT, "--out", self.output)
        self.assertIn("changed the requested Lean target", self.cli(*author, expect=2))
        self.env["RM_TEST_AUTHOR_TARGET"] = "upper_bound"
        self.assertEqual(self.cli(*author, "--retry")["job_id"], upper["job_id"])
        derived = self.cli("lean", "work", upper["job_id"], "--project-dir", FLOW_PROJECT, "--out", self.output)
        self.assertTrue(derived["accepted"])
        version_id = derived["item_version_id"]
        goal = self.cli("lean", "submit", statement["id"], "--project-dir", FLOW_PROJECT, "--out", self.output)
        self.assertEqual(goal["basis_version_ids"], [version_id])
        proven = self.cli("lean", "work", goal["job_id"], "--project-dir", FLOW_PROJECT, "--out", self.output)
        self.assertTrue(proven["accepted"])
        self.assertEqual(proven["basis_version_ids"], [version_id])
        check_id = proven["evidence_id"]
        repeated = self.cli("lean", "work", goal["job_id"], "--project-dir", FLOW_PROJECT, "--out", self.output)
        self.assertEqual(repeated["evidence_id"], check_id)
        tasks = self.cli("research", "tasks", packet["claim_id"])["tasks"]
        recorded = next(task for task in tasks if task["id"] == goal["job_id"])
        self.assertGreaterEqual(recorded["result_reuses"], 1)

        self.cli("review-evidence", check_id, "approve", "--actor", "test researcher",
                 "--reason", "Reviewed the Lean receipt.")
        self.cli("promote-evidence", check_id, "--reason", "Use the reviewed Lean receipt.")
        self.cli("research", "retire-item", version_id, "--reason", "Exercise dependency invalidation.")
        state = self.cli("research", "evidence", packet["claim_id"])
        self.assertEqual(state["claim"][0]["status"], "needs_recheck")
        self.assertTrue(next(item for item in state["evidence"] if item["id"] == check_id)["needs_recheck"])
        self.assertNotIn(version_id, [item["id"] for item in self.cli("research", "basis", packet["id"])])
        self.cli("research", "reopen", packet["claim_id"], "--actor", "test researcher",
                 "--reason", "Schedule a replacement proof.")
        pending = self.cli("lean", "submit", statement["id"], "--target", "attainment",
                           "--project-dir", FLOW_PROJECT, "--out", self.output)
        self.cli("research", "cancel", pending["job_id"], "--actor", "test researcher",
                 "--reason", "Exercise lease cancellation.")
        cancelled = self.cli("lean", "work", pending["job_id"], "--project-dir", FLOW_PROJECT,
                             "--out", self.output, expect=2)
        self.assertIn("cancelled", cancelled)

    def test_researcher_can_prove_a_new_lean_core_goal_without_changing_its_basis(self):
        def document(name, body):
            path = self.output / f"{name}.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            return path

        original = self.cli("research", "propose-item", "proof-example/hypothesis", document("hypothesis", {
            "kind": "hypothesis", "natural": "True holds.", "formal": {"lean": {"proposition": "True"}},
        }))
        self.cli("research", "approve-item", original["id"], "--hash", original["version_sha256"],
                 "--actor", "test researcher", "--reason", "Reviewed the hypothesis.")
        unused = self.cli("research", "propose-item", "proof-example/unused", document("unused", {
            "kind": "hypothesis", "natural": "Zero equals zero.", "formal": {"lean": {"proposition": "0 = 0"}},
        }))
        self.cli("research", "approve-item", unused["id"], "--hash", unused["version_sha256"],
                 "--actor", "test researcher", "--reason", "Reviewed the second hypothesis.")
        lemma = self.cli("research", "propose-item", "proof-example/core-lemma", document("lemma", {
            "kind": "theorem", "natural": "True is inhabited.",
            "formal": {"lean": {"proposition": "True", "declaration": "True.intro"}},
        }))
        self.cli("research", "approve-item", lemma["id"], "--hash", lemma["version_sha256"],
                 "--actor", "test researcher", "--reason", "Reviewed the core theorem.")
        frame = self.cli("goal", "draft", document("frame", {
            "domain": "lean-core", "sensitivity": "public", "title": "Propositional reasoning",
            "objective": "Prove a conjunction.", "basis_version_ids": [original["id"], unused["id"], lemma["id"]],
        }))
        self.cli("goal", "confirm", frame["id"], "--hash", frame["frame_sha256"],
                 "--actor", "test researcher", "--reason", "Reviewed the starting basis.")
        candidate = self.cli("goal", "propose", frame["id"], document("candidate", {
            "statement": "True and True both hold.", "formal": {"lean": {"proposition": "True ∧ True"}},
            "provenance": {"kind": "human"},
        }))
        selected = self.cli("goal", "select", candidate["id"], "--hash", candidate["conjecture_sha256"],
                            "--actor", "test researcher", "--reason", "Reviewed the intended conjunction.")
        proposed = self.cli("lean", "draft-theory", selected["packet_id"])
        self.cli("lean", "confirm", proposed["id"], "--version", proposed["version"],
                 "--hash", proposed["statement_sha256"], "--actor", "test researcher",
                 "--reason", "Reviewed the exact Lean scope.")
        import psycopg
        from psycopg.types.json import Jsonb

        with psycopg.connect(self.env["RM_VERIFIER_DSN"]) as verifier, self.assertRaises(psycopg.errors.CheckViolation):
            verifier.execute("SELECT rm.record_lean_check(%s,%s,%s)", (
                proposed["id"], proposed["statement_sha256"], Jsonb({
                    "theorem": "RMTheory.expected", "candidate_theorem": "RMFlow.RealProof.proof",
                    "trusted_source_sha256": proposed["body"]["trusted_source"]["sha256"],
                    "environment_sha256": proposed["body"]["environment"]["sha256"],
                    "compiled": True, "kernel_rechecked": True, "scope_matches": True, "accepted": True,
                    "candidate": proposed["body"]["trusted_source"], "report": proposed["body"]["trusted_source"],
                    "checker": {"name": "rm-lean-flow-audit", "version": "1"},
                    "dependencies": {"axioms": [], "unauthorized_axioms": [], "unapproved": [], "unsafe": []},
                }),
            ))
        wrong = document("wrong-proof", {"format_version": 1, "proof": {
            "intro": {"count": 2, "body": {"use": 0}},
        }})
        self.assertIn("did not establish", self.cli("lean", "check-theory", proposed["id"], wrong,
                                                    "--out", self.output, expect=2))
        proof = document("proof", {"format_version": 1, "proof": {
            "intro": {"count": 2, "body": {"and": [{"use": 0}, {"ref": lemma["id"]}]}},
        }})
        report = self.cli("lean", "check-theory", proposed["id"], proof, "--out", self.output)
        self.assertTrue(report["accepted"])
        self.assertEqual("True → Eq 0 0 → And True True", report["expected_type"])
        self.assertEqual(report["basis_version_ids"], [lemma["id"]])
        self.assertEqual(report["dependencies"]["premise_usage"], [
            {"item": "H1", "proposition": "True", "occurs_in_proof_term": True},
            {"item": "H2", "proposition": "0 = 0", "occurs_in_proof_term": False},
        ])
        self.assertEqual(report["evidence_id"],
                         self.cli("lean", "check-theory", proposed["id"], proof,
                                  "--out", self.output)["evidence_id"])
        (self.output / "core_author_fixture.py").write_text(
            """
import json
from research_multiplier.model_adapters import AdapterReply, ModelCapabilities

class Adapter:
    capabilities = ModelCapabilities()

    def complete(self, request):
        context = json.loads(request["messages"][1]["content"])
        assert context["antecedents"] == ["True", "0 = 0"]
        assert context["consequent"] == "True ∧ True"
        lemma, = context["approved_theorems"]
        body = {"and": [{"use": 0}, {"ref": lemma["version_id"]}]}
        result = {"format_version": 1, "proof": {"intro": {"count": 2, "body": body}}}
        return AdapterReply(text=json.dumps(result), model_id="fixed-core-model")

def factory(config):
    return Adapter()
""", encoding="utf-8")
        self.env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(self.output), self.env.get("PYTHONPATH"))))
        self.env.update(RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER="fixture-core",
                        RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL="fixed-core-model",
                        RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY="fixture-core-family",
                        RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER="core_author_fixture:factory")
        model = ("lean", "check-theory", proposed["id"], "--model", "--out", self.output)
        self.assertIn("restricted or unclassified basis content", self.cli(*model, expect=2))
        for item in (original, unused, lemma):
            self.cli("research", "release-item", item["id"], "--hash", item["version_sha256"],
                     "--actor", "test researcher", "--reason", "This fixture is approved for public model input.")
        authored = self.cli(*model)
        self.assertEqual(report["evidence_id"], authored["evidence_id"])
        author, = authored["author_attempt_ids"]
        packet = self.cli("review", "packet", report["evidence_id"])
        self.assertEqual(packet["key_steps"]["premises"], report["dependencies"]["premise_usage"])
        self.assertTrue(any(str(row["id"]) == author and row["model_id"] == "fixed-core-model"
                            for row in packet["verification"]["attempts"]))
        self.cli("review-evidence", report["evidence_id"], "approve", "--actor", "test researcher",
                 "--reason", "Reviewed the verifier report.")
        self.cli("promote-evidence", report["evidence_id"], "--reason", "Close the reviewed statement.")
        self.cli("research", "retire-item", lemma["id"], "--reason", "Exercise dependency invalidation.")
        state = self.cli("research", "evidence", selected["claim_id"])
        self.assertEqual(state["claim"][0]["status"], "needs_recheck")

    def test_lean_core_rejects_effectful_proposition_syntax_before_elaboration(self):
        from research_multiplier.general_lean import _check, _environment, _executable, _type_source

        executable = _executable(None)
        auditor, _ = _environment(executable)
        for proposition in ('include_str "/missing/research-multiplier-fixture" = ""',
                            'by_elab pure (Lean.mkConst `True)'):
            with self.subTest(proposition=proposition), self.assertRaisesRegex(ValueError, "unsupported Lean Core syntax"):
                _check(executable, auditor, proposition, [], _type_source(proposition, []), "(True.intro)", {})

    def test_restricted_lean_has_local_exact_refutation_and_requires_human_review(self):
        from hashlib import sha256

        import psycopg

        from research_multiplier.general_lean import _type_source

        def document(name, body):
            path = self.output / f"{name}.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            return path

        def statement(name, goal, basis=()):
            frame = self.cli("goal", "draft", document(name + "-frame", {
                "domain": "lean-core", "sensitivity": "restricted", "title": name,
                "objective": "Check the exact private proposition locally.", "basis_version_ids": list(basis),
            }))
            self.cli("goal", "confirm", frame["id"], "--hash", frame["frame_sha256"],
                     "--actor", "test researcher", "--reason", "Confirm this private scope.")
            candidate = self.cli("goal", "propose", frame["id"], document(name + "-claim", {
                "statement": goal, "formal": {"lean": {"proposition": goal}}, "provenance": {"kind": "human"},
            }))
            selection = self.cli("goal", "select", candidate["id"], "--hash", candidate["conjecture_sha256"],
                                 "--actor", "test researcher", "--reason", "Confirm original claim.")
            draft = self.cli("lean", "draft-theory", selection["packet_id"])
            self.cli("lean", "confirm", draft["id"], "--version", draft["version"], "--hash", draft["statement_sha256"],
                     "--actor", "test researcher", "--reason", "Read the exact Lean statement.")
            return selection["claim_id"], selection["packet_id"], draft["id"]

        hypothesis = self.cli("research", "propose-item", str(uuid4()), document("assumption", {
            "kind": "hypothesis", "natural": "True holds.", "formal": {"lean": {"proposition": "True"}},
        }))
        self.cli("research", "approve-item", hypothesis["id"], "--hash", hypothesis["version_sha256"],
                 "--actor", "test researcher", "--reason", "Approve exact hypothesis.")
        claim, packet, frozen = statement("restricted-false", "False", [hypothesis["id"]])
        self.assertIn("locally supplied structured proof", self.cli("lean", "check-theory", frozen, "--model", expect=2))
        invalid = document("false-term", {"format_version": 1, "proof": {"trivial": True}})
        self.assertIn("did not establish", self.cli("lean", "check-theory", frozen, invalid, "--refute",
                                                    "--out", self.output, expect=2))
        valid = document("refutation", {"format_version": 1, "proof": {
            "intro": {"count": 1, "body": {"apply": [{"use": 0}, {"trivial": True}]}},
        }})
        receipt = self.cli("lean", "check-theory", frozen, valid, "--refute", "--out", self.output)
        self.assertEqual(receipt["evidence_kind"], "counterexample")
        self.assertEqual(receipt["refutation_mode"], "logical_negation")
        self.assertIsNone(receipt["witness"])
        self.assertEqual(receipt["expected_type"], "Not (True → False)")
        self.assertEqual(receipt["dependencies"]["premise_usage"], [])
        self.assertTrue(receipt["dependencies"]["implication_binder_occurs_in_proof"])
        legacy_digest = sha256(_type_source("_root_.Not (\nFalse\n)", ["True"]).encode("utf-8")).hexdigest()
        with psycopg.connect(DSN) as db, self.assertRaisesRegex(psycopg.errors.CheckViolation, "reviewed statement"):
            db.execute("""
                SELECT rm.require_dynamic_lean_job(jsonb_populate_record(j, jsonb_build_object(
                    'payload', j.payload || jsonb_build_object('refutation_source_sha256', %s::text))))
                FROM rm.jobs j JOIN rm.attempts a ON a.job_id = j.id WHERE a.id = %s
            """, (legacy_digest, receipt["attempt_id"]))
        review = self.cli("review", "packet", receipt["evidence_id"])
        self.assertEqual(review["claim_status"], "open")
        self.assertEqual(review["verification"]["review_status"], "pending")
        self.assertEqual(review["contract_comparison"]["verified_result"]["negated_type"], receipt["expected_type"])
        progress = self.cli("advance", "propose", "--for-packet", packet)
        self.cli("advance", "approve", progress["id"], "--hash", progress["policy_sha256"],
                 "--actor", "test researcher", "--reason", "Observe exact outcomes for the frozen packet.")
        state = self.cli("advance", "status", progress["id"])["status"]
        self.assertEqual((state["state"], state["current_evidence_id"]), ("waiting_review", receipt["evidence_id"]))
        worker = ("advance", "worker", "--stage", "verifier", "--policy", progress["id"])
        self.assertEqual(self.cli(*worker)["observations"], 0)
        self.cli("promote-evidence", receipt["evidence_id"], "--reason", "Approval must precede promotion.", expect=2)
        self.cli("review-evidence", receipt["evidence_id"], "approve", "--actor", "test researcher",
                 "--reason", "Reviewed the exact negation and original assumptions.")
        self.assertEqual(self.cli(*worker)["observations"], 1)
        state = self.cli("advance", "status", progress["id"])["status"]
        self.assertEqual((state["disposition"], state["best_evidence_id"]), ("refuted", receipt["evidence_id"]))
        self.assertEqual(self.cli("research", "evidence", claim)["claim"][0]["status"], "open")
        self.cli("promote-evidence", receipt["evidence_id"], "--reason", "Apply reviewed refutation.")
        self.assertEqual(self.cli("research", "evidence", claim)["claim"][0]["status"], "refuted")
        self.cli("research", "retire-item", hypothesis["id"], "--reason", "The original scope needs recheck.")
        self.assertEqual(self.cli("research", "evidence", claim)["claim"][0]["status"], "needs_recheck")

        _, _, private_theorem = statement("restricted-true", "True")
        proof = self.cli("lean", "check-theory", private_theorem, invalid, "--out", self.output)
        self.assertTrue(proof["accepted"])
        self.cli("review-evidence", proof["evidence_id"], "approve", "--actor", "test researcher",
                 "--reason", "Reviewed the private proof.")
        self.cli("promote-evidence", proof["evidence_id"], "--reason", "Apply reviewed private proof.")
        self.assertNotIn(proof["evidence_id"], [row["evidence_id"] for row in self.cli("lean", "theorems")])

    def test_mathlib_checks_reviewed_type_and_reports_actual_library_dependencies(self):
        from research_multiplier import general_lean

        def document(name, body):
            path = self.output / f"{name}.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            return path

        project = self.output / "research-lean"
        build = project / ".lake" / "build" / "lib" / "lean"
        build.mkdir(parents=True)
        (project / ".lake" / "packages").symlink_to(PROJECT / ".lake" / "packages", target_is_directory=True)
        for name in ("lean-toolchain", "lake-manifest.json", "lakefile.toml"):
            shutil.copyfile(PROJECT / name, project / name)
        source = project / "RMNative.lean"
        dependencies = sorted((project / ".lake" / "packages").glob("*/.lake/build/lib/lean"))
        executable = general_lean._executable(None, project)
        compiler_env = {"PATH": os.defpath, "LEAN_SYSROOT": str(executable.parent.parent),
                        "LANG": "C.UTF-8", "LEAN_PATH": os.pathsep.join(map(str, dependencies))}
        if os.name == "nt" and "SYSTEMROOT" in os.environ:
            compiler_env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        original = (
            "import Mathlib.Data.Real.Basic\n"
            "theorem native_square_nonnegative (x : Real) : 0 ≤ x ^ 2 := sq_nonneg x\n"
        )

        def compile_source(text):
            source.write_text(text, encoding="utf-8")
            subprocess.run([str(executable), "-o", str(build / "RMNative.olean"), source.name],
                           cwd=project, env=compiler_env, check=True, capture_output=True, timeout=25)

        compile_source(original)
        module = "RMNative"
        proposition = "∀ x : Real, 0 ≤ x ^ 2"
        item = self.cli("research", "propose-item", "mathlib/native_square_nonnegative", document("library-item", {
            "kind": "theorem", "natural": "The square of a real number is nonnegative.",
            "formal": {"lean": {"proposition": proposition, "declaration": "native_square_nonnegative"}},
        }))
        self.cli("research", "approve-item", item["id"], "--hash", item["version_sha256"],
                 "--actor", "test researcher", "--reason", "Reviewed the real-number theorem and declaration.")
        frame = self.cli("goal", "draft", document("mathlib-frame", {
            "domain": "lean-core", "sensitivity": "public", "title": "Square of a real number",
            "objective": "Prove a nonnegativity result over the reals.", "basis_version_ids": [item["id"]],
        }))
        self.cli("goal", "confirm", frame["id"], "--hash", frame["frame_sha256"],
                 "--actor", "test researcher", "--reason", "Reviewed the research frame.")
        candidate = self.cli("goal", "propose", frame["id"], document("mathlib-candidate", {
            "statement": "Each real number has a nonnegative square.", "provenance": {"kind": "human"},
            "formal": {"lean": {"proposition": proposition}},
        }))
        selected = self.cli("goal", "select", candidate["id"], "--hash", candidate["conjecture_sha256"],
                            "--actor", "test researcher", "--reason", "Reviewed the stated domain.")
        proposed = self.cli("lean", "draft-theory", selected["packet_id"], "--mathlib-project", project,
                            "--import", module)
        self.assertIn("Real", proposed["body"]["elaborated_theorem_type"])
        external = proposed["body"]["environment"]["external"]
        self.assertEqual(external["modules"], [module])
        self.assertRegex(external["project_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn(str(project), json.dumps(proposed["body"]))
        self.cli("lean", "confirm", proposed["id"], "--version", proposed["version"],
                 "--hash", proposed["statement_sha256"], "--actor", "test researcher",
                 "--reason", "Reviewed the actual elaborated real-number statement and its imports.")
        proof = document("native-proof", {"format_version": 1, "proof": {"ref": item["id"]}})
        invalid = document("native-invalid", {"format_version": 1, "proof": {"ref": str(uuid4())}})
        with patch.dict(self.env, {"RM_LEAN_MATHLIB_PROJECT": str(project)}):
            self.assertIn("current promoted proof",
                          self.cli("lean", "check-theory", proposed["id"], invalid, "--out", self.output, expect=2))
        command = ("lean", "check-theory", proposed["id"], proof, "--out", self.output, "--mathlib-project", project)
        report = self.cli(*command)
        compile_source(original + "def nativeBuildChanged : Nat := 1\n")
        self.assertIn("checking environment", self.cli(*command, expect=2))
        self.assertEqual(report["expected_type"], proposed["body"]["elaborated_theorem_type"])
        self.assertEqual(report["basis_version_ids"], [item["id"]])
        self.assertIn({"declaration": "native_square_nonnegative", "module": module},
                      report["dependencies"]["external_constants"])
        self.assertTrue(any(row["declaration"] == "sq_nonneg" and row["module"].startswith("Mathlib.")
                            for row in report["dependencies"]["external_constants"]))
        review = self.cli("review", "packet", report["evidence_id"])
        self.assertEqual(review["key_steps"]["external_constants"], report["dependencies"]["external_constants"])

    def test_derived_conditions_use_the_actual_transitive_basis(self):
        prefix = str(uuid4())

        def document(name, value):
            path = self.output / f"{name}.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return path

        def item(name, kind, proposition, **formal):
            draft = self.cli("research", "propose-item", f"{prefix}/{name}", document(name, {
                "kind": kind, "natural": proposition, "formal": {"lean": {"proposition": proposition, **formal}},
            }))
            self.cli("research", "approve-item", draft["id"], "--hash", draft["version_sha256"],
                     "--actor", "test researcher", "--reason", "Reviewed this formal version.")
            return draft["id"]

        def frame(name, versions):
            result = self.cli("goal", "draft", document(name, {
                "domain": "lean-core", "sensitivity": "public", "title": name,
                "objective": "Check the permitted inputs to a route condition.", "basis_version_ids": versions,
            }))
            self.cli("goal", "confirm", result["id"], "--hash", result["frame_sha256"],
                     "--actor", "test researcher", "--reason", "Reviewed the starting basis.")
            return result["id"]

        def select(name, proposition, versions, attached):
            selected_frame = frame(name, versions)
            proposal = self.cli("goal", "propose", selected_frame, document(f"{name}-candidate", {
                "statement": proposition, "formal": {"lean": {"proposition": proposition}},
                "provenance": {"kind": "human"},
            }))
            selected = self.cli("goal", "select", proposal["id"], "--hash", proposal["conjecture_sha256"],
                                "--actor", "test researcher", "--reason", "Reviewed this proposition.")
            for version in attached:
                self.cli("research", "attach-item", selected["packet_id"], version,
                         "--reason", "Individually reviewed theorem permission in this packet.")
            return selected_frame, selected["packet_id"]

        def proof(name, proposition, versions, tree, *, attached=(), promote=False):
            _, packet = select(name, proposition, versions, attached)
            statement = self.cli("lean", "draft-theory", packet)
            self.cli("lean", "confirm", statement["id"], "--version", statement["version"],
                     "--hash", statement["statement_sha256"], "--actor", "test researcher",
                     "--reason", "Reviewed the checked type.")
            result = self.cli("lean", "check-theory", statement["id"], document(f"{name}-term", {
                "format_version": 1, "proof": tree,
            }), "--out", self.output)
            if promote:
                evidence = result["evidence_id"]
                self.cli("review-evidence", evidence, "approve", "--actor", "test researcher", "--reason", "Reviewed")
                self.cli("promote-evidence", evidence, "--reason", "Record the reviewed theorem.")
                return self.cli("lean", "export-theorem", evidence)["id"]
            return result

        def condition(name, goal, proposition):
            return self.cli("goal", "condition", goal, document(name, {
                "kind": "route_condition", "statement": proposition, "formal": {"lean": {"proposition": proposition}},
                "question": f"Is {proposition} available in this proof route?", "impact": "Required by the next step.",
            }))["id"]

        attached = item("attached", "theorem", "True", declaration="True.intro")
        source_premise = item("source-premise", "hypothesis", "0 = 0")
        source = proof("source", "True", [source_premise], {
            "intro": {"count": 1, "body": {"ref": attached}},
        }, attached=[attached], promote=True)
        direct = proof("direct", "True", [], {"ref": attached}, attached=[attached])
        unused = proof("unused", "True", [], {"trivial": True}, attached=[attached])
        nested_term = {"and": [
            {"apply": [{"ref": source}, {"rfl": True}]}, {"trivial": True},
        ]}
        nested = proof("nested", "True ∧ True", [], nested_term, attached=[attached])
        self.assertEqual(nested["basis_version_ids"], [source])
        self.assertIn(attached, nested["expanded_basis_version_ids"])

        narrower = frame("narrower", [])
        wider = frame("wider", [attached])
        for answer, proposition, label in ((direct, "True", "direct"), (nested, "True ∧ True", "nested")):
            evidence = answer["evidence_id"]
            invalid = condition(f"deny-{label}", narrower, proposition)
            decision = self.cli("goal", "record-derivation", invalid, evidence, expect=2)
            self.assertIn("complete proof basis", decision)
            self.assertIn("complete proof basis", self.cli(
                "goal", "decide-condition", invalid, "derived", "--evidence", evidence,
                "--actor", "test researcher", "--reason", "Check consistent admission", expect=2,
            ))
            valid = condition(f"accept-{label}", wider, proposition)
            self.cli("goal", "record-derivation", valid, evidence)
            status = next(row for row in self.cli("goal", "show", wider)["conditions"] if row["id"] == valid)
            self.assertTrue(status["derivation_current"])

        self.assertEqual(unused["basis_version_ids"], [])
        unused_request = condition("unused-attachment", narrower, "True")
        self.cli("goal", "record-derivation", unused_request, unused["evidence_id"])
        explicit_target, _ = select("explicit-target", "0 = 0", [], [attached])
        explicit_request = condition("explicit-reference", explicit_target, "True ∧ True")
        self.cli("goal", "record-derivation", explicit_request, nested["evidence_id"])

        supporting = proof("supporting-interpretation", "True", [], {"trivial": True})
        interpretation = self.cli("goal", "condition", explicit_target, document("meaning-to-recheck", {
            "kind": "interpretation", "statement": "True", "formal": {"lean": {"proposition": "True"}},
            "question": "Can we discharge this interpretation claim?", "impact": "Required to proceed.",
        }))["id"]
        self.cli("goal", "decide-condition", interpretation, "derived", "--evidence", supporting["evidence_id"],
                 "--actor", "test researcher", "--reason", "Reviewed the supporting exact proof.")
        self.assertTrue(self.cli("goal", "show", explicit_target)["frame"]["proof_ready"])
        self.cli("review-evidence", supporting["evidence_id"], "return",
                 "--actor", "test researcher", "--reason", "Withdraw the supporting proof.")
        held = self.cli("goal", "show", explicit_target)["frame"]
        self.assertTrue(held["interpretation_hold"])
        self.assertFalse(held["proof_ready"])
        self.assertEqual(held["claim_status"], "awaiting_interpretation_review")
        self.assertEqual(held["recorded_claim_status"], "open")
        self.assertIn("Confirm the research packet", self.cli("lean", "draft-theory", held["packet_id"], expect=2))
        entries = self.cli("review", "inbox", "--claim", held["claim_id"])
        self.assertTrue(next(row for row in entries if row["item_id"] == interpretation)["detail"]["requires_reopen"])
        replacement = self.cli("goal", "reopen-condition", interpretation,
                               "--actor", "test researcher", "--reason", "Review withdrawal.")["id"]
        self.cli("goal", "decide-condition", replacement, "reject",
                 "--actor", "test researcher", "--reason", "Keeps the already approved scope.")
        self.assertTrue(self.cli("goal", "show", explicit_target)["frame"]["proof_ready"])
        self.assertEqual(next(row for row in self.cli("goal", "show", explicit_target)["conditions"]
                              if row["id"] == interpretation)["replacement_request_id"], replacement)

        frozen_target_file = document("primary-target", {
            "title": "Primary target with an exact proof", "domain": "lean-core", "sensitivity": "public",
            "statement": "Both propositions hold.", "formal": {"lean": {"proposition": "True ∧ True"}},
        })
        preview = self.cli("intake", "preview-target", frozen_target_file)
        frozen_target = self.cli("intake", "freeze-target", frozen_target_file, "--hash", preview["target_sha256"],
                                 "--actor", "test researcher", "--reason", "Confirm the exact target before the basis.")
        intake = self.cli("intake", "draft", document("primary-basis", {"items": []}), "--target", frozen_target["id"])
        selected = self.cli("intake", "confirm", intake["id"], "--hash", intake["intake_sha256"],
                            "--actor", "test researcher", "--reason", "Confirm the intentionally empty primary basis.")
        statement = self.cli("lean", "draft-theory", selected["packet_id"])
        self.cli("lean", "confirm", statement["id"], "--version", statement["version"], "--hash",
                 statement["statement_sha256"], "--actor", "test researcher", "--reason", "Confirm the exact Lean type.")
        submission = document("primary-proof", {"format_version": 1, "proof": nested_term})
        command = ("lean", "check-theory", statement["id"], submission, "--out", self.output)
        self.assertIn("outside the reviewed basis", self.cli(*command, expect=2))
        self.assertEqual(self.cli("research", "evidence", selected["claim_id"])["evidence"], [])
        self.cli("research", "attach-item", selected["packet_id"], attached,
                 "--reason", "Explicitly reviewed this theorem as a proof input for the primary target.")
        issued = self.cli(*command)
        self.assertTrue(issued["accepted"])
        self.assertEqual(issued["basis_version_ids"], [source])
        self.assertIn(attached, issued["expanded_basis_version_ids"])

        self.cli("research", "retire-item", attached, "--reason", "Withdraw the cited version.")
        statuses = self.cli("goal", "show", wider)["conditions"]
        self.assertFalse(any(row["derivation_current"] for row in statuses))
        self.assertTrue(next(row for row in self.cli("goal", "show", narrower)["conditions"]
                             if row["id"] == unused_request)["derivation_current"])

    def test_lean_core_replays_proven_theorems_between_claims_with_original_antecedents(self):
        def document(name, value):
            path = self.output / f"{name}.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return path

        def item(kind, name, proposition, **formal):
            draft = self.cli("research", "propose-item", key_prefix + name, document(name, {
                "kind": kind, "natural": proposition, "formal": {"lean": {"proposition": proposition, **formal}},
            }))
            self.cli("research", "approve-item", draft["id"], "--hash", draft["version_sha256"],
                     "--actor", "test researcher", "--reason", "Reviewed this exact item.")
            return draft["id"]

        def statement(name, proposition, items):
            frame = self.cli("goal", "draft", document(name + "-frame", {
                "domain": "lean-core", "sensitivity": "public", "title": name,
                "objective": "Check a portable Lean proof.", "basis_version_ids": items,
            }))
            self.cli("goal", "confirm", frame["id"], "--hash", frame["frame_sha256"],
                     "--actor", "test researcher", "--reason", "Reviewed the frame.")
            candidate = self.cli("goal", "propose", frame["id"], document(name + "-candidate", {
                "statement": proposition, "formal": {"lean": {"proposition": proposition}},
                "provenance": {"kind": "human"},
            }))
            selected = self.cli("goal", "select", candidate["id"], "--hash", candidate["conjecture_sha256"],
                                "--actor", "test researcher", "--reason", "Reviewed the intended proposition.")
            proposed = self.cli("lean", "draft-theory", selected["packet_id"])
            self.cli("lean", "confirm", proposed["id"], "--version", proposed["version"],
                     "--hash", proposed["statement_sha256"], "--actor", "test researcher",
                     "--reason", "Reviewed the exact Lean scope.")
            return proposed["id"], selected["claim_id"]

        def check(identifier, name, proof, *, expect=0):
            return self.cli("lean", "check-theory", identifier,
                            document(name, {"format_version": 1, "proof": proof}),
                            "--out", self.output, expect=expect)

        def promote(report):
            self.cli("review-evidence", report["evidence_id"], "approve", "--actor", "test researcher",
                     "--reason", "Reviewed the exact proof.")
            self.cli("promote-evidence", report["evidence_id"], "--reason", "Close the reviewed statement.")

        key_prefix = str(uuid4()) + "/"
        assumption = item("hypothesis", "false", "False")
        elimination = item("theorem", "elimination", "False → (0 = 1)", declaration="False.elim")
        source_id, source_claim = statement("Source", "0 = 1", [assumption, elimination])
        source = check(source_id, "source-proof", {"intro": {
            "count": 1, "body": {"apply": [{"ref": elimination}, {"use": 0}]},
        }})
        self.assertEqual(self.cli("lean", "export-theorem", source["evidence_id"], expect=2).strip(),
                         "Error: Only the current promoted exact Lean Core receipt can be exported.")
        dest_id, dest_claim = statement("Destination", "(0 = 1) ∧ True", [assumption])
        graph = document("blueprint", {"format_version": 1, "nodes": [
            {"name": "destination", "statement_id": dest_id, "proof": {"intro": {"count": 1, "body": {"and": [
                {"apply": [{"node_ref": "source"}, {"use": 0}]}, {"trivial": True},
            ]}}}},
            {"name": "source", "statement_id": source_id},
        ]})
        preview = self.cli("lean", "blueprint", graph)
        self.assertEqual([(node["name"], node["status"]) for node in preview["nodes"]],
                         [("source", "awaiting_evidence_review"), ("destination", "waiting_for_theorems")])
        cycle = document("cycle", {"format_version": 1, "nodes": [
            {"name": "source", "statement_id": source_id, "proof": {"node_ref": "destination"}},
            {"name": "destination", "statement_id": dest_id, "proof": {"node_ref": "source"}},
        ]})
        self.assertIn("contains a cycle", self.cli("lean", "blueprint", cycle, expect=2))
        promote(source)
        exported, = [row for row in self.cli("lean", "theorems") if row["evidence_id"] == source["evidence_id"]]
        version = exported["id"]
        self.assertEqual(exported["body"]["formal"]["lean"]["antecedents"], ["False"])
        self.assertEqual(self.cli("lean", "export-theorem", source["evidence_id"])["id"], version)

        valid = {"intro": {"count": 1, "body": {"and": [
            {"apply": [{"ref": version}, {"use": 0}]}, {"trivial": True},
        ]}}}
        stored_document = self.workspace / "objects" / exported["body"]["source"]["candidate"]["sha256"]
        original_bytes = stored_document.read_bytes()
        try:
            stored_document.write_bytes(b'{"submission":{"trivial":true}}')
            self.assertIn("artifact checksum failed", check(dest_id, "tampered-proof", valid, expect=2))
        finally:
            stored_document.write_bytes(original_bytes)
        work = self.cli("lean", "blueprint", graph, "--work", "--out", self.output)
        self.assertEqual(work["nodes"][1]["status"], "awaiting_evidence_review")
        proof = check(dest_id, "dest-proof", valid)
        self.assertEqual(work["nodes"][1]["evidence_id"], proof["evidence_id"])
        self.assertEqual(proof["basis_version_ids"], [version])
        self.assertCountEqual(proof["expanded_basis_version_ids"], [version, elimination])
        promote(proof)

        missing_id, _ = statement("Missing assumption", "0 = 1", [])
        self.assertIn("did not establish", check(missing_id, "missing-proof", {"ref": version}, expect=2))
        self.cli("research", "retire-item", elimination, "--reason", "Exercise cross-claim dependency invalidation.")
        for claim in (source_claim, dest_claim):
            state = self.cli("research", "evidence", claim)
            self.assertEqual(state["claim"][0]["status"], "needs_recheck")
        self.assertNotIn(version, [row["id"] for row in self.cli("lean", "theorems")])


if __name__ == "__main__":
    unittest.main()
