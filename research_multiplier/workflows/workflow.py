"""Run a check and keep an input snapshot with its evidence."""

from datetime import datetime, timezone

from research_multiplier.domains import CHECKERS
from research_multiplier.protocol.serialization import fingerprint, json_text


def check(problem):
    if not isinstance(problem, dict):
        raise ValueError("the problem must be a JSON object")
    fields = ("title", "question", "success", "kind")
    for field in fields:
        value = problem.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a nonempty string")
    for field in ("assumptions", "notes", "references"):
        entries = problem.get(field, [])
        if not isinstance(entries, list) or any(not isinstance(item, str) for item in entries):
            raise ValueError(f"{field} must be a list of text entries")

    kind = problem["kind"]
    if kind == "open":
        return {
            "status": "needs_review",
            "statement": None,
            "method": "Research brief",
            "finding": "This run organized the question for review.",
            "evidence": {},
            "next_step": "Choose one test that can distinguish two plausible answers to the question.",
        }
    if kind not in CHECKERS:
        raise ValueError(f"unknown kind {kind!r}; choose open, graph-coloring, or linear-program")
    data = problem.get("data")
    if not isinstance(data, dict):
        raise ValueError(f"data must be an object for {kind}")
    return CHECKERS[kind](data)


def start(problem):
    evidence = check(problem)
    return {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "problem_sha256": fingerprint(problem),
        "problem": problem,
        "result": evidence,
    }


def replay(run):
    if not isinstance(run, dict) or run.get("format_version") != 1:
        raise ValueError("expected a version 1 run.json")
    if "problem" not in run or run.get("problem_sha256") != fingerprint(run["problem"]):
        raise ValueError("problem snapshot hash does not match")
    return check(run["problem"]) == run.get("result")


def report(run):
    problem = run["problem"]
    result = run["result"]
    lines = [
        f"# {problem['title']}",
        "",
        "## Research question",
        "",
        problem["question"],
        "",
        "## What counts as progress",
        "",
        problem["success"],
        "",
        "## Check result",
        "",
        f"**Status** `{result['status']}`",
        "",
        result["finding"],
        "",
        f"**Method** {result['method']}",
    ]
    if result["statement"]:
        lines += [
            "",
            f"**Statement checked** {result['statement']}",
            "",
            "The check covers the structured input for this run. Compare its statement with the research question above.",
        ]
    if result["evidence"]:
        lines += ["", "````json", json_text(result["evidence"]), "````"]
    for field, name in (("assumptions", "Assumptions supplied"), ("notes", "Notes supplied"), ("references", "References supplied")):
        if problem.get(field):
            lines += ["", f"## {name}", "", *[f"- {item}" for item in problem[field]]]
    lines += ["", "## Next step", "", result["next_step"]]
    lines += ["", "## Reproducibility", "", f"Input SHA-256 `{run['problem_sha256']}`", ""]
    return "\n".join(lines)
