"""Claim bookkeeping for parallel agents (scripts/tasks.py): the earliest active claim wins."""

from argparse import Namespace
from contextlib import nullcontext
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

SPEC = importlib.util.spec_from_file_location("tasks", Path(__file__).resolve().parents[2] / "scripts" / "tasks.py")
tasks = importlib.util.module_from_spec(SPEC)
sys.modules["tasks"] = tasks
SPEC.loader.exec_module(tasks)
OWNER = "maintainer"
UPSTREAM = f"{OWNER}/trellin"


def comment(at, body, author=OWNER):
    return {"createdAt": at, "body": body, "author": {"login": author}}


def claim(at, agent, branch, *, author=OWNER, repository=None, contributor=None):
    fork = f" repository={repository} contributor={contributor}" if repository and contributor else ""
    return comment(at, f"<!-- rm-claim agent={agent} branch={branch}{fork} -->\nClaimed by **{agent}**.", author)


def release(at, agent, author=OWNER):
    return comment(at, f"<!-- rm-release agent={agent} -->\nReleased.", author)


def test_simultaneous_claims_have_exactly_one_holder():
    comments = [claim("2026-11-18T10:00:02Z", "codex-b", "feat/14-b"), claim("2026-11-18T10:00:01Z", "claude-a", "feat/14-a")]
    assert tasks.holder(comments, OWNER).agent == "claude-a"
    assert [entry.agent for entry in tasks.active_claims(comments, OWNER)] == ["claude-a", "codex-b"]


def test_release_passes_the_task_on_and_reclaiming_moves_to_the_back():
    comments = [
        claim("2026-11-18T10:00:00Z", "claude-a", "feat/a"),
        claim("2026-11-18T10:05:00Z", "codex-b", "feat/b"),
        release("2026-11-18T11:00:00Z", "claude-a"),
    ]
    assert tasks.holder(comments, OWNER).agent == "codex-b"
    comments.append(claim("2026-11-18T12:00:00Z", "claude-a", "feat/a2"))
    assert tasks.holder(comments, OWNER).agent == "codex-b"
    assert tasks.active_claims(comments, OWNER)[-1].branch == "feat/a2"


def test_ordinary_comments_are_not_claims():
    comments = [comment("2026-11-18T10:00:00Z", "I might take this later"), comment("2026-11-18T10:01:00Z", "<!-- rm-claim branch=x -->")]
    assert tasks.holder(comments, OWNER) is None


def test_only_owner_comments_can_confirm_or_release_a_claim():
    comments = [
        claim("2026-11-18T10:00:00Z", "peer-a", "feat/early", author="guest"),
        claim("2026-11-18T10:01:00Z", "trusted-a", "feat/right"),
        release("2026-11-18T10:02:00Z", "trusted-a", author="guest"),
        claim("2026-11-18T10:03:00Z", "trusted-a", "feat/wrong", author="guest"),
        comment("2026-11-18T10:04:00Z", "Report\n<!-- rm-release agent=trusted-a -->"),
    ]
    assert tasks.active_claims(comments, OWNER) == [tasks.Claim("trusted-a", "feat/right", "2026-11-18T10:01:00Z")]


def test_simultaneous_fork_claims_with_same_agent_release_only_the_loser():
    first = tasks.Claim("fork-agent", "feat/fix", "2026-11-18T10:00:01Z", "one/trellin", "one")
    second = tasks.Claim("fork-agent", "feat/fix", "2026-11-18T10:00:02Z", "two/trellin", "two")
    comments = [comment(first.at, first.marker("claim")), comment(second.at, second.marker("claim"))]
    assert tasks.active_claims(comments, OWNER) == [first, second]
    comments.append(comment("2026-11-18T10:00:03Z", second.marker("release")))
    assert tasks.active_claims(comments, OWNER) == [first]


def test_pull_request_links():
    body = "Implements the rail.\n\nCloses #14\nRefs #18, fixes #3. Not #99 alone."
    assert tasks.linked_issues(body) == [3, 14, 18]
    assert tasks.linked_issues("") == []


def test_claim_age():
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    assert tasks.hours_since("2026-11-18T10:00:00Z", now) == pytest.approx(48)


