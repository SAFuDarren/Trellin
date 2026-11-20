"""Verify that evaluation inputs cannot move to holdout under new names or IDs."""

import unittest

import test_lean_flow as fixtures
import test_final_local_lemmas as local_fixtures


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanEvalCorpusTests(unittest.TestCase):
    def test_compares_elaborated_scopes_and_fences_split_changes(self):
        fixture = local_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

        def statement(name, proposition):
            anchor, _, _ = fixture.anchor(name, proposition, [], sensitivity="public")
            return {"statement_id": anchor["id"], "expected": "verified"}

        development = statement("Development", "∀ n : Nat, n + 0 = n")
        renamed = statement("Renamed development", "∀ renamed : Nat, renamed + 0 = renamed")
        holdout = statement("Holdout", "∀ n : Nat, n = n")

        def seal(name, dev, held, expect=0):
            document = fixture.document(name, {"format_version": 1, "development": [dev], "holdout": [held]})
            return fixture.cli("eval", "lean-corpus-seal", document, "--actor", "auditor",
                               "--reason", "Record disjoint formal evaluation inputs.", expect=expect)

        self.assertIn("renamed binders", seal("copies", development, renamed, expect=2))
        reviewed = seal("disjoint", development, holdout)
        self.assertEqual((reviewed["development"], reviewed["holdout"]), (1, 1))
        self.assertEqual(reviewed["artifact"], seal("identical", development, holdout)["artifact"])
        self.assertIn("cannot change evaluation splits", seal("relabel", holdout, renamed, expect=2))
