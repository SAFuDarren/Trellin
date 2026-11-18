"""Check proof-only rebuilds and meaningful Lean scope changes end to end."""

from pathlib import Path
import os
import subprocess
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LakeScopeTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli
        self.project = self.case.fixture.workspace.parent / "external-lake"
        self.project.mkdir()
        (self.project / "lean-toolchain").write_text("leanprover/lean4:v4.19.0\n", encoding="utf-8")
        (self.project / "lakefile.toml").write_text(
            'name = "scope_check"\n[[lean_lib]]\nname = "ScopeBase"\n[[lean_lib]]\nname = "ScopeProof"\n',
            encoding="utf-8",
        )

    def build(self, *, scale=1, approved="True ∧ True", body="⟨rfl, ScopeBase.known⟩",
              consequent="(ScopeBase.scale = ScopeBase.scale) ∧ (True ∧ True)"):
        first = "⟨True.intro, True.intro⟩" if approved == "True ∧ True" else "True.intro"
        (self.project / "ScopeBase.lean").write_text(
            f"namespace ScopeBase\ndef scale : Nat := {scale}\ntheorem known : {approved} := {first}\n"
            "end ScopeBase\n", encoding="utf-8",
        )
        (self.project / "ScopeProof.lean").write_text(
            f"import ScopeBase\ntheorem rebuildProof (premise : True) : {consequent} := {body}\n",
            encoding="utf-8",
        )
        built = subprocess.run(["lake", "build", "ScopeProof"], cwd=self.project, text=True,
                               capture_output=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

    def read_proof(self):
        return (self.project / ".lake" / "build" / "lib" / "lean" / "ScopeProof.olean").read_bytes()

    def test_proof_rebuild_reverifies_but_changed_meaning_requires_review(self):
        self.build()
        choices = [
            {"kind": "hypothesis", "natural": "The frozen starting premise is true.",
             "formal": {"lean": {"proposition": "True"}}},
            {"kind": "theorem", "natural": "The reviewed result is a conjunction of truths.",
             "formal": {"lean": {"proposition": "True ∧ True", "declaration": "ScopeBase.known"}}},
        ]
        with patch.object(database, "PROJECT", self.project):
            anchor, scope, options = self.case.anchor(
                "lake-proof-rebuild-scope", "(ScopeBase.scale = ScopeBase.scale) ∧ (True ∧ True)",
                choices, module="ScopeProof",
            )
        proof = ("lean", "check-source", anchor["id"], "--module", "ScopeProof",
                 "--declaration", "rebuildProof", *options)
        reviewed = anchor["body"]["environment"]["sha256"]
        original_build = self.read_proof()

        # The first submission already uses changed proof bytes, with no human login.
        self.build(body="⟨rfl, (fun (_ : True) => ScopeBase.known) premise⟩")
        self.assertNotEqual(self.read_proof(), original_build)
        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            regenerated = self.cli(*proof)
            self.assertTrue(regenerated["accepted"] and regenerated["scope_matches"])
            self.assertNotEqual(regenerated["lake_validation"]["runtime_sha256"], reviewed)
            self.assertTrue(regenerated["dependencies"]["premise_usage"][0]["occurs_in_proof_term"])
            self.assertEqual(regenerated["evidence_id"], self.cli(*proof)["evidence_id"])
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human

        self.build(consequent="True", body="premise")
        wrong = self.cli(*proof, expect=2)
        self.assertEqual(wrong["status"], "rejected")
        self.assertTrue(wrong["kernel_rechecked"])
        self.assertFalse(wrong["scope_matches"])

        self.build(scale=2)
        different_definition = self.cli(*proof, expect=2)
        self.assertEqual(different_definition["status"], "lake_scope_review_required")
        self.assertIn({"kind": "frozen_goal_or_premises"}, different_definition["changes"])

        self.build(approved="True", body="⟨rfl, ⟨premise, ScopeBase.known⟩⟩")
        different_basis = self.cli(*proof, expect=2)
        version, = [row["id"] for row in self.cli("research", "basis", anchor["packet_id"])
                    if row["body"]["kind"] == "theorem"]
        self.assertEqual(different_basis["changes"], [
            {"kind": "approved_theorem", "declaration": "ScopeBase.known", "version_id": version},
        ])

        packet = self.cli("review", "packet", regenerated["evidence_id"])
        self.assertEqual(packet["claim_id"], scope["claim_id"])
        self.assertEqual(packet["key_steps"]["lake_validation"], regenerated["lake_validation"])
        self.assertEqual(len(self.cli("lean", "show", anchor["id"])["checks"]), 1)
        with patch.dict(os.environ, self.case.fixture.env), Store() as store:
            attempt, = store.query("verifier", """
                SELECT a.model_provider, j.budget ->> 'max_model_calls' AS budget
                FROM rm.attempts a JOIN rm.jobs j ON j.id = a.job_id WHERE a.id = %s
            """, regenerated["attempt_id"])
        self.assertEqual(attempt, {"model_provider": None, "budget": "0"})
