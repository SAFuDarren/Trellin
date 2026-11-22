# SPDX-License-Identifier: Apache-2.0
"""Claim, release and list issues so parallel agents never work on the same task.

A claim is an owner-authored comment binding an agent to a repository, branch and contributor.
The owner confirms a fork contributor's request with --repository and --contributor.
New claims and releases name the ledger revision they reviewed. The status label is reconciled
against the history after each decision, including on a retry.

    python scripts/tasks.py board
    python scripts/tasks.py audit-fit [--json]
    python scripts/tasks.py branches [--json]
    python scripts/tasks.py ready 14
    python scripts/tasks.py claim 14 --agent codex-web --branch feat/14-rail-sections \
      --runner Codex --model gpt-6.2-sol --effort medium --environment "Chromium and Playwright"
    python scripts/tasks.py release 14 --agent claude-web --reason "handed back"
    python scripts/tasks.py finish 14 --agent codex-web --pr 19
    python scripts/tasks.py check-pr 19          # used by CI

Requires the GitHub CLI (`gh`) signed in to an account with access to the repository.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

LABEL = "status:in-progress"
READY_LABEL = "status:ready"
TRIAGE_LABEL = "status:triage"
BLOCKING_LABELS = {"needs-decision"}
PRIORITIES = {f"priority:p{number}": number for number in range(4)}
DIFFICULTIES = {f"difficulty:d{number}": f"D{number}" for number in range(1, 5)}
REVIEW_LABEL = "status:in-review"
STALE_HOURS = 48
AGENT = re.compile(r"^[a-z0-9][a-z0-9._-]{1,40}$")
REPOSITORY = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9._-]+")
LOGIN = re.compile(r"[A-Za-z0-9_-]+")
MARKER = re.compile(r"<!--\s*rm-(claim|release|finish)\s+([^>]*?)\s*-->")
FIELD = re.compile(r"(\w+)=(\S+)")
LINK = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\s+#(\d+)\b", re.IGNORECASE)
FIT_EXAMPLE = "https://github.com/SAFuDarren/Trellin/issues/153"
MERGE_SHA = re.compile(r"[0-9a-f]{40}")
PROFILE_FIELDS = ("runner", "model", "effort", "fallback", "environment", "fit_exception")


@dataclass(frozen=True)
class Claim:
    agent: str
    branch: str
    at: str
    repository: str = ""
    contributor: str = ""
    event: str = ""
    packet: str = ""
    profile: str = ""

    @property
    def identity(self) -> tuple[str, str, str, str]:
        return self.agent, self.branch, self.repository.casefold(), self.contributor.casefold()

    def marker(self, action: str, *, after: str = "", event: str = "", pr: int = 0, merged: str = "") -> str:
        fields = f"agent={self.agent} branch={self.branch}"
        if self.repository:
            fields += f" repository={self.repository} contributor={self.contributor}"
        if event:
            fields += f" after={after} event={event}"
        if action == "claim" and self.packet and self.profile:
            fields += f" packet={self.packet} profile={self.profile}"
        if pr:
            fields += f" pr={pr} merged={merged}"
        return f"<!-- rm-{action} {fields} -->"

    def worker(self, owner: str) -> tuple[str, str]:
        return (self.contributor or owner).casefold(), self.agent


@dataclass
class ClaimHistory:
    claims: list[Claim]
    revision: str
    released: Claim | None
    receipts: set[tuple[str, str, str]]

    @property
    def holder(self) -> Claim | None:
        return self.claims[0] if self.claims else None


def _fields(text: str) -> dict[str, str]:
    return dict(FIELD.findall(text))


def comment_number(comment: dict) -> int:
    match = re.search(r"#issuecomment-(\d+)$", comment.get("url") or "")
    return int(match[1]) if match else 0


def claim_history(comments: list[dict], owner: str) -> ClaimHistory:
    """Replay owner decisions. Stale modern events can never become a subsequent claim."""
    claims: dict[tuple[str, str, str, str], Claim] = {}
    revision = "0"
    released = None
    receipts: set[tuple[str, str, str]] = set()
    seen: set[str] = set()

    for comment in sorted(comments, key=lambda item: (item.get("createdAt", ""), comment_number(item))):
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
        event = fields.get("event", "")
        record = Claim(agent, branch, comment.get("createdAt", ""), repository, contributor, event,
                       fields.get("packet", ""), fields.get("profile", ""))
        if event or "after" in fields:
            if (not re.fullmatch(r"[0-9a-f]{32}", event) or event in seen or fields.get("after") != revision
                    or not branch or (kind == "claim" and not repository)):
                continue
            seen.add(event)
            pr, merged = fields.get("pr", ""), fields.get("merged", "")
            if bool(pr) != bool(merged) or (pr and (not pr.isdecimal() or not MERGE_SHA.fullmatch(merged))):
                continue
            if kind == "claim" and not claims and not pr:
                claims[record.identity] = record
            elif kind == "release" and record.identity in claims:
                released = claims.pop(record.identity)
            elif kind != "finish" or not pr or not released or released.identity != record.identity:
                continue
            if pr:
                receipts.add((agent, pr, merged))
            revision = event
            continue

        # The original history remains readable, including owner-confirmed legacy queues.
        # Legacy releases cannot affect claims recorded with the new protocol.
        prior = claims.copy()
        if kind == "claim":
            if seen:
                continue
            if branch and repository:
                claims.setdefault(record.identity, record)
            elif branch:
                claims = {key: claim for key, claim in claims.items() if claim.agent != agent or claim.repository}
                claims[record.identity] = record
        elif kind == "release" and branch and record.identity in claims and not claims[record.identity].event:
            claims.pop(record.identity, None)
        elif kind == "release" and not branch:
            # Historical releases used the local agent label alone.
            claims = {key: claim for key, claim in claims.items() if claim.agent != agent or claim.repository}
        if claims != prior:
            removed = set(prior) - set(claims)
            if kind == "release" and removed:
                released = next(prior[key] for key in prior if key in removed)
            source = str(comment_number(comment)) if comment_number(comment) else f"{record.at}\n{first_line}"
            revision = hashlib.sha256(source.encode()).hexdigest()[:32]
    return ClaimHistory(sorted(claims.values(), key=lambda claim: claim.at), revision, released, receipts)


def active_claims(comments: list[dict], owner: str) -> list[Claim]:
    """Owner-confirmed claims not yet released, oldest first."""
    return claim_history(comments, owner).claims


def holder(comments: list[dict], owner: str) -> Claim | None:
    return claim_history(comments, owner).holder


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
    data = json.loads(gh("issue", "view", str(number), "--json", "number,title,state,body,labels,comments"))
    # The CLI's nested GraphQL comment connection can reach its page limit.
    if len(data["comments"]) >= 100:
        data["comments"] = issue_comments(repository_name(), number)
    return data


def api_records(path: str) -> list[dict]:
    pages = json.loads(gh("api", "--method", "GET", "--paginate", "--slurp", path))
    return [record for page in pages for record in page]


def issue_comments(upstream: str, number: int) -> list[dict]:
    return [{"createdAt": comment["created_at"], "body": comment["body"], "author": comment.get("user"),
             "url": comment.get("html_url")}
            for comment in api_records(f"repos/{upstream}/issues/{number}/comments?per_page=100")]


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


@contextmanager
def owner_lock(upstream: str, owner: str):
    """Serialize the owner's local commands across worktrees and processes."""
    suffix = hashlib.sha256(upstream.casefold().encode()).hexdigest()[:16]
    runtime = Path(tempfile.gettempdir()) / f"trellin-claims-{owner.casefold()}-{suffix}"
    runtime.mkdir(mode=0o700, exist_ok=True)
    with (runtime / "owner.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def post_comment(number: int, body: str) -> None:
    with tempfile.TemporaryDirectory(prefix="trellin-claim-") as folder:
        message = Path(folder) / "comment.md"
        message.write_text(body + "\n", encoding="utf-8")
        gh("issue", "comment", str(number), "--body-file", str(message))


def claim_revision(number: int, owner: str) -> tuple[dict, ClaimHistory]:
    data = issue(number)
    return data, claim_history(data["comments"], owner)


def reconcile_label(number: int, owner: str, *, confirming: str = "", finishing: bool = False) -> None:
    for attempt in range(4):
        data, history = claim_revision(number, owner)
        labels = label_names(data)
        updates = []
        if bool(history.holder) != (LABEL in labels):
            updates += ["--add-label" if history.holder else "--remove-label", LABEL]
        if finishing and not history.holder and REVIEW_LABEL in labels:
            updates += ["--remove-label", REVIEW_LABEL]
        if not updates:
            return
        if attempt == 3:
            break
        if history.holder and history.holder.event:
            if confirming != history.holder.event:
                return  # Only its own validated confirmation may activate an incomplete claim.
            checked_claim_fit(data, history.holder, number)
            conflict = worker_conflict(number, history.holder, owner)
            if conflict is not None:
                release_pending_claim(number, history, history.holder, conflict)
        gh("issue", "edit", str(number), *updates)
    raise SystemExit(f"#{number} changed during label reconciliation; retry the same command.")


def issue_fit(data: dict) -> tuple[str, bool]:
    from model_routing import validate_issue_fit

    return validate_issue_fit(board_difficulty(label_names(data)), data.get("body") or "")


def confirmable(data: dict, number: int) -> None:
    if data["state"] != "OPEN":
        raise SystemExit(f"#{number} is closed.")
    blocked = label_names(data) & BLOCKING_LABELS
    if blocked:
        raise SystemExit(f"#{number} is labelled {', '.join(sorted(blocked))}; the maintainer must decide first.")


def checked_fit(data: dict, number: int) -> tuple[str, bool]:
    try:
        return issue_fit(data)
    except ValueError as error:
        raise SystemExit(f"#{number} is missing valid Agent fit: {error}. Example: {FIT_EXAMPLE}") from None


def ready_fit(data: dict) -> tuple[str, bool]:
    labels = label_names(data)
    if READY_LABEL not in labels or TRIAGE_LABEL in labels:
        raise ValueError("the issue is not Ready")
    return issue_fit(data)


def issue_packet(data: dict) -> str:
    snapshot = [data.get("body") or "", sorted(label_names(data) & DIFFICULTIES.keys())]
    return hashlib.sha256(json.dumps(snapshot).encode()).hexdigest()


def claim_profile(args: argparse.Namespace) -> str:
    fields = {name: getattr(args, name).strip() for name in PROFILE_FIELDS}
    profile = {**fields, "human": args.human, "scope": args.scope}
    return base64.urlsafe_b64encode(json.dumps(profile, sort_keys=True).encode()).decode()


def verify_claim_fit(data: dict, claim: Claim) -> None:
    level, human = ready_fit(data)
    if not claim.packet or claim.packet != issue_packet(data):
        raise ValueError("the reviewed issue scope changed; release it and reconfirm the claim")
    try:
        profile = json.loads(base64.b64decode(claim.profile, altchars=b"-_", validate=True))
        if (not isinstance(profile, dict) or set(profile) != {*PROFILE_FIELDS, "human", "scope"}
                or any(not isinstance(profile[name], str) for name in PROFILE_FIELDS)
                or not isinstance(profile["human"], bool) or not isinstance(profile["scope"], list)
                or not profile["scope"] or not all(isinstance(path, str) for path in profile["scope"])):
            raise ValueError("invalid profile structure")
    except (TypeError, ValueError) as error:
        raise ValueError("the claim lacks valid actual-fit evidence; release it and reconfirm") from error
    try:
        configuration_text(argparse.Namespace(**profile), level, human)
    except SystemExit as error:
        raise ValueError(f"recorded actual-fit evidence is no longer valid: {error} Release it and reconfirm.") from None


def checked_claim_fit(data: dict, claim: Claim, number: int) -> None:
    try:
        verify_claim_fit(data, claim)
    except ValueError as error:
        raise SystemExit(f"#{number} cannot confirm the existing claim: {error}.") from None


def configuration_text(args: argparse.Namespace, level: str, human_only: bool) -> str:
    from model_routing import model_configuration

    environment = args.environment.strip()
    exception = args.fit_exception.strip()
    if not environment:
        raise SystemExit("Record the available verification tools and environment with --environment.")
    supplied = (args.runner.strip(), args.model.strip(), args.effort.strip())
    if args.human:
        if any(supplied) or args.fallback:
            raise SystemExit("Choose --human or an Agent configuration, not both.")
        if not human_only and not exception:
            raise SystemExit("Use --fit-exception with the owner's reason for a human path on this issue.")
        description = "Human acceptance"
    else:
        if human_only:
            raise SystemExit("This issue requires human acceptance. Confirm with --human and --environment.")
        if not all(supplied):
            raise SystemExit("Supply --runner, --model and --effort for the actual configuration.")
        registry, allowed = model_configuration(level, supplied, args.fallback)
        if allowed is None and not exception:
            raise SystemExit(f"Configuration is not scored at the {level} floor. Run scripts/model_routing.py {level}, "
                             "or document an owner exception with --fit-exception.")
        description = " / ".join(supplied) + (f" ({args.fallback})" if args.fallback else "")
        if allowed is not None:
            description += f" · {allowed['score']} on {registry['benchmark']} ({registry['checked_on']})"
    if exception:
        description += f"\nOwner fit exception: {exception}"
    return f"Agent fit ({level}): {description}\nVerification environment: {environment}"


def open_issues(fields: str) -> list[dict]:
    limit = 1000
    issues = json.loads(gh("issue", "list", "--state", "open", "--limit", str(limit), "--json", f"number,{fields}"))
    if len(issues) >= limit:
        raise SystemExit("The open issue listing is incomplete; cannot verify the full task state.")
    return issues


def full_comments(item: dict) -> list[dict]:
    comments = item["comments"]
    return comments if len(comments) < 100 else issue(item["number"])["comments"]


def worker_conflict(number: int, requested: Claim, owner: str) -> int | None:
    for item in open_issues("comments,labels"):
        if item["number"] == number:
            continue
        current = holder(full_comments(item), owner)
        if (current and requested.worker(owner) == current.worker(owner)
                and (not current.event or LABEL in label_names(item))):
            return item["number"]
    return None


def verify_activation(number: int, owner: str, expected: Claim, *, new_claim: bool) -> None:
    data, history = claim_revision(number, owner)
    if history.holder != expected or LABEL not in label_names(data):
        raise SystemExit(f"#{number} changed while syncing its status; review the current holder.")
    conflict = worker_conflict(number, expected, owner)
    if conflict is None:
        return
    # Another host may have confirmed a different issue between our last scan and label write.
    # Only withdraw the exact event created by this invocation, never an older confirmed claim.
    if new_claim:
        _, history = claim_revision(number, owner)
        if history.holder == expected:
            release_record(number, history, expected, reason=f"worker already holds #{conflict}")
            if claim_revision(number, owner)[1].holder != expected:
                reconcile_label(number, owner)
                raise SystemExit(f"#{number} new claim withdrawn after confirming the worker holds #{conflict}.")
    raise SystemExit(f"{expected.agent} holds both #{number} and #{conflict}; review the claims before continuing.")


def cmd_audit_fit(args: argparse.Namespace) -> int:
    findings = []
    for item in open_issues("body,labels"):
        if not label_names(item) & {READY_LABEL, LABEL, REVIEW_LABEL}:
            continue
        try:
            issue_fit(item)
        except ValueError as error:
            findings.append({"issue": item["number"], "problem": str(error)})
    if args.json:
        print(json.dumps(findings, indent=2))
    elif findings:
        for finding in findings:
            print(f"#{finding['issue']}: {finding['problem']}.")
    else:
        print("All Ready and active issues have valid Agent fit.")
    return int(bool(findings))


def cmd_ready(args: argparse.Namespace) -> int:
    upstream = repository_name()
    owner = upstream.split("/", 1)[0]
    require_owner(owner)
    with owner_lock(upstream, owner):
        data, history = claim_revision(args.issue, owner)
        confirmable(data, args.issue)
        level, _ = checked_fit(data, args.issue)
        labels = label_names(data)
        if history.holder or LABEL in labels or REVIEW_LABEL in labels:
            raise SystemExit(f"#{args.issue} already has ongoing work; reconcile its claim before marking Ready.")
        options = [] if READY_LABEL in labels else ["--add-label", READY_LABEL]
        if TRIAGE_LABEL in labels:
            options.extend(["--remove-label", TRIAGE_LABEL])
        if options:
            gh("issue", "edit", str(args.issue), *options)
            data = issue(args.issue)
            confirmable(data, args.issue)
            checked_fit(data, args.issue)
            if READY_LABEL not in label_names(data) or TRIAGE_LABEL in label_names(data):
                raise SystemExit(f"#{args.issue} changed while marking Ready; retry after checking the issue.")
        print(f"#{args.issue} is Ready ({level}).")
    return 0


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
    with owner_lock(upstream, owner):
        return confirm_claim(args, upstream, owner)


def confirm_claim(args: argparse.Namespace, upstream: str, owner: str) -> int:
    number = args.issue
    data, history = claim_revision(number, owner)
    confirmable(data, number)
    requested = Claim(args.agent, args.branch, "", args.repository or upstream, args.contributor or owner)
    current = history.holder
    if current:
        identity = current.agent, current.branch, (current.repository or upstream).casefold(), current.worker(owner)[0]
        if identity != requested.identity:
            raise SystemExit(f"#{number} is held by {current.agent} on {current.branch} since {current.at}.")
        conflict = worker_conflict(number, current, owner) if current.event else None
        if conflict is not None:
            if LABEL not in label_names(data):
                release_pending_claim(number, history, current, conflict)
            raise SystemExit(f"{current.agent} also holds #{conflict}; release one of the two claims before continuing.")
        if current.event:
            checked_claim_fit(data, current, number)
            if LABEL not in label_names(data) and claim_profile(args) != current.profile:
                raise SystemExit(f"#{number} needs the original scope and configuration to retry; "
                                 "release it to make a different claim.")
        reconcile_label(number, owner, confirming=current.event)
        if current.event:
            verify_activation(number, owner, current, new_claim=False)
        print(f"#{number} is already claimed for {args.agent} on {args.branch}.")
        return 0
    if LABEL in label_names(data) and not history.released:
        raise SystemExit(f"#{number} carries {LABEL} without a recorded claim; ask the maintainer before taking it.")
    labels = label_names(data)
    if READY_LABEL not in labels or TRIAGE_LABEL in labels or REVIEW_LABEL in labels:
        raise SystemExit(f"#{number} is not Ready. Review the issue and run scripts/tasks.py ready {number}.")
    level, human = checked_fit(data, number)
    fit = configuration_text(args, level, human)
    if not args.scope:
        raise SystemExit("Record expected files or directories with --scope.")
    conflict = worker_conflict(number, requested, owner)
    if conflict is not None:
        raise SystemExit(f"{requested.agent} already holds #{conflict}; release it before confirming another issue.")
    if LABEL in labels:  # A previous release wrote its event before the label update was interrupted.
        reconcile_label(number, owner)
        latest, recovered = claim_revision(number, owner)
        if recovered.revision != history.revision or recovered.holder or LABEL in label_names(latest):
            raise SystemExit(f"#{number} changed during label recovery; retry confirmation.")
    event = uuid4().hex
    requested = replace(requested, packet=issue_packet(data), profile=claim_profile(args))
    marker = requested.marker("claim", after=history.revision, event=event)
    paths = ", ".join(f"`{path}`" for path in args.scope)
    post_comment(number, f"{marker}\nConfirmed **{args.agent}** for @{requested.contributor} on "
                         f"`{requested.repository}:{args.branch}`.\n\nExpected scope: {paths}\n{fit}")
    latest, history = claim_revision(number, owner)
    winner = history.holder
    if not winner or winner.event != event:
        reconcile_label(number, owner)
        raise SystemExit(f"#{number} claim state changed during confirmation; review the current holder before retrying.")
    conflict = worker_conflict(number, winner, owner)
    latest_labels = label_names(latest)
    scope_changed = (latest["state"] != "OPEN" or latest.get("body") != data.get("body")
                     or bool(latest_labels & (BLOCKING_LABELS | {TRIAGE_LABEL, REVIEW_LABEL}))
                     or (latest_labels & ({READY_LABEL} | DIFFICULTIES.keys()))
                     != (labels & ({READY_LABEL} | DIFFICULTIES.keys())))
    if conflict is not None or scope_changed:
        reason = (f"worker already holds #{conflict}" if conflict is not None else "issue state changed")
        release_record(number, history, winner, reason=reason)
        reconcile_label(number, owner)
        raise SystemExit(f"#{number} claim withdrawn because {reason}.")
    reconcile_label(number, owner, confirming=event)
    verify_activation(number, owner, winner, new_claim=True)
    print(f"Claimed #{number} for {args.agent} on {args.branch}.")
    return 0


def release_record(number: int, history: ClaimHistory, record: Claim, *, reason: str = "",
                   pr: int = 0, merged: str = "") -> None:
    marker = record.marker("release", after=history.revision, event=uuid4().hex, pr=pr, merged=merged)
    suffix = f": {reason}" if reason else ""
    post_comment(number, f"{marker}\nReleased by **{record.agent}**{suffix}.")


def release_pending_claim(number: int, history: ClaimHistory, record: Claim, conflict: int) -> None:
    release_record(number, history, record, reason=f"worker already holds #{conflict}")
    raise SystemExit(f"#{number} incomplete claim withdrawn because {record.agent} already holds #{conflict}.")


def cmd_release(args: argparse.Namespace) -> int:
    upstream = repository_name()
    owner = upstream.split("/", 1)[0]
    require_owner(owner)
    number = args.issue
    with owner_lock(upstream, owner):
        _, history = claim_revision(number, owner)
        active = [claim for claim in history.claims if claim.agent == args.agent]
        if len(active) > 1:
            raise SystemExit(f"{args.agent} has multiple legacy claims on #{number}; reconcile the exact records first.")
        if active:
            record = active[0]
            release_record(number, history, record, reason=args.reason)
            after = claim_revision(number, owner)[1]
            if record in after.claims:
                raise SystemExit(f"#{number} claim changed during release; retry the same command.")
        elif not history.released or history.released.agent != args.agent:
            raise SystemExit(f"{args.agent} holds no claim on #{number}.")
        reconcile_label(number, owner)
        print(f"Released #{number}.")
    return 0


def cmd_finish(args: argparse.Namespace) -> int:
    upstream = repository_name()
    owner = upstream.split("/", 1)[0]
    require_owner(owner)
    pull = json.loads(gh("api", f"repos/{upstream}/pulls/{args.pr}"))
    base, head = pull["base"], pull["head"]
    merged = pull.get("merge_commit_sha") or ""
    merged_at = pull.get("merged_at") or ""
    created_at = pull.get("created_at") or ""
    if (not pull.get("merged") or not MERGE_SHA.fullmatch(merged) or not merged_at or not created_at
            or base["ref"] != "main"
            or (base.get("repo") or {}).get("full_name", "").casefold() != upstream.casefold()):
        raise SystemExit(f"PR #{args.pr} must be merged to {upstream}:main.")
    if args.issue not in linked_issues(pull.get("body") or ""):
        raise SystemExit(f"PR #{args.pr} does not link issue #{args.issue}.")
    branch = head["ref"]
    source = (head.get("repo") or {}).get("full_name") or ""
    contributor = (pull.get("user") or {}).get("login") or ""
    identity = args.agent, branch, source.casefold(), contributor.casefold()

    def matches(record: Claim | None) -> bool:
        return bool(record and record.at < min(created_at, merged_at)
                    and (record.agent, record.branch, (record.repository or upstream).casefold(),
                         record.worker(owner)[0]) == identity)

    number = args.issue
    receipt = args.agent, str(args.pr), merged
    verified = f"Verified merged PR #{args.pr} at `{merged}`"
    with owner_lock(upstream, owner):
        _, history = claim_revision(number, owner)
        if receipt not in history.receipts:
            current = history.holder
            released = history.released
            if current and matches(current):
                release_record(number, history, current, reason=verified, pr=args.pr, merged=merged)
            elif released and matches(released):
                mark = released.marker("finish", after=history.revision, event=uuid4().hex, pr=args.pr, merged=merged)
                post_comment(number, f"{mark}\n{verified} for the previously released claim.")
            else:
                raise SystemExit(f"PR #{args.pr} source does not match the current or last released claim on #{number}.")
            if receipt not in claim_revision(number, owner)[1].receipts:
                raise SystemExit(f"#{number} changed during finish; retry after checking the issue.")
        reconcile_label(number, owner, finishing=True)
        print(f"{verified}. Finished the recorded claim on #{number}.")
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
        if claim.event and LABEL not in labels:
            return 3, "confirmation interrupted"
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
        claim = holder(full_comments(item), owner)
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

    pulls = api_records(f"repos/{repository}/pulls?state=all&per_page=100")
    issues = open_issues("comments")
    claims = {}
    for item in issues:
        number = item["number"]
        current = holder(full_comments(item), repository.split("/", 1)[0])
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
        elif current.event:
            if LABEL not in label_names(data):
                problems.append(f"#{number} has an incomplete claim; the maintainer must retry confirmation.")
                continue
            try:
                verify_claim_fit(data, current)
            except ValueError as error:
                problems.append(f"#{number} claim is no longer valid: {error}.")
                continue
            conflict = worker_conflict(number, current, owner)
            if conflict is not None:
                problems.append(f"{current.agent} holds both #{number} and #{conflict}; resolve the conflicting claims.")
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
    audit = commands.add_parser("audit-fit", help="Find Ready or active issues with missing or invalid Agent fit.")
    audit.add_argument("--json", action="store_true", help="Print a JSON array of issues needing attention.")
    branches = commands.add_parser("branches", help="Read branch and worktree lifecycle inventory.")
    branches.add_argument("--json", action="store_true", help="Emit machine-readable inventory.")
    claim = commands.add_parser("claim", help="Claim an issue for an agent and branch.")
    claim.add_argument("issue", type=int)
    claim.add_argument("--agent", required=True)
    claim.add_argument("--branch", required=True)
    claim.add_argument("--repository", default="", help="The fork's owner/repo, confirmed by the owner.")
    claim.add_argument("--contributor", default="", help="GitHub login that will open the fork PR.")
    claim.add_argument("--scope", nargs="*", default=[], help="Files or directories you expect to change.")
    claim.add_argument("--runner", default="", help="Actual Agent harness, such as Codex.")
    claim.add_argument("--model", default="", help="Exact model in the dated registry.")
    claim.add_argument("--effort", default="", help="Actual model reasoning effort.")
    claim.add_argument("--fallback", default="", help="Scored fallback mode, if the registry specifies one.")
    claim.add_argument("--human", action="store_true", help="Confirm the issue's human acceptance path.")
    claim.add_argument("--environment", default="", help="Available verification tools and environment.")
    claim.add_argument("--fit-exception", default="", help="Owner's reason for an exception to the model registry.")
    ready = commands.add_parser("ready", help="Validate issue Agent fit and mark it Ready.",
                                description=f"Validate issue Agent fit and mark it Ready. Example: {FIT_EXAMPLE}")
    ready.add_argument("issue", type=int)
    release = commands.add_parser("release", help="Release an issue you hold.")
    release.add_argument("issue", type=int)
    release.add_argument("--agent", required=True)
    release.add_argument("--reason", default="")
    finish = commands.add_parser("finish", help="Record a verified merged PR and reconcile its issue claim.")
    finish.add_argument("issue", type=int)
    finish.add_argument("--agent", required=True)
    finish.add_argument("--pr", type=int, required=True)
    check = commands.add_parser("check-pr", help="Check that a PR links an issue its branch holds.")
    check.add_argument("pr", type=int)
    args = parser.parse_args(argv)
    if args.repo:
        if not REPOSITORY.fullmatch(args.repo):
            parser.error("--repo requires owner/repo")
        os.environ["GH_REPO"] = args.repo
    handlers = {"board": cmd_board, "branches": cmd_branches, "audit-fit": cmd_audit_fit,
                "ready": cmd_ready, "claim": cmd_claim,
                "release": cmd_release, "finish": cmd_finish, "check-pr": cmd_check_pr}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
