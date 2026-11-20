"""Check the offline adapter, admission, and exact dependency replay with real Lean."""

from hashlib import sha256
from importlib.resources import files
import json
from shutil import which
import unittest

import test_lean_flow as native
import test_final_local_lemmas as local

from research_multiplier.proof.lean_decomposition import conditional_route_premises
from research_multiplier.sources.formalpedia_lean import ExportedTheorem
from research_multiplier.workflows.formalpedia_replay import _check_types, exported_proposition

ENVIRONMENT = "0df444a"


class ExportedStatementTests(unittest.TestCase):
    def test_binders_and_comments(self):
        source = """-- theorem stub from the published export
import Mathlib
/- /- nested -/ comment -/
theorem Example.refl (n : Nat) :
    n = n := by
  sorry
"""
        self.assertEqual(exported_proposition(source, "Example.refl"), "∀ (n : Nat), n = n")
        with self.assertRaisesRegex(ValueError, "exact catalog name"):
            exported_proposition(source, "Example.other")
        with self.assertRaisesRegex(ValueError, "scoped declarations"):
            exported_proposition(source.replace("import Mathlib", "open Nat"), "Example.refl")
        with self.assertRaisesRegex(ValueError, "external definitions"):
            exported_proposition(source.replace("import Mathlib", "import Definitions.Def_example"), "Example.refl")
        with self.assertRaisesRegex(ValueError, "statement-only stub"):
            exported_proposition(source.replace("sorry", "rfl"), "Example.refl")


    def test_quantified_route_preserves_binders_and_context(self):
        self.assertEqual(
            conditional_route_premises(
                "∀ (n : Nat), n = 0 → True → (True ∧ True)",
                "∀ (k : Nat), True → True ∧ True",
            ),
            ("∀ (n : Nat), (True) → (n = 0)",),
        )
        self.assertEqual(
            conditional_route_premises("∀ (n : Nat), False → False ∧ True", "∀ (k : Bool), False ∧ True"),
            (),
        )

    @unittest.skipUnless(which("lean") and native.DSN, "requires pinned Lean in the native test environment")
    def test_source_namespace_does_not_resolve_to_a_root_definition(self):
        from research_multiplier.lean_native_runtime import NativeLean

        class ScopedTypes:
            modules = []

            def validate_types(self, sources, commands):
                name = "RMFormalpediaTypes.lean"
                header, body = sources[name].split(b"set_option autoImplicit", 1)
                definitions = """def scopeMeaning : Prop := True
namespace Published
def scopeMeaning : Prop := True ∧ True
end Published
""".encode("utf-8")
                audit = files("research_multiplier").joinpath("lean_support", "TheoryAudit.lean").read_bytes()
                return NativeLean(which("lean")).run({
                    **sources, "TheoryAudit.lean": audit,
                    name: header + definitions + b"set_option autoImplicit" + body,
                }, commands)

        upstream = ExportedTheorem("scopeMeaning", "Published", ())
        public_proof = ExportedTheorem("True ∧ True", None, ())
        self.assertTrue(_check_types(ScopedTypes(), [(upstream, public_proof, "True ∧ True")]))
        with self.assertRaisesRegex(ValueError, "did not match the exported proposition"):
            _check_types(ScopedTypes(), [(upstream, public_proof, "scopeMeaning")])