@pytest.mark.parametrize("name, valid", [("claude-web", True), ("codex-backend-1", True), ("Claude", False), ("a", False), ("x y", False)])
def test_agent_names(name, valid):
    assert bool(tasks.AGENT.match(name)) is valid


@pytest.fixture
def check_pr(monkeypatch):
    def run(*, body="Closes #56", branch="codex/fix", labels=(), issues=None, repository=UPSTREAM, contributor=OWNER):
        pull = {
            "body": body,
            "head": {"ref": branch, "repo": {"full_name": repository} if repository else None},
            "user": {"login": contributor},
            "labels": [{"name": name} for name in labels],
        }
        available = issues or {}

        def fake_gh(*args):
            match args[:3]:
                case ("api", path) if path == f"repos/{UPSTREAM}/pulls/58":
                    return json.dumps(pull)
                case ("issue", "view", number) if int(number) in available:
                    return json.dumps(available[int(number)])
                case _:
                    pytest.fail(f"Unexpected GitHub lookup: {args}")

        monkeypatch.setattr(tasks, "gh", fake_gh)
        monkeypatch.setattr(tasks, "repository_name", lambda: UPSTREAM)
        return tasks.cmd_check_pr(Namespace(pr=58))

    return run


def work_issue(*claims, state="OPEN", labels=()):
    return {"state": state, "comments": list(claims), "labels": [{"name": name} for name in labels]}


def test_task_link_rejects_an_unclaimed_work_issue(check_pr, capsys):
    assert check_pr(issues={56: work_issue(labels=(tasks.LABEL,))}) == 1
    assert "#56 has no active claim" in capsys.readouterr().err


def test_task_link_uses_the_effective_claimant(check_pr, capsys):
    first = claim("2026-11-18T10:00:01Z", "codex-a", "codex/first")
    second = claim("2026-11-18T10:00:02Z", "codex-b", "codex/second")
    issues = {56: work_issue(second, first)}
    assert check_pr(branch="codex/second", issues=issues) == 1
    assert "codex/first" in capsys.readouterr().err
    assert check_pr(branch="codex/first", issues=issues) == 0


def test_task_link_binds_fork_head_and_pr_author(check_pr, capsys):
    confirmed = claim(
        "2026-11-18T10:00:01Z", "fork-agent", "codex/fix", repository="guest/Trellin-fork", contributor="guest",
    )
    issues = {56: work_issue(confirmed)}
    assert check_pr(repository="Guest/trellin-fork", contributor="Guest", issues=issues) == 0
    assert check_pr(repository="other/trellin-fork", contributor="guest", issues=issues) == 1
    assert check_pr(repository="guest/Trellin-fork", contributor="other", issues=issues) == 1
    assert check_pr(repository=None, contributor="guest", issues=issues) == 1
    assert "guest/Trellin-fork:codex/fix" in capsys.readouterr().err


def test_task_link_legacy_claim_is_for_owner_upstream_branch(check_pr):
    legacy = claim("2026-11-18T10:00:01Z", "codex-a", "codex/fix")
    issues = {56: work_issue(legacy)}
    assert check_pr(issues=issues) == 0
    assert check_pr(repository="guest/trellin", contributor="guest", issues=issues) == 1
    assert check_pr(contributor="guest", issues=issues) == 1


def test_fork_user_must_request_confirmation_before_cli_writes(monkeypatch):
    def fake_gh(*args):
        if args[:2] == ("api", "user"):
            return "guest\n"
        pytest.fail(f"Unexpected GitHub lookup: {args}")

    monkeypatch.setattr(tasks, "gh", fake_gh)
    monkeypatch.setattr(tasks, "repository_name", lambda: UPSTREAM)
    args = Namespace(issue=56, agent="fork-agent", branch="feat/fork", repository="guest/fork", contributor="guest")
    with pytest.raises(SystemExit, match="Only the repository owner"):
        tasks.cmd_claim(args)
    with pytest.raises(SystemExit, match="Only the repository owner"):
        tasks.cmd_release(args)


def test_task_link_rejects_a_blocked_work_issue_even_with_a_matching_claim(check_pr, capsys):
    current = claim("2026-11-18T10:00:01Z", "codex-a", "codex/fix")
    assert check_pr(issues={56: work_issue(current, labels=("needs-decision",))}) == 1
    assert "needs-decision" in capsys.readouterr().err


