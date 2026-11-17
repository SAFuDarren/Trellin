"""Exact flow and cut checks for a fixed directed network with integer capacities."""

from collections import Counter
from itertools import combinations


NETWORK_FIELDS = {"vertices", "source", "sink", "edges", "target_value"}


def network(spec):
    if not isinstance(spec, dict):
        raise ValueError("expected a network specification object")
    if spec.keys() - NETWORK_FIELDS:
        raise ValueError("network specification contains unexpected fields")
    nodes = spec.get("vertices")
    source, sink = spec.get("source"), spec.get("sink")
    if (not isinstance(nodes, list) or not 2 <= len(nodes) <= 16
            or any(not isinstance(node, str) or not node for node in nodes)
            or len(set(nodes)) != len(nodes)
            or source not in nodes or sink not in nodes or source == sink):
        raise ValueError("expected 2–16 named vertices and distinct source and sink")
    edges = spec.get("edges")
    if not isinstance(edges, list) or not edges:
        raise ValueError("expected a nonempty edge list")
    capacities = {}
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 3:
            raise ValueError("each edge must give tail, head and integer capacity")
        tail, head, capacity = edge
        if (not isinstance(tail, str) or not isinstance(head, str)
                or tail not in nodes or head not in nodes or head == tail
                or (tail, head) in capacities or type(capacity) is not int or capacity < 0):
            raise ValueError("invalid or duplicate directed edge")
        capacities[tail, head] = capacity
    value = spec.get("target_value")
    if type(value) is not int or value < 0:
        raise ValueError("target_value must be a nonnegative integer")
    return nodes, source, sink, capacities, value


def check_small_cases(spec):
    nodes, source, sink, capacities, value = network(spec)
    interior = [node for node in nodes if node not in {source, sink}]
    checked, witnesses = 0, []
    for size in range(len(interior) + 1):
        for group in combinations(interior, size):
            side = {source, *group}
            capacity = sum(bound for (tail, head), bound in capacities.items()
                           if tail in side and head not in side)
            checked += 1
            if capacity < value:
                witnesses.append((capacity, sorted(side)))
    return {"cuts_checked": checked, "target_value": value,
            "counterexample": min(witnesses) if witnesses else None}


def verify_flow_cut(spec, certificate):
    nodes, source, sink, capacities, expected = network(spec)
    if not isinstance(certificate, dict) or set(certificate) != {"flows", "source_side"}:
        raise ValueError("certificate must contain flows and source_side")
    flows = certificate["flows"]
    if not isinstance(flows, list):
        raise ValueError("flows must list one integer flow for every edge")
    balance = Counter()
    seen = set()
    for flow in flows:
        if not isinstance(flow, list) or len(flow) != 3:
            raise ValueError("flow entries must be [tail, head, integer]")
        tail, head, amount = flow
        edge = tail, head
        if not isinstance(tail, str) or not isinstance(head, str) or edge not in capacities or edge in seen:
            raise ValueError("unknown or repeated flow edge")
        if type(amount) is not int or not 0 <= amount <= capacities[edge]:
            raise ValueError("flow exceeds capacity or is negative or non-integral")
        seen.add(edge)
        balance[tail] += amount
        balance[head] -= amount
    if seen != capacities.keys():
        raise ValueError("some edges have no assigned flow")
    if any(balance[node] != 0 for node in nodes if node not in {source, sink}):
        raise ValueError("flow is not conserved at an interior vertex")
    side = certificate["source_side"]
    if (not isinstance(side, list) or any(node not in nodes for node in side)
            or len(set(side)) != len(side) or source not in side or sink in side):
        raise ValueError("source_side must include source and exclude sink")
    sides = set(side)
    cut = sum(capacity for (tail, head), capacity in capacities.items()
              if tail in sides and head not in sides)
    if balance[source] != expected or cut != expected:
        raise ValueError("the feasible flow and cut must both match the frozen target value")
    return {"value": expected, "flow_value": balance[source], "cut_capacity": cut,
            "source_side": sorted(side), "edges_checked": len(capacities),
            "scope": "Maximum over all feasible real-valued flows in the frozen directed network."}
