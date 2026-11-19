"""Check logical eliminations at the exact-scope verification entry point."""

import unittest

import test_lean_flow as fixtures
import test_final_local_lemmas as final_fixtures
from test_final_local_lemmas import intro


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ScopedLogicTests(unittest.TestCase):
    def test_kernel_checks_branch_hypotheses_and_reports_logical_dependencies(self):
        fixture = final_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        proposition = "∀ P Q : Prop, ((P ∧ True) ∨ (Q ∧ True)) → Q ∨ P"
        anchor, scope, _ = fixture.anchor("disjunction elimination", proposition, [])
        left = intro(1, {"right": {"and_left": {"use": 3}}})
        right = intro(1, {"left": {"and_left": {"use": 3}}})
        invalid = fixture.document("branch_scope_error", {"format_version": 1, "proof": intro(3, {
            "or_elim": [{"use": 2}, left, left],
        })})
        problem = fixture.cli("lean", "check-theory", anchor["id"], invalid, expect=2)
        self.assertIn("type mismatch", problem)
        self.assertEqual(fixture.cli("research", "evidence", scope["claim_id"])["evidence"], [])
        valid = fixture.document("logical_elimination", {"format_version": 1, "proof": intro(3, {
            "or_elim": [{"use": 2}, left, right],
        })})
        result = fixture.cli("lean", "check-theory", anchor["id"], valid)
        self.assertTrue(result["accepted"] and result["kernel_rechecked"] and result["scope_matches"])
        self.assertEqual(result["basis_version_ids"], [])
        self.assertIn("Or.elim", result["dependencies"]["direct_constants"])
