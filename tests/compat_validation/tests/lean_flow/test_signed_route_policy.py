"""Verify the signed proof-route settings and the database reservation fence."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as final_fixtures
import test_lean_flow as fixtures
from research_multiplier.policy import lean_budget
from research_multiplier.workflows import runtime
from research_multiplier.storage.postgres import DatabaseOperationError, Store


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class SignedRoutePolicyTests(unittest.TestCase):
    def test_signed_parallel_policy_caps_reservations_below_the_frozen_goal_limit(self):
        fixture = final_fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        anchor, _, _ = fixture.anchor("signed independent routes", "∀ n : Nat, n + 0 = n", [], sensitivity="public")
        proposal = ("advance", "propose", "--for-packet", anchor["packet_id"], "--lean-route-parallelism", 2,
                    "--lean-route-stall-limit", 1)
        rejected = fixture.cli(*proposal, "--lean-model-rounds", 1, expect=2)
        self.assertIn("signed model allowance", rejected)
        policy = fixture.cli(*proposal, "--lean-model-rounds", 2)
        self.assertEqual(policy["policy"]["lean"]["route_parallelism"], 2)
        self.assertEqual(policy["policy"]["lean"]["route_stall_limit"], 1)
        key = {"claim_id": str(policy["claim_id"]), "contract_sha256": policy["contract_sha256"]}
        payload = {
            "lean_progress_policy_id": policy["id"], "lean_author": True, "lean_search": True,
            "formalization_id": str(anchor["packet_id"]),
            "formalization_sha256": anchor["body"]["scope"]["principal_packet_sha256"],
            "author_statement_id": str(anchor["id"]), "author_statement_sha256": anchor["statement_sha256"],
        }
        with patch.dict(os.environ, fixture.fixture.env), Store() as store:
            def enqueue(index):
                return runtime.schedule(store, key, "prove", payload, 60, f"signed-route-probe-{index}", model_calls=1)["id"]

            pending = enqueue(0)
            with self.assertRaisesRegex(DatabaseOperationError, "active signed Lean route policy"):
                lean_budget.claim(store, "signed-route-probe", 60, pending, retry=False)
            fixture.cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
                        "--actor", "researcher", "--reason", "Authorize a two-call parallel proof pass.")
            for job in (pending, enqueue(1)):
                self.assertEqual(len(lean_budget.claim(store, "signed-route-probe", 60, job, retry=False)), 1)
            with self.assertRaises(lean_budget.Exhausted):
                lean_budget.claim(store, "signed-route-probe", 60, enqueue(2), retry=False)

        budget = fixture.cli("lean", "budget", anchor["id"])
        self.assertEqual((budget["call_limit"], budget["reserved_calls"]), (8, 2))