def test_task_link_accepts_partial_work_with_historical_and_dependency_references(check_pr):
    current = claim("2026-11-18T10:00:01Z", "codex-a", "codex/fix")
    body = "Refs #56. Previously fixed by #28. Depends on #99. Refs #12 for history."
    assert check_pr(body=body, issues={56: work_issue(current), 12: work_issue(state="CLOSED")}) == 0


def test_task_link_requires_open_work_or_an_explicit_exception(check_pr, capsys):
    assert check_pr(body="Refs #12", issues={12: work_issue(state="CLOSED")}) == 1
    assert "open issue" in capsys.readouterr().err
    assert check_pr(body="Trivial docs fix", labels=("no-issue",)) == 0


@pytest.fixture
def task_board(monkeypatch):
    issues = [
        (1, "2026-11-17", ["priority:p1", "difficulty:d3", "status:ready"]),
        (2, "2026-11-18", ["priority:p1", "status:ready"]),
        (3, "2026-11-19", ["priority:p0", "status:ready"]),
        (4, "2026-11-20", ["priority:p1", "status:in-review", tasks.LABEL]),
        (5, "2026-11-10", ["priority:p2", "status:in-review"]),
        (6, "2026-11-10", ["priority:p1", "needs-decision"]),
        (7, "2026-11-10", ["priority:p1", "status:triage"]),
        (8, "2026-11-10", ["priority:p1", "priority:p2"]),
        (9, "2026-11-10", []),
        (10, "2026-11-16", ["priority:p1", "status:ready"]),
    ]
    rows = [
        {
            "number": number,
            "title": f"Task {number}",
            "createdAt": f"{date}T10:00:00Z",
            "labels": [{"name": label} for label in labels],
            "comments": [claim("2026-11-20T10:00:00Z", "codex-a", "fix/review")] if number == 4 else [],
            "milestone": {"title": "Kernel and plugins"} if number == 4 else None,
        }
        for number, date, labels in reversed(issues)
    ]

    def fake_gh(*args):
        if args[:2] == ("issue", "list"):
            assert "createdAt" in args[-1].split(",")
            return json.dumps(rows)
        assert args[:2] == ("pr", "list")
        return json.dumps([{"number": 20, "headRefName": "fix/review", "body": "Closes #4"}])

    monkeypatch.setattr(tasks, "gh", fake_gh)
    monkeypatch.setattr(tasks, "repository_name", lambda: UPSTREAM)


def test_board_orders_by_urgency_review_and_oldest_ready(task_board, capsys):
    assert tasks.cmd_board(Namespace(mine=None)) == 0
    header, *rows = capsys.readouterr().out.splitlines()
    assert "Priority" in header
    assert "Difficulty" in header
    assert [row.split()[0] for row in rows] == [
        "#3", "#4", "#10", "#1", "#2", "#7", "#6", "#5", "#8", "#9",
    ]
    assert "P1" in rows[1]
    assert "D3" in rows[3]
    assert "Kernel and plugins" in rows[1]
    assert "in review · codex-a on fix/review" in rows[1]
    assert "#20 (fix/review)" in rows[1]
    assert "needs decision" in rows[6]
    assert "invalid" in rows[-2]
    assert "unset" in rows[-1]


def test_difficulty_labels_do_not_depend_on_priority():
    assert tasks.board_difficulty({"priority:p0"}) == "unset"
    assert tasks.board_difficulty({"priority:p2", "difficulty:d4"}) == "D4"
    assert tasks.board_difficulty({"difficulty:d1", "difficulty:d4"}) == "invalid"
    assert tasks.board_difficulty({"difficulty:d9"}) == "invalid"


def test_board_can_filter_for_the_effective_holder(task_board, capsys):
    assert tasks.cmd_board(Namespace(mine="codex-a")) == 0
    _, *rows = capsys.readouterr().out.splitlines()
    assert len(rows) == 1
    assert rows[0].startswith("#4 ")


def event_id(number):
    return f"{number:032x}"


def ledger_event(number, record, kind, after):
    entry = comment("2026-11-23T01:00:00Z", record.marker(kind, after=after, event=event_id(number)))
    entry["url"] = f"https://github.com/{UPSTREAM}/issues/56#issuecomment-{number}"
    return entry


