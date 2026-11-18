"""Exercise role fences, guarded activation and normal Lean worker route selection."""

import os
from unittest.mock import patch
import unittest

from research_multiplier.evaluations import lean_evolution, lean_shadow
from research_multiplier.evaluations import evolution
from research_multiplier.storage.migrate import upgrade
from research_multiplier.model_adapters import ModelCapabilities
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.protocol.serialization import fingerprint
import test_final_local_lemmas as fixtures
import test_lean_shadow as shadow_fixtures
from test_lean_flow import DSN


ATTEST = "SELECT rm.attest_lean_route(%s,%s,%s,%s,%s,%s)"
ACTIVATE = "SELECT rm.activate_lean_route(%s,%s,%s,%s,%s,%s,%s,%s) AS id"


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanRouteActivationTests(unittest.TestCase):
    def test_signed_route_applies_to_worker_and_rolls_back_without_a_model(self):
        fixture = fixtures.FinalLocalLemmaTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        cli = fixture.cli
        author = "RESEARCH_MULTIPLIER_LEAN_AUTHOR_"
        fixture.fixture.env.update({author + "PROVIDER": "activation-fixture",
                                    author + "MODEL": "route-model",
                                    author + "FAMILY": "fixture",
                                    author + "ADAPTER": "test_lean_route_activation:factory"})
        goal, scope, _ = fixture.anchor("worker route selection", "True", [], sensitivity="public")
        with patch.dict(os.environ, fixture.fixture.env), Store() as store:
            config = lean_evolution._profile(None)
            pair = {"profile": None, "configuration_sha256": evolution.configuration_sha256(config),
                    "effective_sha256": fingerprint(lean_shadow.model_settings(config)),
                    "search_revision": lean_shadow.implementation_sha256()}
            baseline, candidate = ({**pair, "strategy": name} for name in ("ranked", "legacy"))
            arguments = (None, "a" * 64, "b" * 64, store.json(baseline), store.json(candidate),
                         store.json({"candidate": {"exact": 2}}), "reviewer", "Approve bounded fixture comparison")
            with self.assertRaisesRegex(DatabaseOperationError, "permission denied"):
                store.query("scheduler", ACTIVATE, *arguments)
            with self.assertRaisesRegex(DatabaseOperationError, "permission denied"):
                store.query("human", ACTIVATE.replace("activate_lean_route", "commit_lean_route_activation"), *arguments)
            with self.assertRaisesRegex(DatabaseOperationError, "requires the exact verifier attestation"):
                store.query("human", ACTIVATE, *arguments)
            with self.assertRaisesRegex(DatabaseOperationError, "permission denied"):
                store.query("human", ATTEST, *arguments[:6])
            store.query("verifier", ATTEST, *arguments[:6])
            store.query("verifier", ATTEST, *arguments[:6])
            with self.assertRaisesRegex(DatabaseOperationError, "permission denied"):
                store.query("verifier", ACTIVATE, *arguments)
            altered = (*arguments[:5], store.json({"candidate": {"exact": 3}}), *arguments[6:])
            with self.assertRaisesRegex(DatabaseOperationError, "requires the exact verifier attestation"):
                store.query("human", ACTIVATE, *altered)
            with self.assertRaisesRegex(DatabaseOperationError, "already have a different attestation"):
                store.query("verifier", ATTEST, *altered[:6])
            [event] = store.query("human", ACTIVATE, *arguments)
            with self.assertRaisesRegex(DatabaseOperationError, "active Lean route changed"):
                store.query("human", ACTIVATE, *arguments)
        policy = cli("advance", "propose", "--for-packet", goal["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "reviewer", "--reason", "Run this exact zero-model policy.")
        [completed] = cli("advance", "worker", "--policy", policy["id"])["lean_runs"]
        self.assertEqual(completed["search_strategy"], "legacy")
        self.assertEqual(completed["lean_route_event_id"], str(event["id"]))
        self.assertEqual(completed["status"], "awaiting_evidence_review")
        self.assertEqual(cli("advance", "metrics", policy["id"])["model"]["reservation_count"], 0)
        self.assertEqual(cli("research", "resume", scope["claim_id"])["status"], "open")
        status = cli("eval", "lean-active")
        self.assertTrue(status["implementation_current"])
        self.assertEqual(status["worker_passes_last_7_days"], {"awaiting_evidence_review": 1})

        cli("eval", "lean-rollback", str(event["id"]), "--actor", "reviewer", "--reason", "Restore previous route.")
        active = cli("eval", "lean-active")["active"]
        self.assertEqual(active["effective"], baseline)
        self.assertEqual(active["action"], "rollback")
        cli("eval", "lean-rollback", str(event["id"]), "--actor", "reviewer",
            "--reason", "This activation was already rolled back.", expect=2)

    def test_upgrade_preserves_history_and_requires_explicit_reset_before_reactivation(self):
        import psycopg
        from psycopg.types.json import Jsonb

        shadow = shadow_fixtures.LeanShadowTests()
        self.addCleanup(shadow.doCleanups)
        create_database = shadow.create_database
        old_revision = "a" * 64
        old_binding = {"profile": None, "configuration_sha256": "b" * 64, "effective_sha256": "c" * 64,
                       "search_revision": old_revision}
        old_baseline, old_route = ({**old_binding, "strategy": name} for name in ("ranked", "legacy"))
        def prepare_old_database():
            dsn = create_database()
            upgrade(dsn, "0058")
            with psycopg.connect(dsn) as db:
                (self.old_event,) = db.execute(ACTIVATE, (
                    None, "d" * 64, "e" * 64, Jsonb(old_baseline), Jsonb(old_route), Jsonb({}),
                    "researcher", "Previously approved implementation.",
                )).fetchone()
            return dsn

        with patch.object(shadow, "create_database", side_effect=prepare_old_database):
            shadow.setUp()
        cli, env = shadow.case.cli, shadow.fixture.env
        initial = cli("eval", "lean-active")
        self.assertFalse(initial["implementation_current"])
        self.assertEqual(initial["active"]["effective"], old_route)
        self.assertIn("Restore the installed Lean implementation", cli("eval", "lean-rollback", self.old_event,
                      "--actor", "researcher", "--reason", "The prior measured route is on old code.", expect=2))
        revision = lean_shadow.implementation_sha256()
        with patch.dict(os.environ, env), Store() as store:
            with self.assertRaisesRegex(ValueError, "belongs to another installed implementation"):
                lean_evolution.selection(store, needs_model=False)
            with self.assertRaisesRegex(DatabaseOperationError, "permission denied"):
                store.query("scheduler", "SELECT rm.reset_lean_route_after_upgrade(%s,%s,%s,%s)",
                            self.old_event, revision, "scheduler", "The worker cannot reset a route.")

        reset = cli("eval", "lean-reset-after-upgrade", self.old_event, "--actor", "researcher",
                    "--reason", "Resume the installed ranked default while evaluating this upgrade.")
        active = cli("eval", "lean-active")
        self.assertTrue(active["implementation_current"])
        self.assertEqual(active["active"]["id"], reset["event_id"])
        event, original = active["events"]
        self.assertEqual(event["previous_event_id"], original["id"])
        self.assertEqual(event["action"], "upgrade_reset")
        self.assertIsNone(event["campaign_sha256"])
        self.assertEqual(original["effective"], old_route)
        self.assertIn("current Lean route event", cli("eval", "lean-reset-after-upgrade", self.old_event,
                      "--actor", "researcher", "--reason", "This action is stale.", expect=2))
        self.assertIn("different installed Lean implementation", cli("eval", "lean-reset-after-upgrade",
                      reset["event_id"], "--actor", "researcher", "--reason", "Same implementation.", expect=2))
        with patch.dict(os.environ, env), Store() as store:
            self.assertEqual(lean_evolution.selection(store, needs_model=True), ("ranked", None, reset["event_id"]))

        goal, _, _ = shadow.case.anchor("run installed default after upgrade", "True", [], sensitivity="public")
        policy = cli("advance", "propose", "--for-packet", goal["packet_id"])
        cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"],
            "--actor", "researcher", "--reason", "Use the default route for this zero-model test.")
        worker = cli("advance", "worker", "--policy", policy["id"])
        self.assertFalse(worker["errors"], worker)
        [outcome] = worker["lean_runs"]
        self.assertEqual(outcome["search_strategy"], "ranked")
        self.assertEqual(outcome["lean_route_event_id"], reset["event_id"])
        self.assertEqual(outcome["status"], "awaiting_evidence_review")
        self.assertEqual(cli("advance", "metrics", policy["id"])["model"]["reservation_count"], 0)

        author = "RESEARCH_MULTIPLIER_LEAN_AUTHOR_"
        env.update({author + "PROVIDER": "activation-fixture", author + "MODEL": "route-model",
                    author + "FAMILY": "fixture", author + "ADAPTER": "test_lean_route_activation:factory"})
        with patch.dict(os.environ, env), Store() as store:
            config = lean_evolution._profile(None)
            baseline = {**old_baseline, "search_revision": revision,
                        "configuration_sha256": evolution.configuration_sha256(config),
                        "effective_sha256": fingerprint(lean_shadow.model_settings(config))}
            candidate = {**baseline, "strategy": "legacy"}

            def promote(original, replacement, campaign):
                attestation = (reset["event_id"], campaign, "e" * 64,
                               store.json(original), store.json(replacement), store.json({}))
                store.query("verifier", ATTEST, *attestation)
                return store.query("human", ACTIVATE, *attestation,
                                   "researcher", "Compare two eligible routes on the installed version.")[0]["id"]

            with self.assertRaisesRegex(DatabaseOperationError, "current installed default"):
                promote(old_baseline, old_route, "d" * 64)
            promoted = promote(baseline, candidate, "f" * 64)
            self.assertEqual(lean_evolution.selection(store, needs_model=False), ("legacy", None, str(promoted)))
        self.assertEqual(cli("eval", "lean-active")["active"]["effective"], candidate)
        rollback = cli("eval", "lean-rollback", promoted, "--actor", "researcher",
                       "--reason", "Restore the exact measured baseline on the new implementation.")
        self.assertEqual(rollback["effective"], baseline)


class _Adapter:
    capabilities = ModelCapabilities()

    def complete(self, request):
        raise AssertionError("The route-selection test never sends a model request.")


def factory(config):
    return _Adapter()
