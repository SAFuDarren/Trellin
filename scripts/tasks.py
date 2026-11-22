# SPDX-License-Identifier: Apache-2.0
"""Claim, release and list issues so parallel agents never work on the same task.

A claim is an owner-authored comment binding an agent to a repository, branch and contributor.
The owner confirms a fork contributor's request with --repository and --contributor. After claiming,
the tool reads the issue back and withdraws its record if another claim arrived first.

    python scripts/tasks.py board
    python scripts/tasks.py branches [--json]
    python scripts/tasks.py claim 14 --agent claude-web --branch feat/14-rail-sections
    python scripts/tasks.py release 14 --agent claude-web --reason "handed back"
    python scripts/tasks.py check-pr 19          # used by CI

Requires the GitHub CLI (`gh`) signed in to an account with access to the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LABEL = "status:in-progress"
BLOCKING_LABELS = {"needs-decision"}
PRIORITIES = {f"priority:p{number}": number for number in range(4)}
DIFFICULTIES = {f"difficulty:d{number}": f"D{number}" for number in range(1, 5)}
REVIEW_LABEL = "status:in-review"
STALE_HOURS = 48
AGENT = re.compile(r"^[a-z0-9][a-z0-9._-]{1,40}$")
REPOSITORY = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9._-]+")
LOGIN = re.compile(r"[A-Za-z0-9_-]+")
MARKER = re.compile(r"<!--\s*rm-(claim|release)\s+([^>]*?)\s*-->")
FIELD = re.compile(r"(\w+)=(\S+)")
LINK = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s+#(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Claim:
    agent: str
    branch: str
    at: str
    repository: str = ""
    contributor: str = ""

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return self.agent, self.branch, self.repository.casefold(), self.contributor.casefold()

    def marker(self, action: str) -> str:
        fields = f"agent={self.agent} branch={self.branch}"
        if self.repository:
            fields += f" repository={self.repository} contributor={self.contributor}"
        return f"<!-- rm-{action} {fields} -->"


def _fields(text: str) -> dict[str, str]:
    return dict(FIELD.findall(text))


def active_claims(comments: list[dict], owner: str) -> list[Claim]:
    """Owner-confirmed claims not yet released, oldest first."""
    claims: dict[tuple[str, str, str, str], Claim] = {}
    for comment in sorted(comments, key=lambda item: item.get("createdAt", "")):
        author = (comment.get("author") or {}).get("login") or ""
        if author.casefold() != owner.casefold():
            continue
        first_line = comment.get("body", "").partition("\n")[0]
        marker = MARKER.fullmatch(first_line)
        if not marker:
            continue
        kind, payload = marker.groups()
        fields = _fields(payload)
        agent = fields.get("agent", "")
        if not AGENT.fullmatch(agent):
            continue
        branch = fields.get("branch", "")
        repository = fields.get("repository", "")
        contributor = fields.get("contributor", "")
        if bool(repository) != bool(contributor):
            continue
        record = Claim(agent, branch, comment.get("createdAt", ""), repository, contributor)
        if kind == "claim":
            if branch and repository:
                claims.setdefault(record.identity, record)
            elif branch:
                claims = {key: claim for key, claim in claims.items() if claim.agent != agent or claim.repository}
                claims[record.identity] = record
        elif branch:
            claims.pop(record.identity, None)
        else:
            # Historical releases used the local agent label alone.
            claims = {key: claim for key, claim in claims.items() if claim.agent != agent or claim.repository}
    return sorted(claims.values(), key=lambda claim: claim.at)


def holder(comments: list[dict], owner: str) -> Claim | None:
    claims = active_claims(comments, owner)
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


def repository_name() -> str:
    args = ["repo", "view", "--json", "nameWithOwner"]
    target = os.environ.get("GH_REPO")
    if target:
        args.append(target)
    return json.loads(gh(*args))["nameWithOwner"]


def require_owner(owner: str) -> None:
    login = gh("api", "user", "--jq", ".login").strip()
    if login.casefold() != owner.casefold():
        raise SystemExit("Only the repository owner can confirm or release a claim. Request it in an issue comment.")


def cmd_claim(args: argparse.Namespace) -> int:
    if not AGENT.fullmatch(args.agent):
        raise SystemExit("Use a short lowercase agent name, for example claude-web or codex-backend-1.")
    branch_ref = subprocess.run(["git", "check-ref-format", f"refs/heads/{args.branch}"], capture_output=True)
    if branch_ref.returncode != 0 or any(character in args.branch for character in "<>"):
        raise SystemExit("Use a valid Git branch name without angle brackets.")
    if (args.repository or args.contributor) and (
        not REPOSITORY.fullmatch(args.repository) or not LOGIN.fullmatch(args.contributor)
    ):
        raise SystemExit("For a fork claim, supply both --repository owner/repo and --contributor login.")
    upstream = repository_name()
    owner = upstream.split("/", 1)[0]
    require_owner(owner)
    data = issue(args.issue)
    if data["state"] != "OPEN":
        raise SystemExit(f"#{args.issue} is closed.")
    blocked = label_names(data) & BLOCKING_LABELS
    if blocked:
        raise SystemExit(f"#{args.issue} is labelled {', '.join(sorted(blocked))}; the maintainer must decide first.")
    requested = Claim(args.agent, args.branch, "", args.repository or upstream, args.contributor or owner)
    current = holder(data["comments"], owner)
    if current:
        effective_identity = Claim(
            current.agent, current.branch, current.at, current.repository or upstream, current.contributor or owner,
        ).identity
        if effective_identity != requested.identity:
            raise SystemExit(f"#{args.issue} is held by {current.agent} on {current.branch} since {current.at}.")
        if LABEL not in label_names(data):
            gh("issue", "edit", str(args.issue), "--add-label", LABEL)
        print(f"#{args.issue} is already claimed for {args.agent} on {args.branch}.")
        return 0
    if not current and LABEL in label_names(data):
        raise SystemExit(f"#{args.issue} carries {LABEL} without a recorded claim; ask the maintainer before taking it.")
    details = f"Claimed by **{args.agent}** on branch `{args.branch}`."
    if args.repository:
        details = f"Confirmed **{args.agent}** for @{args.contributor} on `{args.repository}:{args.branch}`."
    body = f"{requested.marker('claim')}\n{details}"
    if args.scope:
        body += "\n\nExpected scope: " + ", ".join(f"`{path}`" for path in args.scope)
    gh("issue", "comment", str(args.issue), "--body", body)
    gh("issue", "edit", str(args.issue), "--add-label", LABEL)
    # Read back: if another agent's claim landed first, give way.
    winner = holder(issue(args.issue)["comments"], owner)
    if winner is None or winner.identity != requested.identity:
        gh("issue", "comment", str(args.issue), "--body",
           f"{requested.marker('release')}\nReleased by **{args.agent}**: "
           f"{winner.agent if winner else 'another agent'} claimed first.")
        print(f"#{args.issue} was claimed first by {winner.agent if winner else 'another agent'}; withdrew.", file=sys.stderr)
        return 1
    print(f"Claimed #{args.issue} for {args.agent} on {args.branch}.")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    owner = repository_name().split("/", 1)[0]
    require_owner(owner)
    data = issue(args.issue)
    record = next((claim for claim in active_claims(data["comments"], owner) if claim.agent == args.agent), None)
    if not record:
        raise SystemExit(f"{args.agent} holds no claim on #{args.issue}.")
    reason = f": {args.reason}" if args.reason else ""
    gh("issue", "comment", str(args.issue), "--body",
       f"{record.marker('release')}\nReleased by **{args.agent}**{reason}.")
    if not active_claims(issue(args.issue)["comments"], owner):
        gh("issue", "edit", str(args.issue), "--remove-label", LABEL)
    print(f"Released #{args.issue}.")
    return 0


def board_priority(labels: set[str]) -> tuple[int, str]:
    priorities = {label for label in labels if label.startswith("priority:")}
    if len(priorities) == 1:
        priority = next(iter(priorities))
        if priority in PRIORITIES:
            rank = PRIORITIES[priority]
            return rank, f"P{rank}"
    return len(PRIORITIES), "invalid" if priorities else "unset"


def board_difficulty(labels: set[str]) -> str:
    selected = {label for label in labels if label.startswith("difficulty:")}
    if len(selected) == 1:
        return DIFFICULTIES.get(next(iter(selected)), "invalid")
    return "invalid" if selected else "unset"


def board_state(labels: set[str], claim: Claim | None) -> tuple[int, str]:
    if labels & BLOCKING_LABELS:
        return 4, "needs decision"
    if REVIEW_LABEL in labels:
        return 0, "in review"
    if claim:
        return 2, "in progress"
    if LABEL in labels:
        return 4, "labelled but unclaimed"
    if "status:triage" in labels:
        return 3, "triage"
    return 1, "ready" if "status:ready" in labels else "free"


def cmd_board(args: argparse.Namespace) -> int:
    owner = repository_name().split("/", 1)[0]
    issues = json.loads(gh("issue", "list", "--state", "open", "--limit", "200", "--json",
                           "number,title,labels,milestone,comments,createdAt"))
    pulls = json.loads(gh("pr", "list", "--state", "open", "--limit", "100", "--json", "number,headRefName,body,title"))
    links: dict[int, list[str]] = {}
    for pull in pulls:
        for number in linked_issues(pull.get("body", "")):
            links.setdefault(number, []).append(f"#{pull['number']} ({pull['headRefName']})")
    rows = []
    for item in issues:
        claim = holder(item["comments"], owner)
        if args.mine and (not claim or claim.agent != args.mine):
            continue
        labels = label_names(item)
        priority_rank, priority = board_priority(labels)
        state_rank, state = board_state(labels, claim)
        if claim:
            age = hours_since(claim.at)
            branch = f"{claim.repository}:{claim.branch}" if claim.repository else claim.branch
            actor = f" ({claim.contributor})" if claim.contributor else ""
            state += f" · {claim.agent}{actor} on {branch}, {age:.0f}h"
            if age > STALE_HOURS:
                state += " (stale?)"
        number = item["number"]
        phase = (item.get("milestone") or {}).get("title", "")
        key = (priority_rank, state_rank, item["createdAt"], number)
        row = (
            f"#{number}", item["title"][:58], priority, board_difficulty(labels), phase, state,
            ", ".join(links.get(number, [])),
        )
        rows.append((key, row))
    header = ("Issue", "Title", "Priority", "Difficulty", "Phase", "Status", "Open PRs")
    table = [header, *(row for _, row in sorted(rows))]
    widths = [max(map(len, column)) for column in zip(*table)]
    for row in table:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    return 0


def cmd_branches(args: argparse.Namespace) -> int:
    from branch_inventory import git, inventory, print_inventory

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    repo = json.loads(gh("repo", "view", "--json", "nameWithOwner,defaultBranchRef"))
    repository = repo["nameWithOwner"]
    origin = git(root, "remote", "get-url", "origin").strip().rstrip("/").removesuffix(".git").lower()
    if not origin.endswith(("/" + repository.lower(), ":" + repository.lower())):
        raise SystemExit("GitHub repository and Git origin do not match.")

    def records(path):
        pages = json.loads(gh("api", "--method", "GET", "--paginate", "--slurp", f"repos/{repository}/{path}"))
        return [record for page in pages for record in page]

    pulls = records("pulls?state=all&per_page=100")
    issues = records("issues?state=open&labels=status%3Ain-progress&per_page=100")
    claims = {}
    for item in issues:
        if "pull_request" in item:
            continue
        number = item["number"]
        current = holder([
            {"createdAt": comment["created_at"], "body": comment["body"], "author": comment.get("user")}
            for comment in records(f"issues/{number}/comments?per_page=100")
        ], repository.split("/", 1)[0])
        if current and current.at and (current.repository or repository).casefold() == repository.casefold():
            claims[current.branch] = {"issue": number, "agent": current.agent, "at": current.at}

    result = inventory(root, repository, repo["defaultBranchRef"]["name"], pulls, claims,
                       datetime.now(timezone.utc), STALE_HOURS)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print_inventory(result)
    return 0


def cmd_check_pr(args: argparse.Namespace) -> int:
    upstream = repository_name()
    pull = json.loads(gh("api", f"repos/{upstream}/pulls/{args.pr}"))
    return check_pull_request(upstream, pull)


def check_pull_request(upstream: str, pull: dict) -> int:
    owner = upstream.split("/", 1)[0]
    if "no-issue" in label_names(pull):
        print("Labelled no-issue; skipping the claim check.")
        return 0
    head = pull["head"]
    branch = head["ref"]
    repository = (head.get("repo") or {}).get("full_name") or ""
    contributor = (pull.get("user") or {}).get("login") or ""
    open_issues = []
    problems = []
    for number in linked_issues(pull.get("body", "")):
        data = issue(number)
        if data["state"] != "OPEN":
            continue
        open_issues.append(number)
        blocked = label_names(data) & BLOCKING_LABELS
        current = holder(data["comments"], owner)
        if blocked:
            problems.append(f"#{number} is labelled {', '.join(sorted(blocked))}; the maintainer must decide first.")
        elif current is None:
            problems.append(f"#{number} has no active claim; claim it for this branch with scripts/tasks.py.")
        elif (
            current.branch != branch
            or (current.repository or upstream).casefold() != repository.casefold()
            or (current.contributor or owner).casefold() != contributor.casefold()
        ):
            expected = f"{current.repository or upstream}:{current.branch}"
            login = current.contributor or owner
            problems.append(f"#{number} is claimed by {current.agent} ({login}) on {expected}; coordinate a handover.")
    if not open_issues:
        problems.append("Link an open issue using 'Closes #N' or 'Refs #N', or use no-issue for a trivial PR.")
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"Linked: {', '.join(f'#{number}' for number in open_issues)}.")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claim and list issues for parallel agents.")
    parser.add_argument("--repo", help="GitHub upstream in owner/repo form, useful from a fork checkout.")
    commands = parser.add_subparsers(dest="command", required=True)
    board = commands.add_parser("board", help="Show open issues in urgency and pickup order.")
    board.add_argument("--mine", metavar="AGENT", help="Only the issues this agent holds.")
    branches = commands.add_parser("branches", help="Read branch and worktree lifecycle inventory.")
    branches.add_argument("--json", action="store_true", help="Emit machine-readable inventory.")
    claim = commands.add_parser("claim", help="Claim an issue for an agent and branch.")
    claim.add_argument("issue", type=int)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--branch", required=True)
    claim.add_argument("--repository", default="", help="The fork's owner/repo, confirmed by the owner.")
    claim.add_argument("--contributor", default="", help="GitHub login that will open the fork PR.")
    claim.add_argument("--scope", nargs="*", default=[], help="Files or directories you expect to change.")
    release = commands.add_parser("release", help="Release an issue you hold.")
    release.add_argument("issue", type=int)
    release.add_argument("--agent", required=True)
    release.add_argument("--reason", default="")
    check = commands.add_parser("check-pr", help="Check that a PR links an issue its branch holds.")
    check.add_argument("pr", type=int)
    args = parser.parse_args(argv)
    if args.repo:
        if not REPOSITORY.fullmatch(args.repo):
            parser.error("--repo requires owner/repo")
        os.environ["GH_REPO"] = args.repo
    handlers = {"board": cmd_board, "branches": cmd_branches, "claim": cmd_claim, "release": cmd_release, "check-pr": cmd_check_pr}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