def test_same_second_competing_claim_cannot_promote_on_release():
    first = tasks.Claim("agent-first", "fix/first", "", UPSTREAM, OWNER)
    late = tasks.Claim("agent-late", "fix/late", "", UPSTREAM, OWNER)
    comments = [ledger_event(20, late, "claim", "0"), ledger_event(10, first, "claim", "0")]
    assert tasks.holder(comments, OWNER).identity == first.identity
    comments += [ledger_event(30, first, "release", event_id(10)), ledger_event(40, late, "claim", "0")]
    assert tasks.holder(comments, OWNER) is None
    comments.append(ledger_event(50, late, "claim", event_id(30)))
    assert tasks.holder(comments, OWNER).identity == late.identity
    comments.append(ledger_event(60, first, "release", event_id(10)))
    assert tasks.holder(comments, OWNER).identity == late.identity


AGENT_FIT = """<!-- trellin-agent-fit:start -->
## Task difficulty and Agent fit

- **D2 · 6/15.** Scope 2, uncertainty 1, impact 1, verification 2, dependencies 0. Workbench scope.
- **Recommended configuration.** Codex / GPT-6.2 Sol / medium, **45** and **$0.57 per benchmark task**. Floor **45** in the [Artificial Analysis Coding Agent Index v2.1](https://artificialanalysis.ai/agents/example). [Trellin snapshot](https://example.com/registry) checked 2026-11-22.
- **Required skills and verification.** TypeScript, Chromium and Playwright.
<!-- trellin-agent-fit:end -->"""
HUMAN_FIT = """<!-- trellin-agent-fit:start -->
## Task difficulty and Agent fit

- **D3 · 3/15.** Scope 0, uncertainty 0, impact 0, verification 3, dependencies 0. Native device floor.
- **Human native acceptance.** Use a physical trackpad on the current signed Mac build.
- **Required skills and verification.** Apple Silicon Mac, physical input and a disposable project.
<!-- trellin-agent-fit:end -->"""


@pytest.fixture
def lifecycle(monkeypatch):
    class GitHub:
        def __init__(self):
            self.issues = {
                n: {"number": n, "body": AGENT_FIT, **work_issue(labels=(tasks.READY_LABEL, "difficulty:d2"))}
                for n in (56, 57)
            }
            self.merged = "a" * 40
            self.pull = {
                "body": "Refs #56", "merged": True, "merge_commit_sha": self.merged,
                "created_at": "2026-11-23T01:00:01Z", "merged_at": "2026-11-23T01:00:10Z",
                "base": {"ref": "main", "repo": {"full_name": UPSTREAM}},
                "head": {"ref": "fix/first", "repo": {"full_name": UPSTREAM}}, "user": {"login": OWNER},
            }
            self.fail_label = False
            self.fail_post_response = False
            self.drop_release = False
            self.before_post = None
            self.before_label = None
            self.serial = 100
            self.now = "2026-11-23T01:00:00Z"

        def append_comment(self, item, body):
            self.serial += 1
            entry = comment(self.now, body)
            entry["url"] = f"https://github.com/{UPSTREAM}/issues/{item['number']}#issuecomment-{self.serial}"
            item["comments"].append(entry)

        def gh(self, *args):
            if args[:2] == ("api", "user"):
                return OWNER
            if args[:2] == ("api", f"repos/{UPSTREAM}/pulls/58"):
                return json.dumps(self.pull)
            if args[:2] == ("issue", "list"):
                return json.dumps([item for item in self.issues.values() if item["state"] == "OPEN"])
            item = self.issues[int(args[2])]
            if args[:2] == ("issue", "view"):
                return json.dumps(item)
            if args[:2] == ("issue", "comment"):
                if self.before_post:
                    hook, self.before_post = self.before_post, None
                    hook(item)
                message = Path(args[4]).read_text(encoding="utf-8")
                if self.drop_release and message.startswith("<!-- rm-release "):
                    self.drop_release = False
                    raise SystemExit("simulated release transport failure before write")
                self.append_comment(item, message)
                if self.fail_post_response:
                    self.fail_post_response = False
                    raise SystemExit("simulated lost response after comment write")
                return ""
            assert args[:2] == ("issue", "edit")
            if self.before_label and args[3:5] == ("--add-label", tasks.LABEL):
                hook, self.before_label = self.before_label, None
                hook()
            if self.fail_label:
                self.fail_label = False
                raise SystemExit("simulated transport failure writing status label")
            labels = tasks.label_names(item)
            for action, label in zip(args[3::2], args[4::2], strict=True):
                if action == "--add-label":
                    labels.add(label)
                else:
                    assert action == "--remove-label"
                    labels.discard(label)
            item["labels"] = [{"name": label} for label in sorted(labels)]
            return ""

        def claim(self, number=56, **options):
            args = {
                "issue": number, "agent": "agent-first", "branch": "fix/first", "repository": "", "contributor": "",
                "scope": ["web/src"], "runner": "Codex", "model": "gpt-6.2-sol", "effort": "medium", "fallback": "",
                "human": False, "environment": "Chromium and Playwright", "fit_exception": "",
            }
            return tasks.cmd_claim(Namespace(**(args | options)))

        def holder(self, number=56):
            return tasks.holder(self.issues[number]["comments"], OWNER)

        def release(self, number=56):
            return tasks.cmd_release(Namespace(issue=number, agent="agent-first", reason="handoff"))

        def finish(self, number=56):
            return tasks.cmd_finish(Namespace(issue=number, agent="agent-first", pr=58))

    github = GitHub()
    monkeypatch.setattr(tasks, "gh", github.gh)
    monkeypatch.setattr(tasks, "repository_name", lambda: UPSTREAM)
    monkeypatch.setattr(tasks, "owner_lock", lambda *_: nullcontext())
    monkeypatch.syspath_prepend(str(Path(tasks.__file__).parent))
    return github


