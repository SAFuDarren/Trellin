# SPDX-License-Identifier: Apache-2.0
"""Render the remote portion of the branch inventory or update its tracking comment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from html import escape

from tasks import gh

TRACKER = "SAFuDarren/Trellin"
ISSUE = 205
MARKER = "<!-- trellin-weekly-branch-inventory -->"
REPORT_AUTHOR = "github-actions[bot]"
ROWS_PER_SECTION = 40


def branch_line(row: dict) -> str:
    facts = [f"{row['branch']}  {row['head']}", row["reason"]]
    if pull := row.get("pull_request"):
        facts.append(f"PR #{pull['number']} · {pull['state']} · head {pull['final_head']} · base {pull['base']}")
        if pull["merge_commit"]:
            facts.append(f"merge {pull['merge_commit']}")
    if claim := row.get("claim"):
        facts.append(f"claim #{claim['issue']} ({claim['agent']})")
    if registration := row.get("preservation"):
        facts.append(f"{registration['owner']} · review {registration['review_on']}")
    return escape(" | ".join(facts))


def render_report(result: dict) -> str:
    branches = result["remote_branches"]
    counts = Counter(row["state"] for row in branches)
    summary = ", ".join(f"{state.replace('_', ' ')} {count}" for state, count in sorted(counts.items()))
    lines = [
        MARKER,
        "### Weekly remote branch inventory",
        "",
        f"Observed {result['observed_at']} · {len(branches)} upstream branches · {summary}",
        "",
        "Verify the exact tip, merge reachability, active claims, dependent PRs, and local worktrees "
        "before retiring a branch. Closed unmerged work requires a preservation or retirement decision.",
        "",
    ]
    if result["remote_coverage"]["heads_missing_locally"]:
        lines.extend(["Some remote history was unavailable in the runner. Review those tips locally.", ""])

    candidates = [row for row in branches if row["state"] == "ready_to_retire"]
    sections = (
        ("Merged PR heads to verify", [row for row in candidates if row["pull_request"]["state"] == "merged"]),
        ("Closed unmerged branches to review", [row for row in candidates if row["pull_request"]["state"] == "closed"]),
        ("Stale branches to review", [row for row in branches if row["state"] == "stale"]),
        ("Preserved branches due for review", [row for row in branches if row.get("review_due")]),
    )
    for title, rows in sections:
        if rows:
            lines.extend([f"<details><summary>{title} ({len(rows)})</summary>", "", "<pre>"])
            lines.extend(branch_line(row) for row in rows[:ROWS_PER_SECTION])
            if len(rows) > ROWS_PER_SECTION:
                lines.append(f"… {len(rows) - ROWS_PER_SECTION} more in the full inventory.")
            lines.extend(["</pre>", "</details>", ""])

    lines.append("Run `python scripts/tasks.py branches` in a local checkout for the full inventory and worktree evidence.")
    return "\n".join(lines)


def publish_report(body: str) -> None:
    path = f"repos/{TRACKER}/issues"
    comments_path = f"{path}/{ISSUE}/comments"
    pages = json.loads(gh("api", "--paginate", "--slurp", f"{comments_path}?per_page=100"))
    existing = next(
        (
            comment
            for page in pages
            for comment in page
            if (comment.get("user") or {}).get("login") == REPORT_AUTHOR
            and (comment.get("body") or "").startswith(f"{MARKER}\n")
        ),
        None,
    )
    if existing is None:
        gh("api", "--method", "POST", comments_path, "-f", f"body={body}")
    elif existing["body"] != body:
        gh("api", "--method", "PATCH", f"{path}/comments/{existing['id']}", "-f", f"body={body}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Update the tracking comment in GitHub Actions.")
    args = parser.parse_args()
    result = json.load(sys.stdin)
    body = render_report(result)
    if args.publish:
        if os.environ.get("GITHUB_REPOSITORY") != TRACKER or result["repository"] != TRACKER:
            raise SystemExit("The weekly report can only publish Trellin's upstream inventory.")
        publish_report(body)
    else:
        print(body)


if __name__ == "__main__":
    main()
