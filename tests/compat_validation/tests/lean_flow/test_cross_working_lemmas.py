"""Replay verified cross-claim obligations with native Lean and PostgreSQL."""

import unittest

import test_final_local_lemmas as fixtures
import test_lean_flow as database


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class CrossWorkingLemmaTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.FinalLocalLemmaTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_rechecks_alpha_equivalent_working_lemma_under_destination_premises(self):
        cli = self.case.cli
        old = {"kind": "hypothesis", "natural": "Zero is reflexive in the original source.",
               "formal": {"lean": {"proposition": "(0 : Nat) = 0"}}}
        added = {"kind": "hypothesis", "natural": "Truth is part of the destination's confirmed scope.",
                 "formal": {"lean": {"proposition": "True"}}}
        origin, _, _ = self.case.anchor("working origin", "True", [old], sensitivity="public")
        source, = cli("lean", "derive", origin["id"], self.case.document("working source", {
            "format_version": 1, "nodes": [{
                "name": "AddZero", "proposition": "∀ (k : Nat), k + 0 = k",
                "proof": fixtures.intro(2, {"rfl": True}),
            }],
        }))["lemmas"]
        target, scope, _ = self.case.anchor(
            "working destination", "∀ n : Nat, ((n + 0 = n) ∧ True)", [added], sensitivity="public",
        )
        policy = cli("advance", "propose", "--for-packet", target["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Continue under the frozen destination scope.")
        report = cli("advance", "worker", "--policy", policy["id"], "--limit", 1)
        self.assertEqual(report["errors"], [], report)
        [finished] = report["lean_runs"]
        self.assertEqual(finished["status"], "awaiting_evidence_review")
        self.assertEqual(cli("lean", "budget", target["id"])["reserved_calls"], 0)
        recorded = cli("lean", "local-theorems", target["id"])
        replay, = [item for item in recorded if item["body"].get("working_lemma_replay")]
        self.assertEqual(replay["body"]["working_lemma_replay"], {
            "lemma_id": source["id"], "submission": source["body"]["submission"],
        })
        self.assertIn("n + 0 = n", replay["proposition"])
        before = cli("research", "evidence", scope["claim_id"])
        self.assertFalse(next(e for e in before["evidence"] if e["id"] == finished["evidence_id"])["needs_recheck"])
        cli("research", "retire-input", "source", source["body"]["candidate"]["sha256"],
            "--reason", "Withdraw the source working proof.")
        after = cli("research", "evidence", scope["claim_id"])
        self.assertTrue(next(e for e in after["evidence"] if e["id"] == finished["evidence_id"])["needs_recheck"])

    def test_replays_source_dependency_graph_then_invalidates_destination_after_ancestor_withdrawal(self):
        cli = self.case.cli
        source_basis = [{
            "kind": "hypothesis",
            "natural": "Zero is reflexive in the original graph scope.",
            "formal": {"lean": {"proposition": "(0 : Nat) = 0"}},
        }]
        destination_basis = [{
            "kind": "hypothesis",
            "natural": "Truth is the only confirmed destination premise.",
            "formal": {"lean": {"proposition": "True"}},
        }]
        origin, _, _ = self.case.anchor("exact working graph origin", "True", source_basis, sensitivity="public")
        plan = {
            "format_version": 1, "nodes": [
                {"name": "RootOfAddition",
                 "proposition": "∀ (k : Nat), k + (0 + 0) = k",
                 "proof": fixtures.intro(2, {"rfl": True})},
                {"name": "AdditionTwice",
                 "proposition": "∀ (k : Nat), (k + 0) + 0 = k",
                 "proof": fixtures.intro(2, fixtures.apply(
                     {"node_ref": "RootOfAddition"}, {"use": 0}, {"use": 1}))},
            ],
        }
        first, second = cli("lean", "derive", origin["id"], self.case.document("working chain", plan))["lemmas"]
        self.assertEqual(second["body"]["local_lemma_ids"], [first["id"]])
        destination, scope, _ = self.case.anchor(
            "exact working graph destination", "∀ n : Nat, (((n + 0) + 0 = n) ∧ True)",
            destination_basis, sensitivity="public",
        )
        policy = cli("advance", "propose", "--for-packet", destination["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Continue without model calls under the frozen basis.")
        report = cli("advance", "worker", "--policy", policy["id"], "--limit", 1)
        self.assertEqual(report["errors"], [], report)
        [finished] = report["lean_runs"]
        self.assertEqual(finished["status"], "awaiting_evidence_review", finished)
        self.assertEqual(cli("lean", "budget", destination["id"])["reserved_calls"], 0)

        imported = {
            row["body"]["working_lemma_replay"]["lemma_id"]: row
            for row in cli("lean", "local-theorems", destination["id"])
            if "working_lemma_replay" in row["body"]
        }
        self.assertEqual(set(imported), {first["id"], second["id"]})
        self.assertEqual(imported[second["id"]]["body"]["local_lemma_ids"], [imported[first["id"]]["id"]])
        self.assertEqual(imported[first["id"]]["body"]["working_lemma_replay"]["submission"],
                         first["body"]["submission"])
        self.assertEqual(imported[second["id"]]["body"]["working_lemma_replay"]["submission"],
                         second["body"]["submission"])
        review = cli("review", "packet", finished["evidence_id"])
        self.assertEqual(review["contract_comparison"]["scope_status"], "exact")
        self.assertIn(imported[first["id"]]["id"], review["key_steps"]["expanded_local_lemma_ids"])
        self.assertIn(imported[second["id"]]["id"], review["key_steps"]["expanded_local_lemma_ids"])
        self.assertFalse(review["verification"]["needs_recheck"])
        self.assertEqual(review["claim_id"], scope["claim_id"])

        cli("research", "retire-input", "source", first["body"]["candidate"]["sha256"],
            "--reason", "Withdraw the upstream source proof only.")
        stale = {row["id"]: row["status"] for row in cli("lean", "local-theorems", destination["id"])}
        self.assertEqual(stale[imported[first["id"]]["id"]], "needs_recheck")
        self.assertEqual(stale[imported[second["id"]]["id"]], "needs_recheck")
        self.assertTrue(cli("review", "packet", finished["evidence_id"])["verification"]["needs_recheck"])

    def test_replay_discharges_source_premise_with_destination_evidence(self):
        cli = self.case.cli
        premise = "∀ n : Nat, 0 ≤ n"
        obligation = "∀ n : Nat, 0 ≤ (n + 0) + 0"
        origin, _, _ = self.case.anchor("conditional origin", "True", [{
            "kind": "hypothesis", "natural": "Every natural is nonnegative in the originating scope.",
            "formal": {"lean": {"proposition": premise}},
        }], sensitivity="public")
        first, second = cli("lean", "derive", origin["id"], self.case.document("conditional chain", {
            "format_version": 1, "nodes": [
                {"name": "SourceLowerBound", "proposition": premise,
                 "proof": fixtures.intro(1, {"use": 0})},
                {"name": "ShiftedLowerBound", "proposition": obligation,
                 "proof": fixtures.intro(2, fixtures.apply(
                     {"node_ref": "SourceLowerBound"}, {"use": 0}, {"use": 1}))},
            ],
        }))["lemmas"]

        candidates = {
            "basis theorem": {"kind": "theorem", "natural": "Lean's natural-number nonnegativity theorem.",
                              "formal": {"lean": {"proposition": "∀ k : Nat, 0 ≤ k",
                                                  "declaration": "Nat.zero_le"}}},
            "local theorem": {"kind": "hypothesis", "natural": "The destination knows a shifted lower bound.",
                              "formal": {"lean": {"proposition": "∀ n : Nat, 0 ≤ n + 0"}}},
        }
        for kind, candidate in candidates.items():
            with self.subTest(destination_evidence=kind):
                destination, scope, _ = self.case.anchor(
                    f"conditional replay using {kind}", "∀ n : Nat, (0 ≤ (n + 0) + 0) ∧ True",
                    [candidate], sensitivity="public",
                )
                if kind == "basis theorem":
                    [approved] = [row for row in cli("research", "basis", destination["packet_id"])
                                  if row["body"]["kind"] == "theorem"]
                    discharge_id = approved["id"]
                    retire = ("retire-item", discharge_id)
                else:
                    [local] = cli("lean", "derive", destination["id"], self.case.document("local lower bound", {
                        "format_version": 1, "nodes": [{
                            "name": "DestinationLowerBound", "proposition": premise,
                            "proof": fixtures.intro(2, fixtures.apply({"use": 0}, {"use": 1})),
                        }],
                    }))["lemmas"]
                    discharge_id = local["id"]
                    retire = ("retire-input", "source", local["body"]["candidate"]["sha256"])

                policy = cli("advance", "propose", "--for-packet", destination["packet_id"])
                cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
                    "--actor", "researcher", "--reason", "Check prerequisites in the destination scope.")
                human = self.case.fixture.env.pop("RM_HUMAN_DSN")
                try:
                    report = cli("advance", "worker", "--policy", policy["id"], "--limit", 1)
                finally:
                    self.case.fixture.env["RM_HUMAN_DSN"] = human
                self.assertEqual(report["errors"], [], report)
                [finished] = report["lean_runs"]
                self.assertEqual(finished["status"], "awaiting_evidence_review", finished)
                self.assertEqual(cli("lean", "budget", destination["id"])["reserved_calls"], 0)

                replayed = {
                    row["body"]["working_lemma_replay"]["lemma_id"]: row
                    for row in cli("lean", "local-theorems", destination["id"])
                    if "working_lemma_replay" in row["body"]
                }
                self.assertLessEqual({first["id"], second["id"]}, set(replayed))
                destination_root = replayed[second["id"]]
                self.assertEqual(destination_root["status"], "verified")
                if kind == "basis theorem":
                    self.assertEqual(destination_root["body"]["basis_version_ids"], [discharge_id])
                    self.assertEqual(destination_root["body"]["local_lemma_ids"], [replayed[first["id"]]["id"]])
                else:
                    self.assertEqual(destination_root["body"]["basis_version_ids"], [])
                    self.assertCountEqual(destination_root["body"]["local_lemma_ids"],
                                          [replayed[first["id"]]["id"], discharge_id])
                review = cli("review", "packet", finished["evidence_id"])
                self.assertEqual(review["claim_id"], scope["claim_id"])
                self.assertEqual(review["contract_comparison"]["scope_status"], "exact")
                self.assertFalse(review["verification"]["needs_recheck"])

                cli("research", *retire, "--reason", "Withdraw the destination proof of the source premise.")
                current = {row["id"]: row["status"] for row in cli("lean", "local-theorems", destination["id"])}
                self.assertEqual(current[destination_root["id"]], "needs_recheck")
                self.assertTrue(cli("review", "packet", finished["evidence_id"])["verification"]["needs_recheck"])
                original = {row["id"]: row["status"] for row in cli("lean", "local-theorems", origin["id"])}
                self.assertEqual(original[second["id"]], "verified")

    def test_does_not_import_any_part_of_a_chain_when_its_source_premise_is_missing(self):
        cli = self.case.cli
        source_basis = [{
            "kind": "hypothesis", "natural": "Every natural in the original conditional example equals zero.",
            "formal": {"lean": {"proposition": "∀ (k : Nat), k = 0"}},
        }]
        origin, _, _ = self.case.anchor("conditional chain origin", "True", source_basis, sensitivity="public")
        cli("lean", "derive", origin["id"], self.case.document("conditional source chain", {
            "format_version": 1, "nodes": [
                {"name": "AllZero", "proposition": "∀ (k : Nat), k = 0",
                 "proof": fixtures.intro(1, {"use": 0})},
                {"name": "AllAddZero", "proposition": "∀ (k : Nat), k + 0 = 0",
                 "proof": fixtures.intro(2, fixtures.apply(
                     {"node_ref": "AllZero"}, {"use": 0}, {"use": 1}))},
            ],
        }))
        destination, scope, _ = self.case.anchor(
            "no stronger premises in destination", "∀ n : Nat, ((n + 0 = 0) ∧ True)", [], sensitivity="public",
        )
        policy = cli("advance", "propose", "--for-packet", destination["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Run only under the destination scope.")
        report = cli("advance", "worker", "--policy", policy["id"], "--limit", 1)
        self.assertEqual(report["errors"], [], report)
        self.assertEqual(cli("lean", "budget", destination["id"])["reserved_calls"], 0)
        self.assertFalse(any("working_lemma_replay" in row["body"]
                             for row in cli("lean", "local-theorems", destination["id"])))
        receipts = cli("research", "evidence", scope["claim_id"])["evidence"]
        self.assertFalse(any(row["kind"] == "lean" and row["verdict"] == "verified" for row in receipts))
