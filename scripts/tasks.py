# SPDX-License-Identifier: Apache-2.0
"""Claim, release and list roadmap tasks so parallel agents never work on the same issue.

Every agent works through the same GitHub account, so a label alone cannot say who holds a task.
A claim is a comment carrying a hidden marker with the agent's name and branch. After claiming,
the tool reads the issue back: if an earlier claim by another agent exists, it withdraws its own,
so two simultaneous claims end with exactly one holder.

    python scripts/tasks.py board
    python scripts/tasks.py claim 14 --agent claude-web --branch feat/14-rail-sections
    python scripts/tasks.py release 14 --agent claude-web --reason "handed back"
    python scripts/tasks.py check-pr 19          # used by CI

Requires the GitHub CLI (`gh`) signed in to an account with access to the repository.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

LABEL = "status:in-progress"
BLOCKING_LABELS = {"needs-decision"}
STALE_HOURS = 48
AGENT = re.compile(r"^[a-z0-9][a-z0-9._-]{1,40}$")
MARKER = re.compile(r"<!--\s*rm-(claim|release)\s+([^>]*?)\s*-->")
FIELD = re.compile(r"(\w+)=(\S+)")
LINK = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s+#(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Claim:
    agent: str
    branch: str
    at: str


def _fields(text: str) -> dict[str, str]:
    return dict(FIELD.findall(text))


def active_claims(comments: list[dict]) -> list[Claim]:
    """Claims not yet released, oldest first. A later claim by the same agent replaces its earlier one."""
    claims: dict[str, Claim] = {}
    for comment in sorted(comments, key=lambda item: item.get("createdAt", "")):
        for kind, payload in MARKER.findall(comment.get("body", "")):
            fields = _fields(payload)
            agent = fields.get("agent", "")
            if not agent:
                continue
            if kind == "claim":
                claims.pop(agent, None)
                claims[agent] = Claim(agent, fields.get("branch", ""), comment.get("createdAt", ""))
            else:
                claims.pop(agent, None)
    return sorted(claims.values(), key=lambda claim: claim.at)


def holder(comments: list[dict]) -> Claim | None:
    claims = active_claims(comments)
    return claims[0] if claims else None


def linked_issues(text: str) -> list[int]:
    return sorted({int(number) for number in LINK.findall(text or "")})


def hours_since(stamp: str, now: datetime | None = None) -> float:
    if not stamp:
        return 0.0
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return ((now or datetime.now(timezone.utc)) - moment).total_seconds() / 3600


def gh(*args: str, capture: bool = True) -> str:
    result = subprocess.run(["gh", *args], capture_output=capture, text=True)
    if result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:2])} failed: {(result.stderr or '').strip()}")
    return result.stdout


def issue(number: int) -> dict:
    return json.loads(gh("issue", "view", str(number), "--json", "number,title,state,labels,comments"))


def label_names(data: dict) -> set[str]:
    return {label["name"] for label in data.get("labels", [])}


def cmd_claim(args: argparse.Namespace) -> int:
    if not AGENT.match(args.agent):
        raise SystemExit("Use a short lowercase agent name, for example claude-web or codex-backend-1.")
    data = issue(args.issue)
    if data["state"] != "OPEN":
        raise SystemExit(f"#{args.issue} is closed.")
    blocked = label_names(data) & BLOCKING_LABELS
    if blocked:
        raise SystemExit(f"#{args.issue} is labelled {', '.join(sorted(blocked))}; the maintainer must decide first.")
    current = holder(data["comments"])
    if current and current.agent != args.agent:
        raise SystemExit(f"#{args.issue} is held by {current.agent} on {current.branch} since {current.at}.")
    if not current and LABEL in label_names(data):
        raise SystemExit(f"#{args.issue} carries {LABEL} without a recorded claim; ask the maintainer before taking it.")
    body = (f"<!-- rm-claim agent={args.agent} branch={args.branch} -->\n"
            f"Claimed by **{args.agent}** on branch `{args.branch}`.")
    if args.scope:
        body += "\n\nExpected scope: " + ", ".join(f"`{path}`" for path in args.scope)
    gh("issue", "comment", str(args.issue), "--body", body)
    gh("issue", "edit", str(args.issue), "--add-label", LABEL)
    # Read back: if another agent's claim landed first, give way.
    winner = holder(issue(args.issue)["comments"])
    if winner is None or winner.agent != args.agent:
        gh("issue", "comment", str(args.issue), "--body",
           f"<!-- rm-release agent={args.agent} -->\nReleased by **{args.agent}**: "
           f"{winner.agent if winner else 'another agent'} claimed first.")
        print(f"#{args.issue} was claimed first by {winner.agent if winner else 'another agent'}; withdrew.", file=sys.stderr)
        return 1
    print(f"Claimed #{args.issue} for {args.agent} on {args.branch}.")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    data = issue(args.issue)
    if not any(claim.agent == args.agent for claim in active_claims(data["comments"])):
        raise SystemExit(f"{args.agent} holds no claim on #{args.issue}.")
    reason = f": {args.reason}" if args.reason else ""
    gh("issue", "comment", str(args.issue), "--body",
       f"<!-- rm-release agent={args.agent} -->\nReleased by **{args.agent}**{reason}.")
    if not active_claims(issue(args.issue)["comments"]):
        gh("issue", "edit", str(args.issue), "--remove-label", LABEL)
    print(f"Released #{args.issue}.")
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    issues = json.loads(gh("issue", "list", "--state", "open", "--limit", "200", "--json",
                           "number,title,labels,milestone,comments"))
    pulls = json.loads(gh("pr", "list", "--state", "open", "--limit", "100", "--json", "number,headRefName,body,title"))
    links: dict[int, list[str]] = {}
    for pull in pulls:
        for number in linked_issues(pull.get("body", "")):
            links.setdefault(number, []).append(f"#{pull['number']} ({pull['headRefName']})")
    rows = []
    for item in sorted(issues, key=lambda entry: entry["number"]):
        claim = holder(item["comments"])
        labels = label_names(item)
        if claim:
            age = hours_since(claim.at)
            state = f"{claim.agent} on {claim.branch}, {age:.0f}h" + (" (stale?)" if age > STALE_HOURS else "")
        elif labels & BLOCKING_LABELS:
            state = "needs decision"
        elif LABEL in labels:
            state = "labelled but unclaimed"
        else:
            state = "free"
        if args.mine and (not claim or claim.agent != args.mine):
            continue
        milestone = (item.get("milestone") or {}).get("title", "")
        rows.append((f"#{item['number']}", item["title"][:58], milestone.split(" ")[0], state, ", ".join(links.get(item["number"], []))))
    widths = [max(len(row[column]) for row in rows + [("Issue", "Title", "Phase", "Status", "Open PRs")]) for column in range(5)]
    for row in [("Issue", "Title", "Phase", "Status", "Open PRs")] + rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    return 0


def cmd_check_pr(args: argparse.Namespace) -> int:
    pull = json.loads(gh("pr", "view", str(args.pr), "--json", "number,headRefName,body,labels"))
    if "no-issue" in {label["name"] for label in pull.get("labels", [])}:
        print("Labelled no-issue; skipping the claim check.")
        return 0
    numbers = linked_issues(pull.get("body", ""))
    if not numbers:
        print("The PR description must link its task, for example 'Closes #14' (or carry the no-issue label).", file=sys.stderr)
        return 1
    problems = []
    for number in numbers:
        data = issue(number)
        claims = active_claims(data["comments"])
        if data["state"] == "OPEN" and claims and not any(claim.branch == pull["headRefName"] for claim in claims):
            problems.append(f"#{number} is claimed by {claims[0].agent} on {claims[0].branch}, not this branch.")
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"Linked: {', '.join(f'#{number}' for number in numbers)}.")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claim and list roadmap tasks for parallel agents.")
    commands = parser.add_subparsers(dest="command", required=True)
    board = commands.add_parser("board", help="Show who holds which open issue.")
    board.add_argument("--mine", metavar="AGENT", help="Only the issues this agent holds.")
    claim = commands.add_parser("claim", help="Claim an issue for an agent and branch.")
    claim.add_argument("issue", type=int)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--branch", required=True)
    claim.add_argument("--scope", nargs="*", default=[], help="Files or directories you expect to change.")
    release = commands.add_parser("release", help="Release an issue you hold.")
    release.add_argument("issue", type=int)
    release.add_argument("--agent", required=True)
    release.add_argument("--reason", default="")
    check = commands.add_parser("check-pr", help="Check that a PR links an issue its branch holds.")
    check.add_argument("pr", type=int)
    args = parser.parse_args(argv)
    handlers = {"board": cmd_board, "claim": cmd_claim, "release": cmd_release, "check-pr": cmd_check_pr}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
