"""Reject conclusions negated only under an inconsistent hypothesis."""

from hashlib import sha256
import unittest

from research_multiplier import general_lean

import test_final_local_lemmas as final_fixtures
from test_lean_flow import DSN


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ExactLeanNegationTests(unittest.TestCase):
    def test_inconsistent_premise_cannot_refute_the_frozen_implication(self):
        fixture = final_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        anchor, scope, _ = fixture.anchor("False premise with true conclusion", "True", [{
            "kind": "hypothesis",
            "natural": "The frozen premise is False.",
            "formal": {"lean": {"proposition": "False"}},
        }])
        base = ("lean", "check-theory", anchor["id"])
        output = ("--out", fixture.fixture.output)
        ordinary = fixture.document("ordinary-proof", {
            "format_version": 1, "proof": final_fixtures.intro(1, {"trivial": True}),
        })
        positive = cli(*base, ordinary, *output)
        self.assertEqual(positive["expected_type"], "False → True")

        # Both terms prove False → Not True in the old consequent-only scope.
        local_proof = final_fixtures.intro(2, {"use": 0})
        wrong = fixture.document("consequent-only", {"format_version": 1, "proof": local_proof})
        self.assertIn("Lean", cli(*base, wrong, "--refute", *output, expect=2))

        plan = fixture.document("local-consequent-only", {"format_version": 1, "nodes": [{
            "name": "FromFalse", "proposition": "¬ True", "proof": local_proof,
        }]})
        lemma, = cli("lean", "derive", anchor["id"], plan)["lemmas"]
        self.assertEqual(lemma["status"], "verified")
        borrowed = fixture.document("bare-scoped-refutation", {
            "format_version": 1, "proof": {"ref": lemma["id"]},
        })
        self.assertIn("Lean", cli(*base, borrowed, "--refute", *output, expect=2))
        wrong_argument = fixture.document("wrong-scope-argument", {
            "format_version": 1,
            "proof": final_fixtures.intro(1, final_fixtures.apply({"ref": lemma["id"]}, {"use": 0})),
        })
        self.assertIn("Lean", cli(*base, wrong_argument, "--refute", *output, expect=2))

        state = cli("research", "evidence", scope["claim_id"])
        self.assertEqual(state["claim"][0]["status"], "open")
        self.assertEqual([(row["id"], row["kind"]) for row in state["evidence"]], [(positive["evidence_id"], "lean")])
        self.assertEqual([row["item_id"] for row in cli("review", "inbox", "--claim", scope["claim_id"])],
                         [positive["evidence_id"]])

    def test_legacy_refutation_is_stale_without_retiring_the_same_conditional_type(self):
        import psycopg

        fixture = final_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        anchor, _, _ = fixture.anchor("Legacy refutation source", "False", [{
            "kind": "hypothesis",
            "natural": "Truth is the premise in the historical receipt fixture.",
            "formal": {"lean": {"proposition": "True"}},
        }])
        legacy_proposition = "_root_.Not (\nFalse\n)"
        legacy_source = general_lean._type_source(legacy_proposition, ["True"])
        legacy_sha = sha256(legacy_source.encode("utf-8")).hexdigest()
        plan = fixture.document("conditional-lemma", {"format_version": 1, "nodes": [{
            "name": "LegacyType", "proposition": legacy_proposition,
            "proof": final_fixtures.intro(2, {"use": 1}),
        }]})
        lemma, = cli("lean", "derive", anchor["id"], plan)["lemmas"]
        self.assertEqual(lemma["body"]["statement_sha256"], legacy_sha)
        current = fixture.document("whole-negation", {"format_version": 1, "proof": final_fixtures.intro(
            1, final_fixtures.apply({"use": 0}, {"trivial": True}),
        )})
        receipt = cli("lean", "check-theory", anchor["id"], current, "--refute", "--out", fixture.fixture.output)
        evidence_id = receipt["evidence_id"]
        cli("review-evidence", evidence_id, "approve", "--actor", "researcher", "--reason", "Review the refutation.")
        cli("promote-evidence", evidence_id, "--reason", "Adopt the reviewed refutation.")
        self.assertEqual(cli("review", "packet", evidence_id)["claim_status"], "refuted")

        # Simulate the old source hash in an already adopted historical record.
        # Normal admission must reject that binding, as tested by the old-term cases.
        with psycopg.connect(DSN) as admin:
            admin.execute("SET LOCAL session_replication_role = replica")
            admin.execute("""
                UPDATE rm.lean_checks c SET body = jsonb_set(c.body, '{refutation_source_sha256}', to_jsonb(%s::text))
                FROM rm.evidence e WHERE e.id = %s AND c.id = e.lean_check_id
            """, (legacy_sha, evidence_id))
        history = cli("review", "packet", evidence_id)
        self.assertEqual(history["claim_status"], "needs_recheck")
        self.assertTrue(history["verification"]["needs_recheck"])
        self.assertEqual(cli("lean", "local-theorems", anchor["id"])[0]["status"], "verified")
