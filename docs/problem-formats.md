# Problem files and check results

A problem file is a UTF-8 JSON object. The required text fields are:

| Field | Meaning |
| --- | --- |
| `title` | A short name for the work. |
| `question` | What you want to find out. |
| `success` | What outcome would count as progress. |
| `kind` | `open`, `graph-coloring`, or `linear-program`. |

You can add `assumptions`, `notes`, and `references` as lists of strings. These are carried into the report for a researcher to read. Checkers evaluate the structured `data` object they define.

## Built-in checks

**Open question.** Use `kind: "open"` to create a brief. No structured data is required. The result is `needs_review`.

**Graph coloring.** Provide `data.vertices` as a list of unique names, `data.edges` as pairs of names, and `data.colors` as the allowed number of colors, from 1 to 8. An assignment with different colors at both ends of every edge gives `verified`. An exhaustive search with no valid assignment, or a self-loop, gives `refuted`. The search is for small graphs, with at most 80 vertices and a search space of at most one million assignments.

**Linear programming.** Provide `data.A`, `data.b`, and `data.c` for the problem “maximize c·x subject to Ax ≤ b and x ≥ 0.” Supply the proposed allocation as `data.primal` and the proposed upper-bound certificate as `data.dual`. For exact data, use JSON integers or quoted numbers such as `"3/2"` or `"0.125"`. The checker tests Ax ≤ b and x ≥ 0, checks Aᵀy ≥ c and y ≥ 0, then compares c·x and b·y. When all checks pass, weak duality proves the optimum and the status is `verified`. If any check fails, the status is `certificate_rejected`. The optimizer can come from any tool that gives you candidate vectors.

## Extend domain checking

The standalone `run` and `replay` commands use the built-in problem kinds above. For registered domain checkers in the persistent PostgreSQL workflow, use the [domain package interface](domain-packages.md). [Contributing](../CONTRIBUTING.md#find-the-code) maps the active packages and the archived implementations used to replay earlier evidence.

A replay recomputes the deterministic check from the saved input and compares it with the recorded result. The input hash detects a changed snapshot relative to the recorded hash. Use a trusted checker version when reviewing somebody else's artifacts.
