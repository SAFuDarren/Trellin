"""Claim bookkeeping for parallel agents (scripts/tasks.py): the earliest active claim wins."""

from argparse import Namespace
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
