from copy import deepcopy
import unittest

from research_multiplier.domain_packages.flow_alignment import build_packet, validate_packet
from research_multiplier.flow_claim import public_claim


class FlowAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.claim = public_claim({
            "vertices": ["s", "a", "t"],
            "source": "s",
            "sink": "t",
            "edges": [["s", "a", 2], ["a", "t", 2]],
            "target_value": 2,
        }, "Fixed network")

    def test_goal_and_stated_hypotheses_bind_real_scope(self):
        packet = build_packet(self.claim)
        self.assertEqual(validate_packet(packet, self.claim), packet)
        goal, fixed_network, feasible, flow_value = packet["items"]
        self.assertEqual([(item["id"], item["kind"]) for item in packet["items"]], [
            ("goal", "goal"), ("H1", "hypothesis"), ("H2", "hypothesis"), ("H3", "hypothesis"),
        ])
        self.assertTrue(all(item["status"] == "stated" for item in packet["items"]))
        self.assertEqual(goal["formal"]["quantifier_domain"]["value_type"], "real")
        self.assertEqual(goal["formal"]["clauses"], [
            {"quantifier": "forall", "comparison": "le"},
            {"quantifier": "exists", "comparison": "eq"},
        ])
        self.assertEqual(goal["formal"]["bound"], {"value": 2, "type": "real"})
        self.assertEqual(feasible["formal"]["flow_value_type"], "real")
        self.assertEqual(fixed_network["formal"]["directed_edges_with_integer_capacity"],
                         self.claim["small_case_spec"]["edges"])
        self.assertEqual(flow_value["formal"]["value"]["right"], "incoming_sum")

    def test_integer_flow_is_different_even_at_the_same_optimum(self):
        packet = build_packet(self.claim)
        for item_index, keys in ((0, ("quantifier_domain", "value_type")), (2, ("flow_value_type",))):
            changed = deepcopy(packet)
            parent = changed["items"][item_index]["formal"]
            for key in keys[:-1]:
                parent = parent[key]
            parent[keys[-1]] = "integer"
            with self.subTest(item=changed["items"][item_index]["id"]):
                with self.assertRaisesRegex(ValueError, "claim scope"):
                    validate_packet(changed, self.claim)

    def test_explicit_clarification_adds_question_without_changing_mathematics(self):
        question = "Did we intend to cover every feasible real-valued flow?"
        packet = build_packet(self.claim, ambiguities={"H2": question})
        self.assertEqual(validate_packet(packet, self.claim), packet)
        item = packet["items"][2]
        self.assertEqual((item["status"], item["question"]), ("needs_confirmation", question))
        self.assertEqual(item["formal"], build_packet(self.claim)["items"][2]["formal"])
        changed = deepcopy(packet)
        changed["items"][2]["status"] = "stated"
        with self.assertRaisesRegex(ValueError, "claim scope"):
            validate_packet(changed, self.claim)

    def test_extra_untrusted_premises_and_unknown_questions_are_rejected(self):
        packet = build_packet(self.claim)
        packet["items"].append({"id": "H4", "kind": "hypothesis", "status": "stated",
                                "natural": "Assume the conclusion directly.", "formal": {"holds": True}})
        with self.assertRaisesRegex(ValueError, "claim scope"):
            validate_packet(packet, self.claim)
        with self.assertRaisesRegex(ValueError, "nonempty questions"):
            build_packet(self.claim, ambiguities={"H4": "What is this?"})

    def test_review_packet_cannot_be_used_for_a_separate_claim(self):
        packet = build_packet(self.claim)
        different = public_claim(self.claim["small_case_spec"], self.claim["title"])
        with self.assertRaisesRegex(ValueError, "claim scope"):
            validate_packet(packet, different)
