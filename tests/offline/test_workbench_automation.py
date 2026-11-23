# SPDX-License-Identifier: Apache-2.0
"""Automation templates name what a claim lacks and validate requests before touching the database (#94)."""

import pytest

from research_multiplier.workflows import workbench_automation as automation

pytestmark = pytest.mark.offline
CLAIM = "11111111-1111-4111-8111-111111111111"


def row(**values):
    return {"packet_id": "p", "proof_ready": True, "domain": "lean-core", "lean_ready": True, **values}


@pytest.mark.parametrize("values, lean, observe", [
    ({}, None, None),
    ({"packet_id": None}, "basis", "basis"),
    ({"proof_ready": False}, "questions", "questions"),
    ({"lean_ready": False}, "lean_statement", None),
    ({"domain": "fixed-directed-max-flow", "lean_ready": False}, "lean_statement", None),
])
def test_each_template_reports_the_missing_prerequisite(values, lean, observe):
    assert automation._missing("lean-reuse", row(**values)) == lean
    assert automation._missing("observe", row(**values)) == observe


def test_templates_build_the_existing_policy_specifications():
    lean = automation._specification("lean-reuse", row(packet_id="p1", lean_statement_id="s1"))
    assert lean == {"packet_id": "p1", "lean_statement_id": "s1", "metric": "exact-closure", "actions": ["lean"],
                    "lean_model_rounds": 0}
    assert automation._specification("observe", row(packet_id="p1"))["actions"] == []
    flow = automation._specification("observe", row(packet_id="p1", domain="fixed-directed-max-flow"))
    assert flow["metric"] == "flow-interval-width"


class NoStore:
    def query(self, *_):
        pytest.fail("Invalid requests are refused before the database is read.")


@pytest.mark.parametrize("action, document, code", [
    (automation.propose, {"claim_id": CLAIM}, "automation.invalid"),
    (automation.propose, {"claim_id": "not-a-uuid", "template": "observe"}, "automation.invalid"),
    (automation.propose, {"claim_id": CLAIM, "template": "run-everything"}, "automation.invalid"),
    (automation.approve, {"policy_id": CLAIM, "fingerprint": "f", "reason": "  "}, "automation.reason"),
    (automation.stop, {"policy_id": CLAIM, "reason": "x", "extra": 1}, "automation.invalid"),
])
def test_requests_are_validated_first(action, document, code):
    with pytest.raises(automation.AutomationError) as error:
        action(NoStore(), document)
    assert error.value.code == code
