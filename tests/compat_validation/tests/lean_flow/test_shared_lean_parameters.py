"""Bind and revoke individual premises within a single reviewed Lean telescope."""

import unittest
from uuid import uuid4

import test_final_local_lemmas as fixtures
import test_lean_flow as database


PARAMETERS = [{"name": name, "type": "Nat → Nat"} for name in ("f", "g")]
F_INJECTIVE = "∀ a b : Nat, f a = f b → a = b"
G_INJECTIVE = "∀ a b : Nat, g a = g b → a = b"
CONCLUSION = "∀ a b : Nat, g (f a) = g (f b) → a = b"


def formal(proposition, parameters=PARAMETERS):
    return {"lean": {"proposition": proposition, "parameters": parameters}}


def use(index):
    return {"use": index}


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class SharedLeanParameterTests(unittest.TestCase):
    def test_individual_premises_replay_under_one_scope_and_retire_independently(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        cli, document = case.cli, case.document
        target = document("shared-injective-composition", {
            "title": f"Shared maps {uuid4()}", "domain": "lean-core", "sensitivity": "public",
            "statement": "A composition of the reviewed maps is injective.",
            "formal": formal(CONCLUSION),
        })
        preview = cli("intake", "preview-target", target)
        fixed = cli("intake", "freeze-target", target, "--hash", preview["target_sha256"],
                    "--actor", "researcher", "--reason", "Review the target and parameter types.")
        statements = [
            {"kind": "hypothesis", "natural": "The first map is injective.", "formal": formal(F_INJECTIVE)},
            {"kind": "hypothesis", "natural": "The second map is injective.", "formal": formal(G_INJECTIVE)},
        ]

        def intake(items):
            notes = document("shared-injective-basis", {"items": items})
            draft = cli("intake", "draft", notes, "--target", fixed["id"])
            return draft, document("shared-injective-decisions", {
                row["id"]: {"action": "accept", "reason": reason}
                for row, reason in zip(draft["comparison"], ("Check first map.", "Check second map."))
                if row["status"] not in {"reuse", "omit_frozen_target"}
            })

        wrong = formal(G_INJECTIVE, list(reversed(PARAMETERS)))
        rejected, decisions = intake([statements[0], {**statements[1], "formal": wrong}])
        result = cli("intake", "confirm", rejected["id"], "--hash", rejected["intake_sha256"], "--decisions", decisions,
                     "--actor", "researcher", "--reason", "Mismatched shared parameter order.", expect=2)
        self.assertIn("same ordered Lean parameters", result)
        self.assertIsNone(cli("intake", "target", fixed["id"])["claim_id"])

        draft, decisions = intake(statements)
        cli("intake", "confirm", draft["id"], "--hash", draft["intake_sha256"], "--decisions", decisions,
            "--actor", "researcher", "--reason", "Confirm separate premises under the same parameters.")
        scope = cli("intake", "target", fixed["id"])
        packet, claim = scope["packet_id"], scope["claim_id"]
        rows = [row for row in cli("research", "basis", packet) if row["body"]["kind"] == "hypothesis"]
        self.assertEqual(len(rows), 2)
        by_label = {row["label"]: row for row in rows}
        self.assertEqual(by_label["H1"]["body"]["formal"], statements[0]["formal"])
        self.assertEqual(by_label["H2"]["body"]["formal"], statements[1]["formal"])
        self.assertNotEqual(by_label["H1"]["id"], by_label["H2"]["id"])

        anchor = cli("lean", "draft-theory", packet)
        self.assertEqual(anchor["body"]["scope"]["lean_parameters"], PARAMETERS)
        self.assertEqual(anchor["body"]["scope"]["lean_hypotheses"], [F_INJECTIVE, G_INJECTIVE])
        self.assertIn("∀ (f g : Nat → Nat)", anchor["body"]["elaborated_theorem_type"])
        cli("lean", "confirm", anchor["id"], "--version", anchor["version"], "--hash", anchor["statement_sha256"],
            "--actor", "researcher", "--reason", "Review the exact theorem under both named maps.")

        intro, apply = fixtures.intro, fixtures.apply
        f, g, hf, hg, a, b, equal = (use(index) for index in range(7))
        lift = "∀ a b : Nat, g (f a) = g (f b) → f a = f b"
        h1 = apply({"node_ref": "Lift"}, f, g, hf, hg, a, b, equal)
        plan = document("shared-injective-plan", {
            "format_version": 1,
            "nodes": [{"name": "Lift", "proposition": lift,
                       "proof": intro(7, apply(hg, apply(f, a), apply(f, b), equal))}],
            "goal_proof": intro(7, apply(hf, a, b, h1)),
        })
        checked = cli("lean", "derive", anchor["id"], plan, "--out", case.fixture.output)
        lemma, = checked["lemmas"]
        receipt = checked["goal"]
        self.assertEqual(lemma["status"], "verified")
        self.assertEqual(receipt["expected_type"], anchor["body"]["elaborated_theorem_type"])
        self.assertEqual([row["item"] for row in receipt["dependencies"]["premise_usage"]], ["H1", "H2"])
        self.assertTrue(all(row["occurs_in_proof_term"] for row in receipt["dependencies"]["premise_usage"]))
        self.assertEqual([row["item_id"] for row in cli("review", "inbox", "--claim", claim)],
                         [receipt["evidence_id"]])
        self.assertEqual(cli("lean", "budget", anchor["id"])["reserved_calls"], 0)
        cli("research", "retire-item", by_label["H2"]["id"], "--reason", "Withdraw only the second premise.")
        self.assertTrue(cli("review", "packet", receipt["evidence_id"])["verification"]["needs_recheck"])
        self.assertEqual(cli("lean", "local-theorems", anchor["id"])[0]["status"], "needs_recheck")
        self.assertIn("stale", cli("lean", "derive", anchor["id"], plan, "--out", case.fixture.output, expect=2))
