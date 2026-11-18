import unittest

from research_multiplier.workflows.derivations import validate_basis


class BasisTests(unittest.TestCase):
    def setUp(self):
        self.requirements = {
            "formalization": {
                "items": [{"id": "goal", "kind": "goal"},
                          {"id": "H1", "kind": "hypothesis"},
                          {"id": "H2", "kind": "hypothesis"},
                          {"id": "H3", "kind": "definition"}],
            },
            "required_basis_items": ["H1", "H2", "H3"],
            "verified_derivations": [{"id": "D:verified", "premise_item_ids": ["H1", "H2"]}],
        }

    def test_uses_confirmed_premises_or_same_context_derived_result(self):
        validate_basis(self.requirements, ["H1", "H2", "H3"])
        validate_basis(self.requirements, ["D:verified", "H3"])

    def test_goal_and_outside_items_are_not_premises(self):
        for item in ("goal", "H4", "D:other-proof"):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "unapproved or circular"):
                validate_basis(self.requirements, ["H1", "H2", "H3", item])

    def test_missing_premises_and_implicit_or_duplicate_declarations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "omits"):
            validate_basis(self.requirements, ["D:verified"])
        with self.assertRaisesRegex(ValueError, "nonempty"):
            validate_basis(self.requirements, None)
        with self.assertRaisesRegex(ValueError, "once"):
            validate_basis(self.requirements, ["H1", "H1", "H2", "H3"])
