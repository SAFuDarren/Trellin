# SPDX-License-Identifier: Apache-2.0
"""Read live branch heads and worktree status without updating Git refs."""

from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

RECENT = timedelta(days=7)
PRESERVED_FILE = Path("docs/preserved-branches.json")


def git(path, *args, stdin=None, required=True):
    command = ["git", "--no-optional-locks", "-c", "credential.interactive=false", "-C", str(path), *args]
    try:
        result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        if required:
            raise SystemExit(f"Git {args[0]} timed out.")
        return None
    if result.returncode and required:
        # A remote error may contain credentials embedded in a URL.
        raise SystemExit(f"Git {args[0]} failed. Check the repository and remote access.")
    return result.stdout if result.returncode == 0 else None


def preserved_branches(root):
    path = root / PRESERVED_FILE
    if not path.exists():
        return {}
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("Preserved branches must be a list.")
        entries = {}
        for record in records:
            branch, reason, owner, review_on = (record[key] for key in ("branch", "reason", "owner", "review_on"))
            if any(not isinstance(value, str) or not value.strip() for value in (branch, reason, owner, review_on)):
                raise ValueError("Preserved branches need a branch, reason, owner and review date.")
            if branch in entries or date.fromisoformat(review_on).isoformat() != review_on:
                raise ValueError("Use unique branch names and YYYY-MM-DD review dates.")
            entries[branch] = {"reason": reason, "owner": owner, "review_on": review_on}
        return entries
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit(f"Invalid {PRESERVED_FILE}: {error}") from None


def remote_heads(root):
    heads = {}
    unavailable = []
    for remote in git(root, "remote").splitlines():
        result = git(root, "ls-remote", "--heads", remote, required=False)
        if result is None:
            unavailable.append(remote)
            continue
        heads[remote] = {
            ref.removeprefix("refs/heads/"): head
            for head, ref in (line.split("\t", 1) for line in result.splitlines())
        }
    if "origin" not in heads:
        raise SystemExit("Cannot read live origin branch heads.")
    return heads, unavailable


def git_worktrees(root):
    rows = []
    for record in git(root, "worktree", "list", "--porcelain", "-z").split("\0\0"):
        if not record:
            continue
        fields = {}
        for item in record.split("\0"):
            if item:
                key, _, value = item.partition(" ")
                fields[key] = value
        row = {
            "path": fields["worktree"],
            "branch": fields.get("branch", "").removeprefix("refs/heads/") or None,
            "head": fields.get("HEAD"),
            "locked": "locked" in fields,
            "prunable": "prunable" in fields,
        }
        status = None
        if "bare" not in fields and not row["prunable"]:
            status = git(row["path"], "status", "--porcelain=v1", "-z", "--untracked-files=normal",
                         "--ignored=matching", required=False)
        files = status.split("\0") if status is not None else []
        row["dirty"] = any(item and not item.startswith("!!") for item in files) if status is not None else None
        row["ignored_files"] = any(item.startswith("!!") for item in files) if status is not None else None
        rows.append(row)
    return rows


def commit_dates(root, heads):
    candidates = sorted({head for head in heads if head and head.strip("0")})
    if not candidates:
        return {}
    objects = git(root, "cat-file", "--batch-check=%(objectname) %(objecttype)", stdin="\n".join(candidates) + "\n")
    known = [line.split()[0] for line in objects.splitlines() if line.endswith(" commit")]
    if not known:
        return {}
    return dict(line.split("\t", 1) for line in git(
        root, "log", "--no-walk", "--format=%H%x09%cI", "--stdin", stdin="\n".join(known) + "\n",
    ).splitlines())


def pull_index(pulls, repository):
    heads, dependents = defaultdict(list), defaultdict(list)
    for pull in sorted(pulls, key=lambda item: item["number"], reverse=True):
        head, base = pull["head"], pull["base"]
        state = "merged" if pull.get("merged_at") else pull["state"]
        record = {
            "number": pull["number"], "state": state, "final_head": head["sha"],
            "base": base["ref"], "merge_commit": pull.get("merge_commit_sha") if state == "merged" else None,
        }
        if (head.get("repo") or {}).get("full_name", "").lower() == repository.lower():
            heads[head["ref"]].append(record)
        if state == "open":
            dependents[base["ref"]].append(pull["number"])
    return heads, dependents


