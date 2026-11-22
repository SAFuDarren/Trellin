# SPDX-License-Identifier: Apache-2.0
"""Preview or retire exact, recoverable tips of merged upstream PRs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from branch_inventory import git, git_worktrees


def eligible_branch(row, default_branch, main_head, occupied, pull_heads, ancestor):
    pr = row["pull_request"]
    return (
        row["state"] == "ready_to_retire"
        and row["branch"] not in occupied
        and not row["claim"]
        and not row["dependent_open_prs"]
        and pr is not None
        and pr["state"] == "merged"
        and pr["base"] == default_branch
        and row["head"] == pr["final_head"] == pull_heads.get(pr["number"])
        and pr["merge_commit"] is not None
        and ancestor(pr["merge_commit"], main_head)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", metavar="BRANCH", nargs="+", help="Retire only these displayed branches.")
    parser.add_argument(
        "--protect-checkout", metavar="PATH", type=Path, action="append", default=[],
        help="Keep branches checked out in another local Trellin clone and its linked worktrees.",
    )
    args = parser.parse_args()
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    inventory = json.loads(subprocess.check_output(
        [sys.executable, str(root / "scripts/tasks.py"), "branches", "--json"], cwd=root, text=True,
    ))
    default_branch = inventory["default_branch"]
    main_ref = f"refs/heads/{default_branch}"
    refs = {
        reference: head
        for head, reference in (
            line.split("\t") for line in git(root, "ls-remote", "origin", main_ref, "refs/pull/*/head").splitlines()
        )
    }
    main_head = refs.get(main_ref)
    if not main_head or git(root, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise SystemExit("The live default branch and full commit history are required.")
    if git(root, "cat-file", "-e", f"{main_head}^{{commit}}", required=False) is None:
        raise SystemExit(f"Fetch origin {default_branch} before checking merge ancestry.")
    pull_heads = {
        int(ref.split("/")[2]): head for ref, head in refs.items() if ref.startswith("refs/pull/")
    }
    occupied = {tree["branch"] for tree in inventory["worktrees"]}
    for checkout in args.protect_checkout:
        target = inventory["repository"].casefold()
        remote = git(checkout, "remote", "get-url", "origin").strip().rstrip("/").removesuffix(".git").casefold()
        if not remote.endswith((f"/{target}", f":{target}")):
            raise SystemExit(f"Protected checkout does not have the same origin: {checkout}")
        occupied.update(tree["branch"] for tree in git_worktrees(checkout))

    def ancestor(commit, head):
        return git(root, "merge-base", "--is-ancestor", commit, head, required=False) is not None

    candidates = {
        row["branch"]: row
        for row in inventory["remote_branches"]
        if eligible_branch(row, default_branch, main_head, occupied, pull_heads, ancestor)
    }
    if not args.apply:
        for branch, row in candidates.items():
            pr = row["pull_request"]
            print(f"{branch}\t{row['head']}\tPR #{pr['number']}\tmerge {pr['merge_commit']}")
        print(f"{len(candidates)} verified candidates. Apply by naming the branches to retire.", file=sys.stderr)
        return
    branches = list(dict.fromkeys(args.apply))
    rejected = [branch for branch in branches if branch not in candidates]
    if rejected:
        raise SystemExit(f"Retirement refused for: {', '.join(rejected)}")
    login = subprocess.check_output(["gh", "api", "user", "--jq", ".login"], text=True).strip()
    if login.casefold() != inventory["repository"].split("/", 1)[0].casefold():
        raise SystemExit("Only the upstream owner can apply branch retirement.")
    leases = [f"--force-with-lease=refs/heads/{branch}:{candidates[branch]['head']}" for branch in branches]
    deletions = [f":refs/heads/{branch}" for branch in branches]
    subprocess.run(["git", "push", "--atomic", *leases, "origin", *deletions], cwd=root, check=True)


if __name__ == "__main__":
    main()
