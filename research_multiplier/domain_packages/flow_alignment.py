"""Canonical, structured review items for the fixed-network flow claim."""

from copy import deepcopy

from research_multiplier.flow_claim import HYPOTHESES, validate_claim_scope
from research_multiplier.protocol.serialization import json_text


_ITEM_IDS = {"goal", "H1", "H2", "H3"}


def build_packet(claim, *, ambiguities=None):
    """Translate the frozen claim's structured input without proposing new premises."""
    spec = validate_claim_scope(claim)
    if ambiguities is None:
        ambiguities = {}
    if (not isinstance(ambiguities, dict) or ambiguities.keys() - _ITEM_IDS
            or any(not isinstance(question, str) or not question.strip() for question in ambiguities.values())):
        raise ValueError("Clarifications must map goal, H1, H2, or H3 to nonempty questions.")

    items = [
        {
            "id": "goal",
            "kind": "goal",
            "natural": claim["conclusion"]["statement"].replace("target_value", str(spec["target_value"])),
            "formal": {
                "quantifier_domain": {"set": "feasible_flows", "value_type": "real", "defined_by": "H2"},
                "clauses": [
                    {"quantifier": "forall", "comparison": "le"},
                    {"quantifier": "exists", "comparison": "eq"},
                ],
                "expression": {"term": "flow_value", "defined_by": "H3"},
                "bound": {"value": spec["target_value"], "type": "real"},
            },
        },
        *flow_hypotheses(spec),
    ]
    for item in items:
        question = ambiguities.get(item["id"])
        if question:
            item.update(status="needs_confirmation", question=question)
        else:
            item["status"] = "stated"
    return {"format_version": 1, "claim_id": claim["claim_id"], "items": items}


def flow_hypotheses(spec):
    """Describe the network and feasible real-valued flows independently of a target."""
    return [
        {
            "id": "H1",
            "kind": "hypothesis",
            "natural": HYPOTHESES[0],
            "formal": {
                "vertices": list(spec["vertices"]),
                "source": spec["source"],
                "sink": spec["sink"],
                "directed_edges_with_integer_capacity": deepcopy(spec["edges"]),
            },
        },
        {
            "id": "H2",
            "kind": "hypothesis",
            "natural": HYPOTHESES[1],
            "formal": {
                "network": "H1",
                "flow_value_type": "real",
                "constraints": [
                    {"rule": "nonnegative", "on": "all_edges"},
                    {"rule": "at_most_edge_capacity", "on": "all_edges"},
                    {"rule": "incoming_sum_equals_outgoing_sum", "on": "vertices_other_than_source_and_sink"},
                ],
            },
        },
        {
            "id": "H3",
            "kind": "hypothesis",
            "natural": HYPOTHESES[2],
            "formal": {
                "network": "H1",
                "vertex": spec["source"],
                "value": {"operation": "subtract", "left": "outgoing_sum", "right": "incoming_sum"},
            },
        },
    ]

def validate_packet(body, claim):
    """Reject a packet that changes the claim's specific mathematical scope."""
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise ValueError("Expected a structured fixed-network review packet.")
    ambiguities = {}
    for item in body["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Expected named review items.")
        if "question" in item:
            ambiguities[item["id"]] = item["question"]
    expected = build_packet(claim, ambiguities=ambiguities)
    if json_text(body) != json_text(expected):
        raise ValueError("The review packet differs from the canonical fixed-network claim scope.")
    return expected