def test_ready_and_claim_require_valid_issue_fit_and_worker_configuration(lifecycle):
    issue = lifecycle.issues[56]
    issue["labels"] = [{"name": tasks.TRIAGE_LABEL}, {"name": "difficulty:d2"}]
    issue["body"] = "Reported problem with no implementation assessment."
    with pytest.raises(SystemExit, match="not Ready"):
        lifecycle.claim()
    with pytest.raises(SystemExit, match="Task difficulty and Agent fit"):
        tasks.cmd_ready(Namespace(issue=56))
    issue["body"] = AGENT_FIT.replace("**45**", "**49**")
    with pytest.raises(SystemExit, match="refresh the recommendation"):
        tasks.cmd_ready(Namespace(issue=56))
    issue["body"] = AGENT_FIT
    assert tasks.cmd_ready(Namespace(issue=56)) == 0
    assert tasks.label_names(issue) == {tasks.READY_LABEL, "difficulty:d2"}
    with pytest.raises(SystemExit, match="D2 floor"):
        lifecycle.claim(effort="low")
    with pytest.raises(SystemExit, match="--environment"):
        lifecycle.claim(environment="")
    assert not issue["comments"]
    assert lifecycle.claim(effort="low", fit_exception="Owner approved a small prepared fixture") == 0
    assert "Owner fit exception: Owner approved" in issue["comments"][-1]["body"]
    with pytest.raises(SystemExit, match="already holds #56"):
        lifecycle.claim(57, branch="fix/other")
    assert not lifecycle.issues[57]["comments"]
    assert lifecycle.claim(57, agent="agent-other", branch="fix/other") == 0


def test_human_native_acceptance_cannot_be_confirmed_by_agent_profile(lifecycle):
    issue = lifecycle.issues[56]
    issue["labels"] = [{"name": tasks.READY_LABEL}, {"name": "difficulty:d3"}]
    issue["body"] = HUMAN_FIT
    with pytest.raises(SystemExit, match="requires human acceptance"):
        lifecycle.claim()
    assert lifecycle.claim(human=True, runner="", model="", effort="", environment="Mac with physical trackpad") == 0
    body = issue["comments"][-1]["body"]
    assert "Agent fit (D3): Human acceptance" in body and "physical trackpad" in body


