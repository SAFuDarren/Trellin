# SPDX-License-Identifier: Apache-2.0
"""Keep contributors' local information out of the public repository and its records.

This repository is public. Nothing from a contributor's own machine may reach tracked files, issue
or PR text, comments, commit messages or commit identities: local paths and user names, temporary
directories, personal email addresses, or the names of private projects. Describe problems with a
generic reproduction on synthetic fixtures instead.

Usage:
    check_privacy.py FILE...          scan files (use - for standard input)
    check_privacy.py --tracked        scan every tracked text file
    check_privacy.py --pr NUMBER      scan a PR's title, body, comments, commit messages and identities

Private terms (your own project, paper or collaborator names) are never listed in the repository.
Put them one per line in the file named by RM_PRIVATE_TERMS_FILE, or in
~/.config/research-multiplier/private-terms.txt. They are matched case-insensitively.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Placeholders that documentation and tests may use.
_HOME_PLACEHOLDERS = r"(?:you|your-name|researcher|example|name|USER|Shared|runner|user)"
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("home path", re.compile(rf"(?<![\w.])/Users/(?!{_HOME_PLACEHOLDERS}\b)[A-Za-z0-9._-]+"),
     "write ~/… or /Users/you/…"),
    ("home path", re.compile(rf"(?<![\w.])/home/(?!{_HOME_PLACEHOLDERS}\b|ubuntu\b)[a-z_][a-z0-9_-]*"),
     "write ~/… or /home/you/…"),
    ("home path", re.compile(r"\b[A-Za-z]:\\+Users\\+(?!(?:you|example|runneradmin)\b)[A-Za-z0-9._-]+"),
     "write %USERPROFILE%\\…"),
    ("temporary directory", re.compile(r"/(?:private/)?var/folders/[A-Za-z0-9_+-]{2}/"),
     "describe the step instead of the local temporary path"),
    ("temporary directory", re.compile(r"/(?:private/)?tmp/[A-Za-z0-9._-]*\.[A-Za-z0-9]{8}\b"),
     "describe the step instead of the mktemp directory"),
    ("personal email", re.compile(
        r"[A-Za-z0-9._%+-]+@(?:gmail|googlemail|qq|163|126|yeah|foxmail|outlook|hotmail|live|msn|icloud|me|mac"
        r"|yahoo|proton|protonmail|pm)\.(?:com|me|net|cn)\b", re.IGNORECASE),
     "use the GitHub noreply address"),
]
NOREPLY = re.compile(r"@users\.noreply\.github\.com$", re.IGNORECASE)
SKIP_PREFIXES = ("research_multiplier/workbench/app/assets/",)


@dataclass(frozen=True)
class Finding:
    where: str
    line: int
    kind: str
    text: str
    hint: str

    def __str__(self) -> str:
        return f"{self.where}:{self.line}: {self.kind} '{self.text}' — {self.hint}"


def private_terms() -> list[str]:
    configured = os.environ.get("RM_PRIVATE_TERMS_FILE")
    file = Path(configured) if configured else Path.home() / ".config/research-multiplier/private-terms.txt"
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def scan_text(text: str, where: str, terms: list[str] | None = None) -> list[Finding]:
    terms = private_terms() if terms is None else terms
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for kind, pattern, hint in PATTERNS:
            for match in pattern.finditer(line):
                found.append(Finding(where, number, kind, match.group(0), hint))
        lowered = line.casefold()
        for term in terms:
            if term.casefold() in lowered:
                found.append(Finding(where, number, "private term", term, "describe it generically"))
    return found


def scan_identity(name: str, email: str, where: str) -> list[Finding]:
    if not email or NOREPLY.search(email):
        return []
    return [Finding(where, 0, "commit identity", f"{name} <{email}>",
                    "set git config user.email to your GitHub noreply address")]


def tracked_files() -> list[Path]:
    output = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [ROOT / name for name in output.decode().split("\0") if name and not name.startswith(SKIP_PREFIXES)]


def scan_tracked() -> list[Finding]:
    found = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found.extend(scan_text(text, str(path.relative_to(ROOT))))
    return found


SIGNOFF = re.compile(r"^(?:Signed-off-by|Co-authored-by): *(.*?) *<([^>]+)>", re.IGNORECASE | re.MULTILINE)


def scan_pull_request(records: dict) -> list[Finding]:
    """Scan PR records shaped like the GitHub REST API: pull, issue comments, review comments, commits."""
    pull = records["pull"]
    found = scan_text(f"{pull.get('title') or ''}\n{pull.get('body') or ''}", f"PR #{pull['number']}")
    for comment in records.get("comments", []) + records.get("review_comments", []):
        found.extend(scan_text(comment.get("body") or "", f"comment {comment['id']}"))
    for commit in records.get("commits", []):
        sha = commit["sha"][:7]
        detail = commit["commit"]
        found.extend(scan_text(detail.get("message") or "", f"commit {sha}"))
        for role in ("author", "committer"):
            person = detail.get(role) or {}
            if role == "committer" and person.get("email", "").casefold() == "noreply@github.com":
                continue  # GitHub's own web-flow committer
            found.extend(scan_identity(person.get("name", ""), person.get("email", ""), f"commit {sha} {role}"))
        for name, email in SIGNOFF.findall(detail.get("message") or ""):
            if "anthropic.com" in email.casefold() or "openai.com" in email.casefold():
                continue  # tool attribution lines, not a contributor's address
            found.extend(scan_identity(name, email, f"commit {sha} sign-off"))
    return found


def fetch_pull_request(number: int, repository: str | None = None) -> dict:
    def api(path: str) -> list | dict:
        flags = ["--paginate", "--slurp"] if "?" in path else []
        result = subprocess.run(["gh", "api", path, *flags], capture_output=True, text=True, check=True).stdout
        data = json.loads(result)
        return [item for page in data for item in page] if flags else data

    repo = repository or subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                                        capture_output=True, text=True, check=True).stdout.strip()
    base = f"repos/{repo}"
    return {
        "pull": api(f"{base}/pulls/{number}"),
        "comments": api(f"{base}/issues/{number}/comments?per_page=100"),
        "review_comments": api(f"{base}/pulls/{number}/comments?per_page=100"),
        "commits": api(f"{base}/pulls/{number}/commits?per_page=100"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--tracked", action="store_true")
    parser.add_argument("--pr", type=int)
    args = parser.parse_args(argv)
    found: list[Finding] = []
    if args.tracked:
        found += scan_tracked()
    if args.pr:
        found += scan_pull_request(fetch_pull_request(args.pr))
    for name in args.files:
        text = sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8")
        found += scan_text(text, "stdin" if name == "-" else name)
    if not (args.tracked or args.pr or args.files):
        parser.error("Give files, --tracked or --pr.")
    for finding in found:
        print(finding)
    if found:
        print(f"{len(found)} privacy finding(s). This repository is public: keep local details out of it.",
              file=sys.stderr)
        return 1
    print("No local or private details found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
