"""Use the scoped structured operators on an ordinary Lean verification entry point."""

import unittest

import test_lean_flow as fixtures
import test_final_local_lemmas as local_fixtures


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ScopedProofLiteralsTests(unittest.TestCase):
    def test_check_theory_validates_explicit_literal_without_local_references(self):
        fixture = local_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        anchor, _, _ = fixture.anchor(
            "instantiate a quantified premise",
            "(∀ n : Nat, n = n) → (2 : Nat) = 2",
            [],
        )
        invalid = fixture.document("unsupported-literal", {"format_version": 1, "proof": {
            "intro": {"count": 1, "body": {"apply": [{"use": 0}, {"nat_literal": 17}]}},
        }})
        rejected = fixture.cli("lean", "check-theory", anchor["id"], invalid, expect=2)
        self.assertIn("Unsupported or invalid proof operator: nat_literal", rejected)
        valid = fixture.document("checked-literal", {"format_version": 1, "proof": {
            "intro": {"count": 1, "body": {"apply": [{"use": 0}, {"nat_literal": 2}]}},
        }})
        result = fixture.cli("lean", "check-theory", anchor["id"], valid)
        self.assertTrue(result["accepted"] and result["scope_matches"] and result["kernel_rechecked"])
        self.assertEqual(result["basis_version_ids"], [])