def test_retry_repairs_partial_claim_and_release_without_extra_events(lifecycle):
    issue = lifecycle.issues[56]
    lifecycle.fail_label = True
    with pytest.raises(SystemExit, match="transport failure"):
        lifecycle.claim()
    assert lifecycle.holder().agent == "agent-first"
    assert tasks.LABEL not in tasks.label_names(issue)
    assert lifecycle.claim() == 0
    assert len(issue["comments"]) == 1
    assert tasks.LABEL in tasks.label_names(issue)
    lifecycle.fail_label = True
    with pytest.raises(SystemExit, match="transport failure"):
        lifecycle.release()
    assert lifecycle.holder() is None
    assert tasks.LABEL in tasks.label_names(issue)
    assert lifecycle.release() == 0
    assert len(issue["comments"]) == 2
    assert tasks.LABEL not in tasks.label_names(issue)


def test_losing_claim_is_inactive_even_if_it_was_written(lifecycle):
    first = tasks.Claim("agent-other", "fix/other", "", UPSTREAM, OWNER)
    lifecycle.before_post = lambda issue: lifecycle.append_comment(issue, first.marker(
        "claim", after="0", event=event_id(1),
    ))
    with pytest.raises(SystemExit, match="claim state changed"):
        lifecycle.claim()
    assert lifecycle.holder().identity == first.identity
    assert tasks.cmd_release(Namespace(issue=56, agent=first.agent, reason="handoff")) == 0
    assert lifecycle.holder() is None


def test_finish_requires_merged_upstream_pr_and_matching_claim(lifecycle):
    assert lifecycle.claim() == 0
    issue = lifecycle.issues[56]
    lifecycle.pull["merged"] = False
    with pytest.raises(SystemExit, match="must be merged"):
        lifecycle.finish()
    lifecycle.pull["merged"] = True
    lifecycle.pull["head"]["repo"]["full_name"] = "guest/trellin"
    with pytest.raises(SystemExit, match="does not match"):
        lifecycle.finish()
    lifecycle.pull["head"]["repo"]["full_name"] = UPSTREAM
    lifecycle.pull["body"] = "Refs #57"
    with pytest.raises(SystemExit, match="does not link issue"):
        lifecycle.finish()
    assert len(issue["comments"]) == 1
    assert lifecycle.holder().agent == "agent-first"


def test_finish_records_exact_merge_then_recovers_label_write(lifecycle):
    assert lifecycle.claim() == 0
    issue = lifecycle.issues[56]
    issue["labels"].append({"name": tasks.REVIEW_LABEL})
    lifecycle.fail_label = True
    with pytest.raises(SystemExit, match="transport failure"):
        lifecycle.finish()
    assert lifecycle.holder() is None
    assert tasks.LABEL in tasks.label_names(issue)
    assert lifecycle.finish() == 0
    assert lifecycle.finish() == 0
    assert tasks.LABEL not in tasks.label_names(issue)
    assert tasks.REVIEW_LABEL not in tasks.label_names(issue)
    assert issue["state"] == "OPEN"  # Refs finishes the slice, not the whole issue.
    assert len(issue["comments"]) == 2
    assert lifecycle.merged in issue["comments"][-1]["body"]


def test_finish_last_released_claim_leaves_new_owner_active(lifecycle):
    assert lifecycle.claim() == 0
    assert lifecycle.release() == 0
    assert lifecycle.claim(agent="agent-next", branch="fix/next") == 0
    issue = lifecycle.issues[56]
    issue["labels"].append({"name": tasks.REVIEW_LABEL})
    assert lifecycle.finish() == 0
    assert lifecycle.finish() == 0
    assert lifecycle.holder().agent == "agent-next"
    assert tasks.REVIEW_LABEL in tasks.label_names(issue)
    assert len(lifecycle.issues[56]["comments"]) == 4


def test_finish_does_not_release_same_branch_reclaimed_after_pr_opened(lifecycle):
    assert lifecycle.claim() == 0
    assert lifecycle.release() == 0
    lifecycle.now = "2026-11-23T01:00:02Z"
    assert lifecycle.claim() == 0
    assert lifecycle.finish() == 0
    assert lifecycle.holder().at == "2026-11-23T01:00:02Z"


def test_audit_fit_reports_missing_fields_on_ready_issues(lifecycle, capsys):
    lifecycle.issues[57]["body"] = "Missing fit."
    assert tasks.cmd_audit_fit(Namespace(json=True)) == 1
    assert json.loads(capsys.readouterr().out) == [
        {"issue": 57, "problem": "add one issue-visible Task difficulty and Agent fit section"},
    ]