@unittest.skipUnless(native.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class FormalpediaReplayTests(unittest.TestCase):
    def setUp(self):
        self.case = local.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.cli = self.case.cli
        self.root = self.case.fixture.output.parent.parent / "inputs" / "public-snapshot"
        self.env_dir = self.root / "envs" / ENVIRONMENT
        for part in ("Theorems", "Solutions"):
            (self.env_dir / part).mkdir(parents=True)
        (self.root / "MANIFEST.json").write_text(json.dumps({
            "license": "Apache-2.0", "generated_at": "2026-11-16T06:00:04.546257+00:00",
            "environments": [{"dir": ENVIRONMENT, "mathlib_rev": ENVIRONMENT + "0" * 33,
                              "toolchain": "leanprover/lean4:v4.33.1"}],
        }), encoding="utf-8")

    def source(self, name, proposition, proof):
        stub = f"Theorems/Thm_{name}.lean"
        solution = f"Solutions/Sol_{name}.lean"
        (self.env_dir / stub).write_text(f"theorem {name} : {proposition} := by sorry\n", encoding="utf-8")
        body = f"theorem solution : {proposition} := {proof}\n".encode()
        (self.env_dir / solution).write_bytes(b"-- Prove2me | solution 1\n-- author: @fixture\n\n" + body)
        row = {
            "theorem_name": name, "module": f"Theorems.Thm_{name}", "id": f"theorem-{name}",
            "env": ENVIRONMENT, "status": "Proved", "title": f"Fixture {name}", "author": "@fixture",
            "statement_path": f"envs/{ENVIRONMENT}/{stub}",
            "solutions": [{"id": f"solution-{name}", "status": "ACCEPTED", "proof_type": "prove",
                           "sha256": sha256(body).hexdigest(), "path": f"envs/{ENVIRONMENT}/{solution}"}],
        }
        with (self.env_dir / "index-000.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")

    def replay(self, anchor, nodes, *, proof=None, expect=0, finish=True):
        data = {"format_version": 1, "nodes": nodes}
        if proof is not None:
            data["goal_proof"] = proof
        plan = self.case.document("formalpedia-port", data)
        flags = ("--finish",) if finish else ()
        return self.cli("lean", "replay-formalpedia", anchor["id"], plan,
                        "--snapshot", self.root, "--environment", ENVIRONMENT, *flags, expect=expect)

    def test_chain_needs_no_approval_and_final_receipt_keeps_real_dependency(self):
        reflexive = "∀ n : Nat, n = n"
        joined = f"({reflexive}) ∧ True"
        self.source("First", reflexive, "fun _ => rfl")
        self.source("Second", joined, "And.intro (fun _ => rfl) True.intro")
        matches = self.cli("literature", "formalpedia", "Fixture", "--snapshot", self.root, "--proved-only")
        self.assertEqual(len(matches["matches"]), 2)
        anchor, scope, _ = self.case.anchor("use-external-lemma", f"({joined}) ∧ True", [])
        before = self.cli("research", "basis", anchor["packet_id"])
        nodes = [
            {"name": "First", "theorem": "First", "proof": local.intro(1, {"rfl": True})},
            {"name": "Second", "theorem": "Second", "proof":
             {"and": [{"node_ref": "First"}, {"trivial": True}]}},
        ]
        changed = [{**nodes[0], "local_proposition": "True"}]
        rejected = self.replay(anchor, changed, expect=2)
        self.assertIn("did not match the exported proposition", rejected)
        self.assertEqual(self.cli("lean", "local-theorems", anchor["id"]), [])
        statement_file = self.env_dir / "Theorems" / "Thm_First.lean"
        original = statement_file.read_text(encoding="utf-8")
        statement_file.write_text("theorem First : True := by sorry\n", encoding="utf-8")
        try:
            mismatch = self.replay(anchor, [nodes[0]], expect=2)
            self.assertIn("did not match the exported proposition and proof type", mismatch)
            self.assertEqual(self.cli("lean", "local-theorems", anchor["id"]), [])
        finally:
            statement_file.write_text(original, encoding="utf-8")
        human = self.case.fixture.env.pop("RM_HUMAN_DSN")
        try:
            result = self.replay(anchor, nodes)
            nodes[0]["local_proposition"] = reflexive
            repeat = self.replay(anchor, nodes)
        finally:
            self.case.fixture.env["RM_HUMAN_DSN"] = human
        first, second = result["lemmas"]
        self.assertEqual(second["body"]["local_lemma_ids"], [first["id"]])
        self.assertEqual(result["goal"]["local_lemma_ids"], [second["id"]])
        self.assertEqual(result["goal"]["evidence_id"], repeat["goal"]["evidence_id"])
        self.assertTrue(result["goal"]["accepted"])
        self.assertEqual(self.cli("research", "basis", anchor["packet_id"]), before)
        self.assertEqual(len(self.cli("lean", "formalpedia-sources", anchor["id"])), 2)
        self.cli("research", "retire-input", "source", first["body"]["candidate"]["sha256"],
                 "--reason", "Withdraw an imported working lemma.")
        packet = self.cli("review", "packet", result["goal"]["evidence_id"])
        self.assertTrue(packet["verification"]["needs_recheck"])

    def test_verified_conditional_leaves_only_its_missing_application_premise(self):
        self.source("Conditional", "∀ (n : Nat), False → False ∧ True", "fun _ h => ⟨h, True.intro⟩")
        anchor, scope, _ = self.case.anchor("conditional-application", "∀ (k : Nat), False ∧ True", [])
        before = self.cli("research", "basis", anchor["packet_id"])
        nodes = [{"name": "Conditional", "theorem": "Conditional", "proof": local.intro(2, {
            "and": [{"use": 1}, {"trivial": True}],
        })}]
        result = self.replay(anchor, nodes, expect=2)
        self.assertEqual(result["status"], "needs_basis_review")
        self.assertEqual(result["lemmas"][0]["status"], "verified")
        duplicate = self.replay(anchor, nodes, expect=2)
        self.assertEqual(result["blockers"], duplicate["blockers"])
        self.assertEqual(result["blockers"][0]["obligations"], [
            {"kind": "premise", "proposition": "∀ (n : Nat), (False)"},
        ])
        rendered = self.cli("research", "resume", scope["claim_id"], "--format", "text")
        self.assertIn("False", rendered)
        self.assertEqual(self.cli("research", "basis", anchor["packet_id"]), before)
