"""Verify model experiments cannot alter proof authority and can select only reviewed production routes."""

import json
import os
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

import test_evaluation as cases
import test_production as fixtures

from research_multiplier.evaluations import evolution
from research_multiplier.workflows import formalization
from research_multiplier import production
from research_multiplier.domain_packages import production_domains
from research_multiplier.model_adapters import AdapterReply, ModelCapabilities, ModelConfig
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ShadowEvolutionTests(unittest.TestCase):
    def test_paired_shadow_human_activation_and_explicit_rollback(self):
        fixtures.ProductionTests.setUpClass()
        self.addCleanup(fixtures.ProductionTests.doClassCleanups)
        fixture = fixtures.ProductionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        development, holdout, _, _ = cases.cases_and_responses()
        requests = []

        class FixedAdapter:
            capabilities = ModelCapabilities()

            def __init__(self, config):
                self.model = config.model

            def complete(self, request):
                context = json.loads(request["messages"][-1]["content"])
                frozen = context["frozen_claim"]
                requests.append((self.model, frozen))
                if self.model == "shadow-baseline":
                    reply = {"failure_reason": "The route was not found."}
                elif self.model == "shadow-auditor":
                    reply = fixtures.AUDIT
                else:
                    domain = production_domains.get(frozen["domain"])
                    witness = domain.candidate(frozen["small_case_spec"])["certificate"]
                    reply = {"proof_markdown": "A flow and equal cut give the exact value.", "certificate": witness}
                    if "formalization" in context["proof_requirements"]:
                        reply["basis_items"] = ["H1", "H2", "H3"]
                return AdapterReply(json.dumps(reply), self.model, model_revision="shadow-fixture")

        adapter = ModuleType("rm_shadow_adapter")
        adapter.make = FixedAdapter
        profiles = {}
        for setting, model, family in (
            ("PROVER", "shadow-baseline", "base"), ("AUDITOR", "shadow-auditor", "audit"),
            ("PROFILE_CHALLENGER", "shadow-next", "next"),
        ):
            prefix = f"RESEARCH_MULTIPLIER_{setting}_"
            profiles.update({prefix + name: value for name, value in {
                "PROVIDER": "shadow-adapter", "MODEL": model, "FAMILY": family, "ADAPTER": "rm_shadow_adapter:make",
            }.items()})

        with patch.dict(os.environ, {**fixture.env, **profiles}), patch.dict(sys.modules, {adapter.__name__: adapter}):
            with Store() as store:
                before, = store.query("verifier", "SELECT count(*) AS total FROM rm.evidence")
            result = evolution.shadow(development, holdout, "CHALLENGER", confirm_public=True)
            self.assertTrue(result["recommended"])
            paired = {model: [frozen for used, frozen in requests if used == model]
                      for model in ("shadow-baseline", "shadow-next", "shadow-auditor")}
            self.assertEqual(paired["shadow-baseline"], paired["shadow-next"])
            self.assertEqual(paired["shadow-next"], paired["shadow-auditor"])
            self.assertGreater(len(paired["shadow-next"]), 0)
            self.assertEqual(result["metrics"]["baseline"]["model_calls"], len(paired["shadow-baseline"]))
            self.assertEqual(result["metrics"]["candidate"]["model_calls"], 2 * len(paired["shadow-next"]))
            experiment = result["experiment_id"]
            digest = result["experiment"]["sha256"]
            packet = formalization.draft(fixtures.FLOW_INSTANCE, "Frozen production trial claim")
            formalization.confirm(packet["id"], packet["version"], packet["packet_sha256"], [],
                                  "operator", "Confirmed the exact network.")
            trial = evolution.shadow_live([packet["claim_id"]], "CHALLENGER", confirm_public=True)
            saved_trial = evolution.live_status(trial["trial_id"])
            self.assertEqual(saved_trial["paired_outcomes"][str(packet["claim_id"])],
                             {"reviewed_outcome": None, "baseline": "abstained", "candidate": "verified"})
            self.assertEqual(trial["counts"]["reviewed_cases"], 0)
            live_id, live_digest = trial["trial_id"], trial["trial"]["sha256"]
            with Store() as store:
                self.assertEqual(store.query("verifier", "SELECT count(*) AS total FROM rm.evidence"), [before])
                with self.assertRaisesRegex(ValueError, "permission denied"):
                    store.query("verifier", "SELECT rm.activate_shadow_route(%s,%s,%s,%s,'operator','unauthorized')",
                                experiment, digest, live_id, live_digest)
            with self.assertRaisesRegex(ValueError, "approved experiment"):
                evolution.activate(experiment, digest, live_id, live_digest, "operator", "needs signed assessment")
            with Store() as store:
                store.query("human", "SELECT rm.approve_eval_report(%s,%s,'operator','paired results checked')",
                            result["evaluation_report_id"], result["evaluation_report"]["sha256"])
            with self.assertRaisesRegex(ValueError, "exact shadow experiment hash"):
                evolution.activate(experiment, "0" * 64, live_id, live_digest, "operator", "wrong digest")
            evolution.record_review(experiment, "case-1", "baseline", 3, "operator")
            with self.assertRaisesRegex(ValueError, "paired review time"):
                evolution.activate(experiment, digest, live_id, live_digest, "operator", "only one measured side")
            evolution.record_review(experiment, "case-1", "candidate", 2, "operator")
            with patch.dict(os.environ, {"RESEARCH_MULTIPLIER_PROFILE_CHALLENGER_MODEL": "edited-after-trial"}):
                with self.assertRaisesRegex(ValueError, "settings changed"):
                    evolution.activate(experiment, digest, live_id, live_digest, "operator", "changed config")
            with self.assertRaisesRegex(ValueError, "exact live shadow trial hash"):
                evolution.activate(experiment, digest, live_id, "0" * 64, "operator", "wrong live trial")
            event = evolution.activate(
                experiment, digest, live_id, live_digest, "operator", "verified evidence and less human time",
            )
            activated = evolution.status(experiment)["experiment"]
            self.assertEqual(activated["candidate_minutes"], 2)
            self.assertEqual(activated["active_route"]["id"], event["route_event_id"])
            self.assertEqual(ModelConfig.from_env("PROVER").model, "shadow-next")

            _, run = production.run_public(fixtures.FLOW_INSTANCE, "Production after shadow adoption",
                                           fixture.root, "operator")
            self.assertEqual(run["attempts"]["prove"]["return"]["model"]["id"], "shadow-next")
            self.assertEqual(run["human_review"], "pending")
            with self.assertRaisesRegex(ValueError, "current activation"):
                evolution.rollback(experiment, "operator", "stale event")
            with patch.dict(os.environ, {"RESEARCH_MULTIPLIER_PROVER_MODEL": "edited-after-trial"}):
                with self.assertRaisesRegex(ValueError, "previous profile"):
                    evolution.rollback(event["route_event_id"], "operator", "prior settings drifted")
            evolution.rollback(event["route_event_id"], "operator", "restore the original profile")
            self.assertEqual(ModelConfig.from_env("PROVER").model, "shadow-baseline")
            self.assertEqual([entry["action"] for entry in evolution.status(experiment)["experiment"]["events"]],
                             ["activate", "rollback"])