def branch_state(name, head, stamp, pulls, dependents, claim, preserved, default, now, stale_hours):
    commit = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None
    match = next((pull for pull in pulls if pull["final_head"] == head), None)
    opened = next((pull for pull in pulls if pull["state"] == "open"), None)
    tracked = opened or match or next(iter(pulls), None)
    row = {
        "branch": name, "head": head, "last_commit_at": stamp, "pull_request": tracked,
        "dependent_open_prs": dependents, "claim": claim,
    }

    if name == default:
        return {**row, "state": "preserved", "reason": "default branch"}
    if preserved:
        return {
            **row, "state": "preserved", "preservation": preserved, "reason": preserved["reason"],
            "review_due": date.fromisoformat(preserved["review_on"]) <= now.date(),
        }
    if opened or dependents:
        return {**row, "state": "active", "reason": "open PR" if opened else "base of an open PR"}

    expired = False
    if claim:
        claimed = datetime.fromisoformat(claim["at"].replace("Z", "+00:00"))
        recent_progress = commit is not None and commit > claimed and now - commit <= RECENT
        expired = now - claimed > timedelta(hours=stale_hours) and not recent_progress
        if not expired or commit is None:
            return {**row, "state": "active", "reason": "held claim"}

    if match:
        return {
            **row, "state": "ready_to_retire",
            "reason": "tip matches merged PR head" if match["state"] == "merged" else "tip matches closed, unmerged PR head",
        }
    if commit is not None and (expired or now - commit > RECENT):
        return {**row, "state": "stale", "reason": "stale claim" if expired else "no matching PR, claim or recent commit"}
    return {**row, "state": "active", "reason": "recent unclaimed tip" if commit else "commit date unavailable"}


def inventory(root, repository, default, pulls, claims, now, stale_hours):
    heads, unavailable = remote_heads(root)
    worktrees = git_worktrees(root)
    locals_by_name = dict(line.split("\t", 1) for line in git(
        root, "for-each-ref", "--format=%(refname:strip=2)%09%(objectname)", "refs/heads",
    ).splitlines())
    remote_tips = {head for branches in heads.values() for head in branches.values()}
    stamps = commit_dates(root, [*remote_tips, *locals_by_name.values(), *(row["head"] for row in worktrees)])
    unknown_tips = remote_tips - stamps.keys()
    shallow = git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
    known_coverage = not (unavailable or unknown_tips or shallow)
    exclude = "".join(f"^{head}\n" for head in sorted(remote_tips - unknown_tips))
    preserved = preserved_branches(root)
    pr_heads, pr_bases = pull_index(pulls, repository)
    unpublished = {}

    def local_only(head):
        if not head or head not in stamps:
            return None
        if head not in unpublished:
            count = int(git(root, "rev-list", "--count", head, "--stdin", stdin=exclude))
            unpublished[head] = count if known_coverage or count == 0 else None
        return unpublished[head]

    def classify(name, head):
        return branch_state(
            name, head, stamps.get(head), pr_heads[name], pr_bases[name], claims.get(name),
            preserved.get(name), default, now, stale_hours,
        )

    local_rows = []
    for name, head in sorted(locals_by_name.items()):
        local_rows.append({**classify(name, head), "local_only_commits": local_only(head)})
    remote_rows = [classify(name, head) for name, head in sorted(heads["origin"].items())]
    local_by_name = {row["branch"]: row for row in local_rows}
    for tree in worktrees:
        branch = local_by_name.get(tree["branch"])
        if branch:
            tree.update({"state": branch["state"], "reason": branch["reason"]})
        else:
            tree.update({"state": "preserved", "reason": "detached or unborn checkout; review manually"})
        tree["local_only_commits"] = local_only(tree["head"])

    return {
        "repository": repository, "observed_at": now.isoformat(), "default_branch": default,
        "remote_coverage": {
            "checked": sorted(heads), "unavailable": sorted(unavailable),
            "heads_missing_locally": len(unknown_tips), "shallow": shallow,
        },
        "remote_branches": remote_rows, "local_branches": local_rows, "worktrees": worktrees,
    }


def print_inventory(result):
    print(f"{result['repository']} · {result['observed_at']}")
    coverage = result["remote_coverage"]
    if coverage["unavailable"]:
        print("Unavailable remotes: " + ", ".join(coverage["unavailable"]))
    if coverage["heads_missing_locally"]:
        print(f"Remote heads without local history: {coverage['heads_missing_locally']}")
    if coverage["shallow"]:
        print("Local history is shallow.")
    for section in ("remote_branches", "local_branches", "worktrees"):
        rows = result[section]
        counts = ", ".join(f"{state.replace('_', ' ')}: {count}" for state, count in sorted(
            Counter(row["state"] for row in rows).items()
        ))
        print(f"\n{section.replace('_', ' ').capitalize()} ({len(rows)}) · {counts}")
        for row in rows:
            name = row.get("path", row["branch"])
            pull = row.get("pull_request")
            details = [row["reason"]]
            if row["head"]:
                details.append(f"tip {row['head']}")
            if pull:
                details.append(f"PR #{pull['number']} {pull['state']}; head {pull['final_head']}")
                if pull["merge_commit"]:
                    details.append(f"merge {pull['merge_commit']}")
            if "local_only_commits" in row:
                count = row["local_only_commits"]
                details.append("remote reachability unknown" if count is None else f"local-only commits: {count}")
            if row.get("review_due"):
                details.append(f"review due {row['preservation']['review_on']} ({row['preservation']['owner']})")
            if "dirty" in row and row["dirty"] is None:
                details.append("worktree status unavailable")
            details.extend(key.replace("_", " ") for key in ("dirty", "ignored_files", "locked", "prunable")
                           if row.get(key))
            print(f"{row['state'].replace('_', ' '):16} {name}  " + " · ".join(details))
