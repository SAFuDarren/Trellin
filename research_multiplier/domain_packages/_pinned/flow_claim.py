"""Frozen contracts for the public fixed-network maximum-flow domain."""

from copy import deepcopy
from uuid import UUID, uuid4

from .flow_certificate import network, verify_flow_cut


CLAIM_TEXT = "The maximum feasible flow equals the specified target value in the fixed directed network."
HYPOTHESES = (
    "The directed network and its source, sink and arc capacities are fixed as specified.",
    "Flows are real and nonnegative, respect the edge capacities and conserve flow at all interior vertices.",
    "The value of a flow is net outflow at the source.",
)
CERTIFICATE_REQUIREMENTS = {
    "certificate_rule": "The certificate must contain exactly the keys flows and source_side. "
                        "Keep all other fields and derivation details out of the certificate.",
    "flows": "Array of [tail, head, nonnegative integer amount] for each directed edge, including zero flows.",
    "source_side": "Array of unique vertex names containing the source and excluding the sink.",
    "proof": "Justify feasibility and use the flow-cut upper bound for arbitrary feasible real flows, "
             "with the fixed network and frozen target. Include the exhibited flow and cut in proof_markdown.",
}


def _claim_contract(spec, title, claim_id):
    return {
        "format_version": 1,
        "claim_id": claim_id,
        "kind": "researcher_claim",
        "origin": "human",
        "title": title,
        "domain": "fixed-directed-max-flow",
        "statement": CLAIM_TEXT,
        "sensitivity": "public",
        "formal_skeleton": {},
        "small_case_spec": spec,
        "definition_ids": [],
        "hypotheses": [
            {"label": f"H{index}", "statement": clause, "formal": None}
            for index, clause in enumerate(HYPOTHESES, start=1)
        ],
        "conclusion": {
            "statement": "Every feasible flow has value at most target_value and some feasible flow attains it.",
            "quantifiers": [
                {"type": "forall", "over": "feasible flows"},
                {"type": "exists", "over": "feasible flows"},
            ],
            "parameters": [],
            "constant_dependencies": {},
        },
    }


def public_claim(spec, title):
    """Build the canonical contract before freezing a public network claim."""
    network(spec)
    if not isinstance(title, str) or not title.strip():
        raise ValueError("claim title must be nonempty text")
    return _claim_contract(deepcopy(spec), title, str(uuid4()))


def validate_claim_scope(frozen):
    """Return the network specification for a canonical frozen flow claim."""
    scope_error = "frozen claim is outside the scope of the fixed-network maximum-flow checker"
    if not isinstance(frozen, dict) or type(frozen.get("format_version")) is not int:
        raise ValueError(scope_error)
    claim_id, title, spec = frozen.get("claim_id"), frozen.get("title"), frozen.get("small_case_spec")
    if not isinstance(claim_id, str) or not isinstance(title, str) or not title.strip():
        raise ValueError(scope_error)
    try:
        canonical_id = str(UUID(claim_id))
    except ValueError:
        raise ValueError(scope_error) from None
    if frozen != _claim_contract(spec, title, canonical_id):
        raise ValueError(scope_error)
    network(spec)
    return spec


def verify_claim_certificate(frozen, certificate):
    """Check a certificate against the full frozen contract and its network."""
    return verify_flow_cut(validate_claim_scope(frozen), certificate)


def verify_claim_result(frozen, certificate):
    """Verify an exact optimum or its mismatch with the target on the same network."""
    if not isinstance(certificate, dict) or set(certificate) != {"actual_value", "flow_cut"}:
        return verify_claim_certificate(frozen, certificate)
    spec = validate_claim_scope(frozen)
    actual = certificate["actual_value"]
    if type(actual) is not int or actual == spec["target_value"]:
        raise ValueError("A refutation needs a different exact optimum on the frozen network.")
    result = verify_flow_cut({**spec, "target_value": actual}, certificate["flow_cut"])
    return {**result, "target_value": spec["target_value"], "evidence_kind": "counterexample"}
