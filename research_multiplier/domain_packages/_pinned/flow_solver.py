"""Generate an exact fixed-network witness for the independent flow checker."""

from collections import deque

from .flow_certificate import network


def _reachable(source, residual, neighbors, sink=None):
    visited = {source: None}
    frontier = deque([source])
    while frontier:
        tail = frontier.popleft()
        for head in neighbors[tail]:
            if head in visited or residual[tail, head] == 0:
                continue
            visited[head] = tail
            if head == sink:
                return visited
            frontier.append(head)
    return visited


def solve(spec):
    """Augment along shortest residual paths, then exhibit the reachable cut."""
    vertices, source, sink, capacities, _ = network(spec)
    adjacency = {vertex: set() for vertex in vertices}
    residual = {edge: capacity for edge, capacity in capacities.items()}
    for tail, head in capacities:
        adjacency[tail].add(head)
        adjacency[head].add(tail)
        residual.setdefault((head, tail), 0)
    neighbors = {vertex: sorted(adjacent) for vertex, adjacent in adjacency.items()}
    value = 0
    while True:
        parents = _reachable(source, residual, neighbors, sink)
        if sink not in parents:
            break
        path = []
        node = sink
        while parents[node] is not None:
            parent = parents[node]
            path.append((parent, node))
            node = parent
        increment = min(residual[edge] for edge in path)
        for tail, head in path:
            residual[tail, head] -= increment
            residual[head, tail] += increment
        value += increment
    return value, {
        "flows": [[tail, head, max(0, capacity - residual[tail, head])]
                  for (tail, head), capacity in capacities.items()],
        "source_side": sorted(parents),
    }


def candidate(spec):
    value, certificate = solve(spec)
    if value != spec["target_value"]:
        return {"failure_reason": f"Deterministic search found a different optimum ({value}).",
                "observed_value": value, "certificate": certificate}
    return {
        "proof_markdown": (
            f"The certificate exhibits a feasible flow of value {value}. The displayed cut has "
            f"capacity {value}, an upper bound for every feasible real flow in the frozen network."
        ),
        "certificate": certificate,
        "basis_items": ["H1", "H2", "H3"],
    }
