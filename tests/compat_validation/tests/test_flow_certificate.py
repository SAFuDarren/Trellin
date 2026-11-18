from copy import deepcopy
import unittest

from research_multiplier.flow_certificate import check_small_cases, verify_flow_cut


class FlowCertificateTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "vertices": ["s", "a", "t"], "source": "s", "sink": "t", "target_value": 2,
            "edges": [["s", "a", 3], ["a", "t", 2], ["a", "s", 1]],
        }
        self.certificate = {
            "flows": [["s", "a", 3], ["a", "t", 2], ["a", "s", 1]],
            "source_side": ["s", "a"],
        }

    def test_flow_value_is_net_outflow(self):
        self.assertEqual(verify_flow_cut(self.spec, self.certificate)["value"], 2)

    def test_missing_capacity_or_conservation_and_wrong_cut_are_rejected(self):
        for path, value in [
            ("missing", None), ("capacity", 4), ("conservation", 0), ("cut", ["s"]),
        ]:
            candidate = deepcopy(self.certificate)
            if path == "missing":
                candidate["flows"].pop()
            elif path == "capacity":
                candidate["flows"][0][2] = value
            elif path == "conservation":
                candidate["flows"][-1][2] = value
            else:
                candidate["source_side"] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                verify_flow_cut(self.spec, candidate)

    def test_cut_below_target_is_a_counterexample(self):
        self.spec["target_value"] = 3
        self.assertEqual(check_small_cases(self.spec)["counterexample"], (2, ["a", "s"]))
