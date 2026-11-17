"""Frozen proper-coloring claims and exact witnesses for finite graphs."""

from copy import deepcopy
from itertools import product
from uuid import UUID, uuid4

from .domains import coloring_graph, graph_coloring
from .serialization import json_text


DOMAIN = "graph-coloring"
STATEMENT = "The specified undirected graph admits a proper coloring with at most the specified number of colors."
HYPOTHESES = (
    "The graph consists of the specified vertices and undirected edges.",
    "A proper coloring assigns a color to each vertex and gives different colors to the endpoints of each edge.",
)


def _scope(spec):
    if not isinstance(spec, dict) or set(spec) != {"vertices", "edges", "colors"}:
        raise ValueError("A graph-coloring specification requires vertices, edges and colors.")
    return coloring_graph(spec)


def _claim(spec, title, identifier):
    vertices, edges, count, _ = _scope(spec)
    return {
        "format_version": 1,
        "claim_id": identifier,
        "kind": "researcher_claim",
        "origin": "human",
        "title": title,
        "domain": DOMAIN,
        "statement": STATEMENT,
        "sensitivity": "public",
        "formal_skeleton": {"predicate": "graph_colorable", "colors": count},
        "small_case_spec": spec,
        "definition_ids": [],
        "hypotheses": [
            {"label": "H1", "statement": HYPOTHESES[0], "formal": {"vertices": vertices, "edges": edges}},
            {"label": "H2", "statement": HYPOTHESES[1], "formal": {"rule": "different_colors_on_each_edge"}},
        ],
        "conclusion": {
            "statement": f"There exists a proper coloring with at most {count} colors.",
            "quantifiers": [{"type": "exists", "over": "vertex colorings"}],
            "parameters": [],
            "constant_dependencies": {},
        },
    }


def public_claim(spec, title):
    if not isinstance(title, str) or not title.strip():
        raise ValueError("claim title must be nonempty text")
    return _claim(deepcopy(spec), title, str(uuid4()))


def validate_claim_scope(frozen):
    message = "frozen claim is outside the scope of the finite graph-coloring checker"
    if not isinstance(frozen, dict):
        raise ValueError(message)
    identifier = frozen.get("claim_id")
    title = frozen.get("title")
    if not isinstance(identifier, str) or not isinstance(title, str) or not title.strip():
        raise ValueError(message)
    spec = frozen.get("small_case_spec")
    if json_text(frozen) != json_text(_claim(spec, title, str(UUID(identifier)))):
        raise ValueError(message)
    return spec


def build_packet(claim, *, ambiguities=None):
    spec = validate_claim_scope(claim)
    if ambiguities is None:
        ambiguities = {}
    if (not isinstance(ambiguities, dict) or ambiguities.keys() - {"goal", "H1", "H2"}
            or any(not isinstance(question, str) or not question.strip() for question in ambiguities.values())):
        raise ValueError("Clarifications must map goal, H1, or H2 to nonempty questions.")
    items = [{
        "id": "goal", "kind": "goal", "natural": claim["conclusion"]["statement"],
        "formal": {
            "quantifier": "exists",
            "map": {"domain": "H1.vertices", "codomain": {"type": "integer", "min": 1, "max": spec["colors"]}},
            "condition": "H2",
        },
    }]
    items.extend({
        "id": item["label"], "kind": "hypothesis", "natural": item["statement"], "formal": item["formal"],
    } for item in claim["hypotheses"])
    for item in items:
        if item["id"] in ambiguities:
            item.update(status="needs_confirmation", question=ambiguities[item["id"]])
        else:
            item["status"] = "stated"
    return {"format_version": 1, "claim_id": claim["claim_id"], "items": items}


def validate_packet(body, claim):
    if not isinstance(body, dict) or not isinstance(body.get("items"), list):
        raise ValueError("Expected a structured graph-coloring review packet.")
    ambiguities = {}
    for item in body["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("Expected named review items.")
        if "question" in item:
            ambiguities[item["id"]] = item["question"]
    if json_text(body) != json_text(build_packet(claim, ambiguities=ambiguities)):
        raise ValueError("The review packet differs from the canonical graph-coloring claim scope.")


def candidate(spec):
    result = graph_coloring(spec)
    detail = result["evidence"]
    if result["status"] == "verified":
        certificate = {"coloring": detail["coloring"]}
    elif "self_loop" in detail:
        certificate = {"self_loop": detail["self_loop"]}
    else:
        certificate = {"exhaustive": True}
    return {
        "proof_markdown": result["finding"],
        "basis_items": ["H1", "H2"],
        "certificate": certificate,
        **({"evidence_kind": "counterexample"} if result["status"] == "refuted" else {}),
    }


def verify_claim_certificate(frozen, certificate):
    spec = validate_claim_scope(frozen)
    vertices, edges, count, neighbors = _scope(spec)
    if not isinstance(certificate, dict):
        raise ValueError("Expected a graph-coloring certificate.")
    result = {"colors": count, "vertices_checked": len(vertices), "edges_checked": len(edges)}
    if set(certificate) == {"coloring"}:
        coloring = certificate["coloring"]
        if (not isinstance(coloring, dict) or coloring.keys() != neighbors.keys()
                or any(type(color) is not int or not 1 <= color <= count for color in coloring.values())
                or any(coloring[left] == coloring[right] for left, right in edges)):
            raise ValueError("The certificate is not a proper coloring of the frozen graph.")
        return {**result, "method": "proper_coloring"}
    if set(certificate) == {"self_loop"}:
        vertex = certificate["self_loop"]
        if not isinstance(vertex, str) or vertex not in neighbors or vertex not in neighbors[vertex]:
            raise ValueError("The frozen graph has no self-loop on the named vertex.")
        return {**result, "method": "self_loop", "self_loop": vertex, "evidence_kind": "counterexample"}
    if set(certificate) != {"exhaustive"} or certificate["exhaustive"] is not True:
        raise ValueError("Expected a coloring, self-loop, or bounded exhaustive-check certificate.")
    if len(vertices) > 80 or count ** len(vertices) > 1_000_000:
        raise ValueError("Exhaustive checking is bounded to 80 vertices and 1,000,000 assignments.")
    indices = {vertex: position for position, vertex in enumerate(vertices)}
    indexed_edges = {(indices[left], indices[right]) for left, right in edges}
    assignments = product(range(1, count + 1), repeat=len(vertices))
    if any(all(colors[left] != colors[right] for left, right in indexed_edges) for colors in assignments):
        raise ValueError("A proper coloring exists, so an exhaustive refutation is invalid.")
    return {
        **result, "method": "exhaustive", "assignments_checked": count ** len(vertices),
        "evidence_kind": "counterexample",
    }
