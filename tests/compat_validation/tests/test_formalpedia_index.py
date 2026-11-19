"""Test selection and integrity against the Formalpedia export layout."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_multiplier.sources.formalpedia import FormalpediaError, FormalpediaSnapshot, replay_identity
from research_multiplier.sources.formalpedia_lean import exported_solution_type, exported_theorem


ENV = "c5ea003"
EXTERNAL_ENV = "0df444a"
NAME = "Fixture.Order.reflexivity"
BODY = "theorem solution (n : Nat) : n = n := by rfl\n".encode()
HEADER = b"-- Prove2me | solution 1 for Fixture.Order.reflexivity\n-- author  : @fixture\n\n"
SOLUTION = {
    "id": "chosen-proof", "status": "ACCEPTED", "proof_type": "prove",
    "path": f"envs/{ENV}/Solutions/Sol_fixture.lean", "sha256": hashlib.sha256(BODY).hexdigest(),
    "author": "@fixture", "url": "https://prove2.me/submissions/chosen-proof",
}
STATEMENT = {
    "id": "chosen-theorem", "theorem_name": NAME, "module": "Theorems.Thm_fixture",
    "title": "Natural number reflexivity", "env": ENV, "status": "Proved", "solutions": [SOLUTION],
    "statement_path": f"envs/{ENV}/Theorems/Thm_fixture.lean", "url": "https://prove2.me/theorems/chosen-theorem",
}


class FormalpediaIndexTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "snapshot"
        self.manifest = {
            "license": "Apache-2.0", "generated_at": "2026-11-16T06:00:04.546257+00:00",
            "environments": [
                {"dir": ENV, "mathlib_rev": ENV + "0" * 33, "toolchain": "leanprover/lean4:v4.30.0"},
                {"dir": EXTERNAL_ENV, "mathlib_rev": EXTERNAL_ENV + "0" * 33, "toolchain": "leanprover/lean4:v4.33.1"},
            ],
        }
        self.write("MANIFEST.json", json.dumps(self.manifest).encode())
        self.write(SOLUTION["path"], HEADER + BODY)
        self.write(STATEMENT["statement_path"], b"theorem fixture (n : Nat) : n = n := by sorry\n")
        self.write_index(ENV, [STATEMENT])
        self.write_index(EXTERNAL_ENV, [{**STATEMENT, "env": EXTERNAL_ENV, "id": "other-id", "solutions": [],
                                         "status": "Open"}])

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def write_index(self, environment, entries, shard=0):
        content = "".join(json.dumps(item) + "\n" for item in entries).encode()
        return self.write(f"envs/{environment}/index-{shard:03}.jsonl", content)

    def test_search_is_local_and_deterministic_across_shards_and_environments(self):
        self.write_index(ENV, [{**STATEMENT, "id": "alternative", "module": "Theorems.Thm_alternative",
                                "theorem_name": f"{NAME}.alternative"}], shard=1)
        snapshot = FormalpediaSnapshot(self.root)
        with patch("socket.socket", side_effect=AssertionError("unexpected network access")):
            matches = snapshot.search(NAME, limit=2)
            self.assertEqual({result["id"] for result in matches}, {"chosen-theorem", "other-id"})
            self.assertEqual(matches, snapshot.search(NAME.upper(), limit=2))
            self.assertEqual([record["id"] for record in snapshot.search("number reflexivity", environment=ENV)],
                             ["chosen-theorem", "alternative"])
            self.assertEqual(len(snapshot.search(NAME, proved_only=True)), 2)
        self.assertTrue(snapshot.entry(ENV, "Theorems.Thm_fixture")["replay_candidate"])
        self.assertFalse(snapshot.entry(EXTERNAL_ENV, NAME)["replay_candidate"])
        with self.assertRaisesRegex(FormalpediaError, "limit"):
            snapshot.search("reflexivity", limit=201)

    def test_solution_verifies_original_body_and_records_exact_source_identities(self):
        snapshot = FormalpediaSnapshot(self.root)
        result = snapshot.solution(ENV, "chosen-theorem", solution_id="chosen-proof")
        self.assertEqual(result["body"].encode(), BODY)
        self.assertEqual(result["solution"]["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(result["file_sha256"], hashlib.sha256(HEADER + BODY).hexdigest())
        self.assertEqual(result["entry"]["license"], "Apache-2.0")
        self.assertEqual(result["entry"]["manifest_sha256"], hashlib.sha256((self.root / "MANIFEST.json").read_bytes()).hexdigest())
        self.assertIn("sorry", snapshot.statement(ENV, NAME)["text"])
        self.write(SOLUTION["path"], HEADER + BODY + b"\n")
        with self.assertRaisesRegex(FormalpediaError, "SHA-256"):
            snapshot.solution(ENV, NAME)

    def test_unfiltered_search_accepts_partial_snapshots(self):
        absent = self.root / f"envs/{EXTERNAL_ENV}/index-000.jsonl"
        absent.unlink()
        snapshot = FormalpediaSnapshot(self.root)
        self.assertEqual([entry["id"] for entry in snapshot.search(NAME)], ["chosen-theorem"])
        with self.assertRaisesRegex(FormalpediaError, f"No local index shards.*{EXTERNAL_ENV}"):
            snapshot.search(NAME, environment=EXTERNAL_ENV)
        (self.root / f"envs/{ENV}/index-000.jsonl").unlink()
        with self.assertRaisesRegex(FormalpediaError, "No local index shards for any environment"):
            snapshot.search(NAME)

    def test_candidate_identity_binds_statement_index_and_full_solution(self):
        snapshot = FormalpediaSnapshot(self.root)

        def identity():
            selection, = snapshot.replay_sources(ENV, [(NAME, "chosen-proof")])
            return replay_identity(*selection)

        original = identity()
        self.write(STATEMENT["statement_path"], b"theorem fixture : True := by sorry\n")
        changed_statement = identity()
        self.assertNotEqual(original["statement_sha256"], changed_statement["statement_sha256"])
        self.assertEqual(original["snapshot"], changed_statement["snapshot"])
        self.assertEqual(original["solution_sha256"], changed_statement["solution_sha256"])

        self.write_index(ENV, [{**STATEMENT, "title": "Revised catalog title"}])
        changed_index = identity()
        self.assertNotEqual(changed_statement["index_entry_sha256"], changed_index["index_entry_sha256"])

        self.write(SOLUTION["path"], HEADER.replace(b"@fixture", b"@other") + BODY)
        changed_header = identity()
        self.assertNotEqual(changed_index["solution_file_sha256"], changed_header["solution_file_sha256"])
        self.assertEqual(changed_index["solution_sha256"], changed_header["solution_sha256"])

    def test_batch_reads_index_once_and_preserves_selection_order(self):
        other = {**STATEMENT, "id": "other", "module": "Theorems.Thm_other", "theorem_name": "Fixture.Other"}
        self.write_index(ENV, [STATEMENT, other])
        snapshot = FormalpediaSnapshot(self.root)
        with patch.object(snapshot, "_index_rows", wraps=snapshot._index_rows) as read_index:
            sources = snapshot.replay_sources(ENV, [("other", None), (NAME, "chosen-proof")])
            read_index.assert_called_once_with(ENV)
        self.assertEqual([proof["entry"]["id"] for proof, _ in sources], ["other", "chosen-theorem"])
        self.assertEqual([statement["entry"]["id"] for _, statement in sources], ["other", "chosen-theorem"])

    def test_refuses_open_accepted_sketch_or_refutation_as_a_proof(self):
        choices = [
            {**STATEMENT, "status": "Open"},
            {**STATEMENT, "solutions": [{**SOLUTION, "status": "SKETCH_ACCEPTED"}]},
            {**STATEMENT, "solutions": [{**SOLUTION, "proof_type": "disprove"}]},
        ]
        for entry in choices:
            with self.subTest(entry=entry):
                self.write_index(ENV, [entry])
                snapshot = FormalpediaSnapshot(self.root)
                self.assertEqual(snapshot.search(NAME, proved_only=True), [])
                with self.assertRaisesRegex(FormalpediaError, "No fully accepted"):
                    snapshot.solution(ENV, NAME)

    def test_rejects_path_escape_and_an_unmatched_environment(self):
        snapshot = FormalpediaSnapshot(self.root)
        outside = self.root.parent / "outside.lean"
        outside.write_bytes(HEADER + BODY)
        local = self.root / SOLUTION["path"]
        local.unlink()
        local.symlink_to(outside)
        with self.assertRaisesRegex(FormalpediaError, "inside the snapshot"):
            snapshot.solution(ENV, NAME)
        self.write_index(ENV, [{**STATEMENT, "solutions": [{**SOLUTION, "path": f"envs/{EXTERNAL_ENV}/Solutions/a.lean"}]}])
        with self.assertRaisesRegex(FormalpediaError, "source must be"):
            snapshot.solution(ENV, NAME)
        with self.assertRaisesRegex(FormalpediaError, "Unknown Formalpedia environment"):
            snapshot.entry("../snapshot", NAME)

    def test_rejects_ambiguous_exact_identity(self):
        self.write_index(ENV, [STATEMENT, {**STATEMENT, "id": "another-id"}])
        with self.assertRaisesRegex(FormalpediaError, "Ambiguous"):
            FormalpediaSnapshot(self.root).entry(ENV, NAME)


class FormalpediaLeanInputTests(unittest.TestCase):
    def test_statement_preserves_namespace_and_imports(self):
        statement = exported_theorem(
            "import Mathlib\nimport Theorems.Thm_previous\nnamespace Fixture.Scope\n"
            "theorem ref (a : Nat) : localMeaning a := by /- nested /- comment -/ -/ sorry\n"
            "end Fixture.Scope",
            "Fixture.Scope.ref",
        )
        self.assertEqual(statement.namespace, "Fixture.Scope")
        self.assertEqual(statement.imports, ("Mathlib", "Theorems.Thm_previous"))
        self.assertEqual(statement.proposition, "∀ (a : Nat), localMeaning a")

    def test_solution_type_preserves_binder_colons_and_stops_at_the_proof(self):
        solution = exported_solution_type(
            "import Mathlib\n"
            "theorem solution {a : Nat} (h : (a : Nat) = a) : a = a := by\n"
            "  arbitrary_tactic_arbitrary_code\n"
        )
        self.assertIsNone(solution.namespace)
        self.assertEqual(solution.imports, ("Mathlib",))
        self.assertEqual(solution.proposition, "∀ {a : Nat} (h : (a : Nat) = a), a = a")

    def test_unsupported_lexical_controls_remain_diagnostic(self):
        with self.assertRaisesRegex(ValueError, "scoped declarations"):
            exported_solution_type("open Scoped in\ntheorem solution : True := by trivial")
        with self.assertRaisesRegex(ValueError, "external definitions"):
            exported_theorem("import Definitions.External\ntheorem X : True := by sorry", "X")
        with self.assertRaisesRegex(ValueError, "declared namespace"):
            exported_theorem("namespace A\ntheorem X : True := by sorry\nend B", "A.X")
