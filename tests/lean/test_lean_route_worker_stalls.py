# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
from types import ModuleType

import pytest

from research_multiplier import general_lean
from research_multiplier.model_adapters import AdapterReply, ModelCapabilities
from research_multiplier.models.model_errors import ModelFailure
from research_multiplier.policy import lean_budget
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workflows import intake, lean_workflow, research_progress
from tests._native_db import disposable_project_logins


pytestmark = pytest.mark.lean


class RouteAdapter:
    capabilities = ModelCapabilities(output_token_limit_enforced=True)

    def complete(self, request):
        candidate = json.loads(request["messages"][1]["content"])
        proposition = candidate["search_focus"]["proposition"]
        if "* 1" in proposition:
            result = {"action": "blocked", "reason": "No proof for this obligation."}
        elif candidate["round"] <= 2:
            raise ModelFailure("timeout", "Fixture provider timed out.", retryable=True)
        else:
            result = {"action": "lemma", "proposition": proposition,
                      "proof": {"intro": {"count": 1, "body": {"rfl": True}}}}
        return AdapterReply(text=json.dumps(result), model_id=request["model_id"])


def _approved_parallel_search():
    proposition = "∀ n : Nat, ((n + 0 + 0 = n) ∨ (n * 1 = n))"
    target = json.dumps({
        "title": "Two arithmetic proof routes", "domain": "lean-core", "sensitivity": "public",
        "statement": proposition, "formal": {"lean": {"proposition": proposition}},
    }).encode()
    actor, reason = "Regression test", "Reviewed the exact public fixture."
    with Store() as store:
        preview = intake.preview_target(store, target)
        frozen = intake.freeze_target(store, target, preview["target_sha256"], actor, reason)
        draft = intake.draft(store, b'{"items":[]}', target_id=frozen["id"])
        intake.confirm(store, draft["id"], draft["intake_sha256"], {}, actor, reason)
        packet = intake.target(store, frozen["id"])
    statement = general_lean.propose(packet["packet_id"])
    lean_workflow.confirm(statement["id"], statement["version"], statement["statement_sha256"], actor, reason)
    policy = research_progress.propose({
        "actions": ["lean"], "metric": "exact-closure", "packet_id": packet["packet_id"],
        "lean_statement_id": str(statement["id"]), "lean_model_rounds": 4, "lean_route_parallelism": 2,
        "lean_route_stall_limit": 1,
    })
    research_progress.approve(policy["id"], policy["policy_sha256"], actor, reason)
    return statement["id"], policy["id"]


def test_provider_failure_does_not_pause_signed_lean_route(tmp_path, monkeypatch):
    module_name = "route_stall_fixture_adapter"
    module = ModuleType(module_name)
    module.factory = lambda _: RouteAdapter()
    monkeypatch.setitem(sys.modules, module_name, module)
    with disposable_project_logins() as logins:
        for name in tuple(os.environ):
            if name.startswith(("RM_", "RESEARCH_MULTIPLIER_LEAN_AUTHOR_")):
                monkeypatch.delenv(name)
        for name, value in logins.items():
            monkeypatch.setenv(name, value)
        prefix = "RESEARCH_MULTIPLIER_LEAN_AUTHOR_"
        for name, value in {
            "PROVIDER": "route-fixture", "MODEL": "route-fixture", "FAMILY": "route-fixture",
            "ADAPTER": f"{module_name}:factory",
        }.items():
            monkeypatch.setenv(prefix + name, value)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        workspace = paths.initialize_workspace(tmp_path / "project")
        with paths.use_workspace(workspace, strict=True):
            bind_workspace(workspace)
            statement, policy = _approved_parallel_search()
            with monkeypatch.context() as worker:
                worker.delenv("RM_HUMAN_DSN")
                first = research_progress.advance_cycle("combined", 1, [policy], 2)
                assert not first["errors"]
                run, = first["lean_runs"]
                failure, = [step for step in run["rounds"] if step["status"] == "proposal_failed"]
                stalled, = [step for step in run["rounds"] if step["status"] == "blocked"]
                assert "route_checkpoint" not in failure
                assert {lane["implication_id"] for lane in run["route_lanes"] if lane["paused"]} == {
                    stalled["focus"]["implication_id"],
                }
                second = research_progress.advance_cycle("combined", 1, [policy], 2)
                assert not second["errors"]
            recovered, = second["lean_runs"]
            assert recovered["status"] == "awaiting_evidence_review"
            assert recovered["evidence_id"]
            budget = lean_budget.inspect(statement)
            assert budget["reserved_calls"] == 3
            assert budget["outcomes"]["failed"] == 1
