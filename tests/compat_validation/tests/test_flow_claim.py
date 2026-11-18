from copy import deepcopy
import unittest

from research_multiplier.flow_claim import public_claim, verify_claim_certificate


class FlowClaimTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "vertices": ["s", "t"],
            "source": "s",
            "sink": "t",
            "edges": [["s", "t", 2]],
            "target_value": 2,
        }
        self.certificate = {"flows": [["s", "t", 2]], "source_side": ["s"]}

    def test_valid_certificate_applies_to_the_frozen_flow_statement(self):
        claim = public_claim(self.spec, "Two-vertex maximum flow")
        self.assertEqual(verify_claim_certificate(claim, self.certificate)["value"], 2)

    def test_valid_flow_certificate_cannot_certify_a_changed_claim(self):
        frozen = public_claim(self.spec, "Two-vertex maximum flow")
        unrelated = deepcopy(frozen)
        unrelated["domain"] = "unrelated-theory"
        unrelated["statement"] = "0 = 1"
        unrelated["conclusion"]["statement"] = "0 = 1"
        for changed in (
            unrelated,
            {**frozen, "statement": "0 = 1"},
            {**frozen, "definition_ids": ["00000000-0000-0000-0000-000000000000"]},
            {**frozen, "hypotheses": frozen["hypotheses"][1:]},
            {**frozen, "conclusion": {**frozen["conclusion"], "quantifiers": []}},
            {**frozen, "formal_skeleton": {"statement": "False"}},
            {**frozen, "format_version": True},
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "scope"):
                verify_claim_certificate(changed, self.certificate)

    def test_extra_network_fields_are_rejected_at_creation_and_verification(self):
        claim = public_claim(self.spec, "Two-vertex maximum flow")
        spec = {**self.spec, "sensitivity": "restricted", "notes": "example private material"}
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            public_claim(spec, "Two-vertex maximum flow")
        with self.assertRaisesRegex(ValueError, "unexpected fields"):
            verify_claim_certificate({**claim, "small_case_spec": spec}, self.certificate)
