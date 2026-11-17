"""Built-in checks. Each checker defines the exact statement it can certify."""

from fractions import Fraction


def vector(values, field, size=None):
    if not isinstance(values, list) or (size is not None and len(values) != size):
        raise ValueError(f"{field} must be a list" + (f" of length {size}" if size is not None else ""))
    if any(type(value) not in (str, int) for value in values):
        raise ValueError(f'{field} must use integers or quoted exact numbers, such as "3/2"')
    try:
        return [Fraction(value) for value in values]
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{field} must use integers or quoted exact numbers") from error


def dot(left, right):
    return sum(x * y for x, y in zip(left, right, strict=True))


def coloring_graph(data):
    vertices = data.get("vertices")
    edges = data.get("edges")
    count = data.get("colors")
    if not isinstance(vertices, list) or not vertices or any(
        not isinstance(vertex, str) or not vertex.strip() for vertex in vertices
    ):
        raise ValueError("vertices must be a nonempty list of names")
    if len(set(vertices)) != len(vertices):
        raise ValueError("vertex names must be unique")
    if type(count) is not int or not 1 <= count <= 8:
        raise ValueError("colors must be an integer from 1 to 8")
    if not isinstance(edges, list):
        raise ValueError("edges must be a list of pairs of vertex names")
    neighbors = {vertex: set() for vertex in vertices}
    for edge in edges:
        if not isinstance(edge, list) or len(edge) != 2 or any(
            not isinstance(vertex, str) or vertex not in neighbors for vertex in edge
        ):
            raise ValueError("each edge must connect two listed vertices")
        left, right = edge
        neighbors[left].add(right)
        neighbors[right].add(left)

    return vertices, edges, count, neighbors


def graph_coloring(data):
    vertices, _, count, neighbors = coloring_graph(data)
    statement = f"The supplied graph admits a proper coloring with at most {count} colors."
    if any(vertex in neighbors[vertex] for vertex in vertices):
        vertex = next(vertex for vertex in vertices if vertex in neighbors[vertex])
        return {
            "status": "refuted",
            "statement": statement,
            "method": "Self-loop obstruction",
            "finding": f"Vertex {vertex!r} would have to differ in color from itself.",
            "evidence": {"self_loop": vertex},
            "next_step": "Check whether the self-loop is an intended constraint in the input graph.",
        }
    if len(vertices) > 80 or count ** len(vertices) > 1_000_000:
        raise ValueError("graph exceeds the demo search limit: 80 vertices and 1,000,000 assignments")

    order = sorted(vertices, key=lambda vertex: (-len(neighbors[vertex]), vertex))
    coloring = {}
    trials = 0

    def search(index):
        nonlocal trials
        if index == len(order):
            return True
        vertex = order[index]
        for color in range(1, count + 1):
            trials += 1
            if any(coloring.get(other) == color for other in neighbors[vertex]):
                continue
            coloring[vertex] = color
            if search(index + 1):
                return True
            del coloring[vertex]
        return False

    found = search(0)
    if not found:
        finding = f"No valid assignment exists using {count} colors."
        next_step = "Compare a separate run with one more color if the demo input limits permit."
    elif count == 1:
        finding = "A valid one-color assignment exists."
        next_step = "One color is the minimum for this nonempty graph."
    else:
        finding = f"A valid assignment exists using at most {count} colors."
        next_step = "Try one fewer color to investigate whether this coloring uses the minimum."
    return {
        "status": "verified" if found else "refuted",
        "statement": statement,
        "method": "Exact finite search with adjacency constraints",
        "finding": finding,
        "evidence": {
            "vertices": len(vertices),
            "color_choices_checked": trials,
            "coloring": coloring if found else None,
        },
        "next_step": next_step,
    }


def linear_program(data):
    """Maximize c·x subject to Ax ≤ b and x ≥ 0."""
    objective = vector(data.get("c"), "c")
    rows = data.get("A")
    if not objective or not isinstance(rows, list) or not rows:
        raise ValueError("A and c must describe at least one constraint and variable")
    matrix = [vector(row, "A row", len(objective)) for row in rows]
    limits = vector(data.get("b"), "b", len(matrix))
    statement = "The supplied vectors certify a maximum for c·x under Ax ≤ b and x ≥ 0."
    x = vector(data.get("primal"), "primal", len(objective))
    y = vector(data.get("dual"), "dual", len(limits))
    primal_value = dot(objective, x)
    dual_value = dot(limits, y)
    checks = {
        "primal_feasible": all(value >= 0 for value in x) and all(
            dot(row, x) <= limit for row, limit in zip(matrix, limits, strict=True)
        ),
        "dual_feasible": all(value >= 0 for value in y) and all(
            dot(column, y) >= value
            for column, value in zip(zip(*matrix), objective, strict=True)
        ),
        "objectives_match": primal_value == dual_value,
    }
    passed = all(checks.values())
    return {
        "status": "verified" if passed else "certificate_rejected",
        "statement": statement,
        "method": "Exact rational primal and dual feasibility and matching objectives",
        "finding": (
            f"The maximum is {primal_value}." if passed else
            "The submitted vectors did not establish the optimum."
        ),
        "evidence": {**checks, "primal_objective": str(primal_value), "dual_objective": str(dual_value)},
        "next_step": (
            "Compare the same certificate against a separately specified instance if studying sensitivity."
            if passed else "Inspect the failed checks and submit corrected vectors in a new run."
        ),
    }


CHECKERS = {"graph-coloring": graph_coloring, "linear-program": linear_program}
