from copy import deepcopy
import unittest
from unittest.mock import patch

from research_multiplier.workflows import exploration
from research_multiplier.v0a_models import ModelRun


def document():
    return {
        "format_version": 1,
        "domain": "fixed-directed-max-flow",
        "sensitivity": "public",
        "title": "Compare feasible flow and cut",
        "network": {"vertices": ["s", "a", "t"], "source": "s", "sink": "t",
                    "edges": [["s", "a", 4], ["a", "t", 11], ["s", "t", 5]]},
        "items": [
            {"id": "D1", "kind": "definition", "natural": "A cut has source on one side and sink on the other.",
             "formal": {"source_side_contains": "s", "source_side_excludes": "t"},
             "version": "1", "source": "notes §2"},
            {"id": "T1", "kind": "theorem", "natural": "Any flow is bounded by the capacity of a cut.",
             "formal": {"forall": "feasible_flow_and_cut", "inequality": "flow_value_le_cut_capacity"},
             "version": "2", "source": "public course notes, Theorem 3"},
        ],
    }


class ExplorationTests(unittest.TestCase):
    def test_basis_keeps_versioned_sources_and_has_no_target(self):
        data = document()
        data["items"][0]["question"] = "Are both parts nonempty?"
        packet = exploration.build_basis(data)
        self.assertEqual(packet["mode"], "exploration")
        self.assertNotIn("claim_id", packet)
        self.assertNotIn("target_value", packet["network"])
        self.assertEqual([item["id"] for item in packet["items"]], ["H1", "H2", "H3", "D1", "T1"])
        self.assertEqual(packet["items"][3]["status"], "needs_confirmation")
        self.assertEqual(packet["items"][4]["version"], "2")
        self.assertEqual(packet["items"][4]["source"], "public course notes, Theorem 3")
        self.assertEqual(exploration._validate_saved_basis(packet), packet)

    def test_candidate_binds_the_actual_network_and_keeps_extra_context_separate(self):
        data = document()
        basis = exploration.build_basis(data)
        candidate, packet, claim = exploration._candidate(basis)
        self.assertEqual(candidate["target_value"], 9)
        self.assertEqual(candidate["support"],
                         {"cut_capacity": 9, "source_side": ["s"], "cuts_checked": 2})
        self.assertEqual(candidate["goal"]["formal"]["quantifier_domain"]["value_type"], "real")
        self.assertEqual(candidate["goal"]["status"], "needs_confirmation")
        self.assertTrue(all(item["status"] == "stated" for item in packet["items"][1:]))
        self.assertEqual(candidate["basis_item_ids"], ["H1", "H2", "H3"])
        self.assertEqual([item["id"] for item in packet["items"]], ["goal", "H1", "H2", "H3"])
        self.assertEqual(claim["small_case_spec"], {**data["network"], "target_value": 9})
        changed = deepcopy(data)
        changed["network"]["edges"][0][-1] = 2
        next_candidate, _, _ = exploration._candidate(exploration.build_basis(changed))
        self.assertEqual(next_candidate["target_value"], 7)

    def test_rejects_unsupported_or_changed_inputs(self):
        data = document()
        with self.assertRaisesRegex(ValueError, "public fixed-network"):
            exploration.build_basis({**data, "sensitivity": "restricted"})
        with self.assertRaisesRegex(ValueError, "without a target"):
            exploration.build_basis({**data, "network": {**data["network"], "target_value": 9}})
        with self.assertRaisesRegex(ValueError, "Duplicate or reserved"):
            exploration.build_basis({**data, "items": [data["items"][0], data["items"][0]]})
        tampered = exploration.build_basis(data)
        tampered["items"][1]["formal"]["flow_value_type"] = "integer"
        with self.assertRaisesRegex(ValueError, "canonical public basis"):
            exploration._validate_saved_basis(tampered)

    def test_model_sees_only_approved_public_math_fields(self):
        data = document()
        data["items"][0]["question"] = "An unpublished note."
        basis = exploration.build_basis(data)
        candidate, _, _ = exploration._candidate(basis)
        result = ModelRun({"target_value": 9, "analysis": "Try constructing a feasible flow of value nine.",
                           "basis_item_ids": ["D1"]}, {"id": "mock"}, {})
        with patch.object(exploration.v0a_models.ModelConfig, "from_env"), patch.object(
            exploration.v0a_models, "complete_structured", return_value=result
        ) as send:
            response = exploration._model_candidate(basis, candidate)
        payload = send.call_args.args[3]
        self.assertNotIn(data["title"], str(payload))
        self.assertNotIn("An unpublished note.", str(payload))
        self.assertEqual(payload["public_basis"][3]["formal"], basis["items"][3]["formal"])
        self.assertNotIn("target_value", payload)
        self.assertTrue(response["matches_cut_candidate"])
        self.assertEqual(response["basis_item_ids"], ["D1"])

    def test_model_cannot_add_new_source_or_select_another_goal(self):
        basis = exploration.build_basis(document())
        candidate, _, _ = exploration._candidate(basis)
        for bad in (
            {"analysis": "Use this", "basis_item_ids": ["unapproved"], "target_value": 9},
            {"analysis": "Change this", "basis_item_ids": ["H1"], "target_value": True},
        ):
            with self.subTest(bad=bad), patch.object(exploration.v0a_models.ModelConfig, "from_env"), patch.object(
                exploration.v0a_models, "complete_structured", return_value=ModelRun(bad, {}, {})
            ), self.assertRaisesRegex(ValueError, "listed basis"):
                exploration._model_candidate(basis, candidate)

    def test_disagreeing_model_value_does_not_replace_selectable_candidate(self):
        basis = exploration.build_basis(document())
        candidate, _, _ = exploration._candidate(basis)
        wrong = {"target_value": 19, "analysis": "Test whether nineteen is feasible.", "basis_item_ids": ["H2"]}
        with patch.object(exploration.v0a_models.ModelConfig, "from_env"), patch.object(
            exploration.v0a_models, "complete_structured", return_value=ModelRun(wrong, {}, {})
        ):
            response = exploration._model_candidate(basis, candidate)
        self.assertFalse(response["matches_cut_candidate"])
        self.assertEqual(response["target_value"], 19)
        self.assertEqual(candidate["target_value"], 9)
