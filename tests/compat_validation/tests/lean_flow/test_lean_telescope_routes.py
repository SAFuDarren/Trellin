"""Check real telescope reductions, proof obligations and multiple route records."""

import unittest

import test_final_local_lemmas as fixtures
import test_lean_flow as database


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class TelescopeRouteTests(unittest.TestCase):
    def test_open_telescope_gets_a_checked_independent_route_and_exact_final_proof(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        cli = case.cli
        prefix = "∀ (x y : Nat), ∀ p : Prop, (p) → "
        target, scope, _ = case.anchor(
            "checked nested telescope", prefix + "((x = x ∧ p) ∧ y = y)",
            [{"kind": "hypothesis", "natural": "Truth is the frozen original premise.",
              "formal": {"lean": {"proposition": "True"}}}], sensitivity="public",
            statement_text="The original grouped goal keeps its bound variables and given proof.",
        )
        # Register a second valid implication needing two grouped obligations.
        inputs = [{"use": i} for i in (3, 4, 5, 6)]
        second = case.document("grouped-route", {
            "format_version": 1, "obligations": [prefix + "(x = x ∧ p)", prefix + "(y = y)"],
            "proof": {"intro": {"count": 7, "body": {
                "and": [{"apply": [{"use": 1}, *inputs]}, {"apply": [{"use": 2}, *inputs]}],
            }}},
        })
        routes = cli("lean", "reductions", target["id"], "--register", second)["reductions"]
        self.assertEqual([route["status"] for route in routes], ["awaiting_obligations"])
        grouped = routes[0]["implication_id"]

        initial = cli("research", "resume", scope["claim_id"], "--details")
        policy = cli("advance", "propose", "--for-packet", target["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Test program-only work within the confirmed telescope.")
        run = cli("advance", "worker", "--policy", policy["id"], "--limit", 1)
        self.assertEqual(run["errors"], [])
        [completed] = run["lean_runs"]
        self.assertEqual(completed["status"], "awaiting_evidence_review", completed)
        self.assertNotEqual(completed["reduction_id"], grouped)
        self.assertEqual(completed["route_comparison"][0]["verified_obligations"], 3)
        self.assertEqual(completed["route_comparison"][0]["status"], "goal_checked")
        self.assertEqual(completed["route_comparison"][1]["verified_obligations"], 1)
        self.assertEqual(cli("lean", "budget", target["id"])["reserved_calls"], 0)

        final = cli("research", "resume", scope["claim_id"], "--details")
        self.assertEqual(final["details"]["contract_sha256"], initial["details"]["contract_sha256"])
        self.assertEqual(final["scope"], initial["scope"])
        self.assertEqual([row["kind"] for row in final["decisions"]["items"]], ["review_evidence"])

    def test_all_five_deterministic_obligations_finish_without_model_reservation(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        cli = case.cli
        goal = "∀ n : Nat, " + " ∧ ".join(f"(n + {i} = n + {i})" for i in range(5))
        target, _, _ = case.anchor("five deterministic branches", goal, [], sensitivity="public")
        case.fixture.env["RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER"] = "not-a-model"
        policy = cli("advance", "propose", "--for-packet", target["packet_id"], "--lean-model-rounds", 1)
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Allow a paid fallback only if program work is exhausted.")
        report = cli("advance", "worker", "--policy", policy["id"], "--limit", 1)
        self.assertEqual(report["errors"], [], report)
        [completed] = report["lean_runs"]
        self.assertEqual(completed["status"], "awaiting_evidence_review")
        self.assertEqual(completed["route_comparison"][0]["verified_obligations"], 5)
        self.assertEqual(cli("lean", "budget", target["id"])["reserved_calls"], 0)
