"""Keep model target preparation and discussion advisory and scope limited."""

import json
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from research_multiplier.workflows import goal_suggestions, target_draft
from research_multiplier.model_adapters import ModelRun
from research_multiplier.models.model_errors import ModelOutputError


class TargetDraftTests(unittest.TestCase):
    def test_archives_a_proposal_without_confirming_the_target(self):
        body = {"title": "Reflexivity", "domain": "lean-core", "sensitivity": "public",
                "statement": "Every natural number equals itself."}
        model = ModelRun({"formal": {"lean": {"proposition": "∀ n : Nat, n = n"}}}, {}, {"messages": []})
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"RM_ARTIFACT_ROOT": directory}), \
                patch.object(target_draft.ModelConfig, "from_env", return_value=object()), \
                patch.object(target_draft, "complete_structured", return_value=model) as adapter:
            result = target_draft.propose(json.dumps(body).encode())
            self.assertEqual({key: result["target"][key] for key in body}, body)
            self.assertRegex(result["record"]["sha256"], r"^[a-f0-9]{64}$")
            adapter.reset_mock()
            with self.assertRaisesRegex(ValueError, "public"):
                target_draft.propose(json.dumps({**body, "sensitivity": "restricted"}).encode())
            adapter.assert_not_called()

    def test_material_ambiguity_has_no_frozen_target_candidate(self):
        body = {"title": "Unclear type", "domain": "lean-core", "sensitivity": "public",
                "statement": "Show the bound."}
        model = ModelRun({"questions": ["Which set is the bound over?"]}, {}, {})
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"RM_ARTIFACT_ROOT": directory}), \
                patch.object(target_draft.ModelConfig, "from_env", return_value=object()), \
                patch.object(target_draft, "complete_structured", return_value=model):
            result = target_draft.propose(json.dumps(body).encode())
            self.assertIsNone(result["target"])
            self.assertEqual(len(result["questions"]), 1)

    def test_discussion_uses_the_researchers_intuition_and_only_the_reviewed_basis(self):
        frame = {"frame_sha256": "0"*64}
        payload = {"domain": "lean-core", "objective": "Identify when equality is attainable.",
                   "basis": [{"version_id": "approved", "formal": {"lean": {"proposition": "True"}}}]}
        proposal = {"statement": "Equality is attained at zero.", "reason": "Check the extremizing case.",
                    "basis_version_ids": ["approved"]}
        model = ModelRun({"proposals": [proposal]}, {}, {})
        intuition = {"intuition": "The bound looks tight at the origin."}
        store = Mock()
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"RM_ARTIFACT_ROOT": directory}), \
                patch.object(goal_suggestions, "_model_basis", side_effect=lambda *_: (frame, dict(payload))), \
                patch.object(goal_suggestions.ModelConfig, "from_env", return_value=object()), \
                patch.object(goal_suggestions, "complete_structured", return_value=model) as adapter:
            report = goal_suggestions.discuss(store, "goal", intuition)
            self.assertEqual(report["proposals"], [proposal])
            self.assertEqual(adapter.call_args.args[3]["intuition"], intuition["intuition"])
            store.query.assert_called_once_with(
                "human", "SELECT rm.remember_goal_discussion(%s,%s,%s,%s)",
                "goal", frame["frame_sha256"], report["record"]["sha256"], 1)
            store.query.reset_mock()
            adapter.return_value = ModelRun({"proposals": [{**proposal, "basis_version_ids": ["unreviewed"]}]}, {}, {})
            with self.assertRaises(ModelOutputError):
                goal_suggestions.discuss(store, "goal", intuition)
            store.query.assert_not_called()