def test_pr_blocks_conflicting_modern_worker_claims(lifecycle, capsys):
    assert lifecycle.claim() == 0
    duplicate = tasks.Claim("agent-first", "fix/other", "", UPSTREAM, OWNER)
    lifecycle.append_comment(lifecycle.issues[57], duplicate.marker("claim", after="0", event=event_id(900)))
    lifecycle.issues[57]["labels"].append({"name": tasks.LABEL})
    assert tasks.cmd_check_pr(Namespace(pr=58)) == 1
    assert "holds both #56 and #57" in capsys.readouterr().err


def test_lost_comment_response_retries_only_original_ready_packet_and_profile(lifecycle, capsys):
    issue = lifecycle.issues[56]
    lifecycle.fail_post_response = True
    with pytest.raises(SystemExit, match="lost response"):
        lifecycle.claim()
    assert lifecycle.holder()
    assert tasks.LABEL not in tasks.label_names(issue)
    assert tasks.cmd_check_pr(Namespace(pr=58)) == 1
    assert "incomplete claim" in capsys.readouterr().err
    issue["labels"] = [{"name": tasks.TRIAGE_LABEL}, {"name": "difficulty:d2"}]
    issue["body"] = "Re-scoping required."
    with pytest.raises(SystemExit, match="not Ready"):
        lifecycle.claim()
    assert tasks.LABEL not in tasks.label_names(issue)
    issue["labels"][0]["name"] = tasks.READY_LABEL
    issue["body"] = AGENT_FIT + "\nScope changed since this claim."
    with pytest.raises(SystemExit, match="reviewed issue scope changed"):
        lifecycle.claim()
    issue["body"] = AGENT_FIT
    with pytest.raises(SystemExit, match="original scope and configuration"):
        lifecycle.claim(effort="xhigh")
    assert lifecycle.claim() == 0
    assert len(issue["comments"]) == 1
    assert tasks.cmd_check_pr(Namespace(pr=58)) == 0
    issue["body"] = AGENT_FIT + "\nNew scope after confirmation."
    assert tasks.cmd_check_pr(Namespace(pr=58)) == 1
    assert "reviewed issue scope changed" in capsys.readouterr().err


def test_failed_compensation_leaves_loser_incomplete_and_retry_releases_it(lifecycle, capsys):
    def confirm_other_issue(_):
        assert lifecycle.claim(57, branch="fix/other") == 0
        lifecycle.drop_release = True

    lifecycle.before_post = confirm_other_issue
    with pytest.raises(SystemExit, match="release transport failure"):
        lifecycle.claim()
    assert lifecycle.holder(56)
    labels = tasks.label_names(lifecycle.issues[56])
    assert tasks.LABEL not in labels
    assert tasks.board_state(labels, lifecycle.holder(56))[1] == "confirmation interrupted"
    assert tasks.cmd_check_pr(Namespace(pr=58)) == 1
    lifecycle.pull["body"] = "Refs #57"
    lifecycle.pull["head"]["ref"] = "fix/other"
    assert tasks.cmd_check_pr(Namespace(pr=58)) == 0
    with pytest.raises(SystemExit, match="incomplete claim withdrawn"):
        lifecycle.claim()
    assert not lifecycle.holder(56)
    assert lifecycle.holder(57).branch == "fix/other"
    assert tasks.LABEL in tasks.label_names(lifecycle.issues[57])


def test_worker_conflict_at_status_write_withdraws_only_the_new_claim(lifecycle, capsys):
    def confirm_other_issue():
        assert lifecycle.holder(56)
        assert tasks.LABEL not in tasks.label_names(lifecycle.issues[56])
        assert lifecycle.claim(57, branch="fix/other") == 0

    lifecycle.before_label = confirm_other_issue
    with pytest.raises(SystemExit, match="new claim withdrawn.*holds #57"):
        lifecycle.claim()
    assert "Claimed #56" not in capsys.readouterr().out
    assert lifecycle.holder(56) is None
    assert tasks.LABEL not in tasks.label_names(lifecycle.issues[56])
    assert lifecycle.holder(57).branch == "fix/other"
    assert len(lifecycle.issues[57]["comments"]) == 1
    lifecycle.pull["body"] = "Refs #57"
    lifecycle.pull["head"]["ref"] = "fix/other"
    assert tasks.cmd_check_pr(Namespace(pr=58)) == 0
