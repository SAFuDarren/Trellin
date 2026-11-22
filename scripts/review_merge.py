# SPDX-License-Identifier: Apache-2.0
"""Recheck a reviewed PR from the upstream checkout before an owner squash merge."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from check_privacy import fetch_pull_request, scan_pull_request
from tasks import check_pull_request, gh, repository_name

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"[0-9a-f]{40}")
REVIEW_SENSITIVE = {"scripts/tasks.py", "scripts/review_merge.py", "scripts/check_package_version.py",
                    "scripts/check_privacy.py", "scripts/submit_pr.py"}


def api(path: str, *flags: str) -> dict | list:
    return json.loads(gh("api", path, *flags))


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def pull_snapshot(pull: dict, upstream: str) -> tuple[str, bool]:
    if pull["state"] != "open" or pull["draft"]:
        raise SystemExit("Review an open, non-draft pull request.")
    base = pull["base"]
    if base["ref"] != "main" or base["repo"]["full_name"].casefold() != upstream.casefold():
        raise SystemExit(f"The pull request must target {upstream}:main.")
    head = pull["head"]
    source = (head.get("repo") or {}).get("full_name")
    if not source or not SHA.fullmatch(head["sha"]):
        raise SystemExit("The pull request has no verifiable source repository and head commit.")
    owner = upstream.split("/", 1)[0]
    outside = source.casefold() != upstream.casefold() or pull["user"]["login"].casefold() != owner.casefold()
    return head["sha"], outside


def workflow_runs(upstream: str, pull: dict, head: str) -> dict[int, dict]:
    pages = api(f"repos/{upstream}/actions/runs?event=pull_request&head_sha={head}&per_page=100", "--paginate", "--slurp")
    source = pull["head"]["repo"]["full_name"].casefold()
    return {
        run["check_suite_id"]: run
        for page in pages for run in page["workflow_runs"]
        if run["event"] == "pull_request" and run["head_sha"] == head and run["head_branch"] == pull["head"]["ref"]
        and (run.get("head_repository") or {}).get("full_name", "").casefold() == source
    }


def check_succeeded(run: dict) -> bool:
    return run["status"] == "completed" and run["conclusion"] == "success"


def workflow_key(run: dict) -> tuple[int, str]:
    return run["workflow_id"], run["path"]


def current_workflows(workflows: dict[int, dict], pull: dict, base: str) -> dict[tuple[int, str], dict]:
    current = {}
    base_repository = pull["base"]["repo"]["id"]
    head_repository = pull["head"]["repo"]["id"]
    for run in sorted(workflows.values(), key=lambda item: item["run_number"]):
        if any(
            pr["number"] == pull["number"] and pr["base"]["ref"] == "main" and pr["base"]["sha"] == base
            and pr["head"]["sha"] == pull["head"]["sha"] and pr["head"]["ref"] == pull["head"]["ref"]
            and (pr["base"].get("repo") or {}).get("id") == base_repository
            and (pr["head"].get("repo") or {}).get("id") == head_repository
            for pr in run.get("pull_requests", [])
        ):
            current[workflow_key(run)] = run
    return current


def suite_id(check: dict) -> int | None:
    return (check.get("check_suite") or {}).get("id")


def required_checks(upstream: str, head: str, pull: dict | None = None, base: str | None = None) -> int:
    rules = api(f"repos/{upstream}/rules/branches/main")
    expected = {
        (check["context"], check.get("integration_id"))
        for rule in rules
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    }
    if not expected:
        raise SystemExit("No effective required check policy found on main.")
    pages = api(f"repos/{upstream}/commits/{head}/check-runs?filter=latest&per_page=100", "--paginate", "--slurp")
    runs = [run for page in pages for run in page["check_runs"] if run["head_sha"] == head]
    blocked = []
    workflows, current = None, {}
    for context, app_id in expected:
        matches = [run for run in runs
                   if run["name"] == context and (not app_id or (run.get("app") or {}).get("id") == app_id)]
        successes = {suite_id(run) for run in matches if check_succeeded(run)}
        if not successes:
            blocked.append(context)
            continue
        failures = [run for run in matches if not check_succeeded(run)]
        # GitHub supplies PR associations for upstream Actions runs. Fork runs can omit them.
        actions = any((run.get("app") or {}).get("slug") == "github-actions" for run in matches)
        require_association = bool(pull and actions and (
            pull["head"]["repo"]["full_name"].casefold() == upstream.casefold()
            or any(run.get("pull_requests") for run in matches)
        ))
        cancellations = failures and all(run["conclusion"] == "cancelled" for run in failures)
        if app_id and pull and base and (require_association or cancellations):
            if workflows is None:
                workflows = workflow_runs(upstream, pull, head)
                current = current_workflows(workflows, pull, base)
            if require_association:
                relevant = {workflow_key(workflows[suite]) for suite in map(suite_id, matches) if suite in workflows}
                if not relevant or any(key not in current or current[key]["check_suite_id"] not in successes
                                       for key in relevant):
                    blocked.append(context)
                    continue
            # Discard only completed cancellations that can be shown to have been replaced.
            unresolved = []
            for failure in failures:
                prior = workflows.get(suite_id(failure))
                latest = current.get(workflow_key(prior)) if prior else None
                if not (failure["status"] == "completed" and failure["conclusion"] == "cancelled"
                        and latest and latest["run_number"] > prior["run_number"]
                        and latest["check_suite_id"] in successes and check_succeeded(latest)):
                    unresolved.append(failure)
            failures = unresolved
        if failures:
            blocked.append(context)
    if blocked:
        raise SystemExit(f"Required checks without a matching success on {head}: {', '.join(sorted(blocked))}.")
    return len(expected)


def check_version(base: str, head: str, bump: str) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_package_version.py"), "--base", base,
         "--candidate", head, "--bump", bump],
        cwd=ROOT,
    )
    if result.returncode:
        raise SystemExit("Version and base preflight failed. Rebase and check the updated pull request.")


def check_candidate(upstream: str, pr: int, base: str, head: str, bump: str) -> None:
    git("fetch", "--no-tags", "--no-write-fetch-head", f"https://github.com/{upstream}.git",
        "refs/heads/main", f"refs/pull/{pr}/head")
    check_version(base, head, bump)


def privacy_findings(upstream: str, pr: int) -> list:
    """The repository is public: the PR's text, commits and identities must hold no local details (#307)."""
    return scan_pull_request(fetch_pull_request(pr, upstream))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", type=int)
    parser.add_argument("--head", type=str.lower, help="The full commit SHA reviewed by the owner.")
    parser.add_argument("--merge", action="store_true", help="Apply the verified squash merge.")
    parser.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    parser.add_argument("--reviewed-workflow-changes", action="store_true",
                        help="Confirm separate owner review of an outside PR's workflow or merge-check changes.")
    args = parser.parse_args(argv)
    if args.pr <= 0 or (args.head and not SHA.fullmatch(args.head)) or (args.merge and not args.head):
        parser.error("Use a positive PR number and a full --head SHA. --merge requires --head.")

    upstream = repository_name()
    owner = upstream.split("/", 1)[0]
    if api("user")["login"].casefold() != owner.casefold():
        raise SystemExit("The repository owner must run this preflight and merge.")
    os.environ["GH_REPO"] = upstream
    endpoint = f"repos/{upstream}/pulls/{args.pr}"
    base_ref = f"repos/{upstream}/git/ref/heads/main"
    base = api(base_ref)["object"]["sha"]
    if args.merge and (git("rev-parse", "HEAD") != base or git("status", "--porcelain")):
        raise SystemExit("Run the merge from a clean checkout of the current upstream main.")

    pull = api(endpoint)
    head, outside = pull_snapshot(pull, upstream)
    if args.head and head != args.head:
        raise SystemExit("The pull request head has changed since the reviewed SHA.")
    if outside:
        license_review = api(f"repos/{upstream}/issues/24")
        if license_review["state"] != "closed" or license_review.get("state_reason") != "completed":
            raise SystemExit("Confirm and close the license review in issue #24 as completed before merging outside work.")
    check_candidate(upstream, args.pr, base, head, args.bump)
    if outside and not args.reviewed_workflow_changes:
        changed = git("diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", base, head).split("\0")
        if any(path.startswith((".github/workflows/", ".github/actions/")) or path in REVIEW_SENSITIVE
               for path in changed):
            raise SystemExit("Separately review the outside PR's workflow changes before confirming them.")

    findings = privacy_findings(upstream, args.pr)
    if findings:
        print("\n".join(str(finding) for finding in findings))
        raise SystemExit("Remove local or private details from the PR text, comments or commits before merging.")
    count = required_checks(upstream, head, pull, base)
    latest = api(endpoint)
    if pull_snapshot(latest, upstream) != (head, outside) or api(base_ref)["object"]["sha"] != base:
        raise SystemExit("The candidate or main changed during preflight. Review the new state and retry.")
    review = json.loads(gh("pr", "view", str(args.pr), "--json", "reviewDecision"))
    if review["reviewDecision"] == "CHANGES_REQUESTED" or check_pull_request(upstream, latest):
        raise SystemExit("The review or current issue claim must be resolved before merging.")
    print(f"#{args.pr} {head} passed preflight with {count} required checks on main {base}.")
    if args.merge:
        merged = api(f"{endpoint}/merge", "--method", "PUT", "-f", "merge_method=squash", "-f", f"sha={head}")
        if not merged["merged"]:
            raise SystemExit(f"GitHub declined the merge: {merged['message']}")
        print(f"Merged #{args.pr} as {merged['sha']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
