"""Claim bookkeeping for parallel agents (scripts/tasks.py): the earliest active claim wins."""

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

SPEC = importlib.util.spec_from_file_location("tasks", Path(__file__).resolve().parents[2] / "scripts" / "tasks.py")
tasks = importlib.util.module_from_spec(SPEC)
sys.modules["tasks"] = tasks
SPEC.loader.exec_module(tasks)


def comment(at, body):
    return {"createdAt": at, "body": body}


def claim(at, agent, branch):
    return comment(at, f"<!-- rm-claim agent={agent} branch={branch} -->\nClaimed by **{agent}**.")


def release(at, agent):
    return comment(at, f"<!-- rm-release agent={agent} -->\nReleased.")


def test_simultaneous_claims_have_exactly_one_holder():
    comments = [claim("2026-11-18T10:00:02Z", "codex-b", "feat/14-b"), claim("2026-11-18T10:00:01Z", "claude-a", "feat/14-a")]
    assert tasks.holder(comments).agent == "claude-a"
    assert [entry.agent for entry in tasks.active_claims(comments)] == ["claude-a", "codex-b"]


def test_release_passes_the_task_on_and_reclaiming_moves_to_the_back():
    comments = [
        claim("2026-11-18T10:00:00Z", "claude-a", "feat/a"),
        claim("2026-11-18T10:05:00Z", "codex-b", "feat/b"),
        release("2026-11-18T11:00:00Z", "claude-a"),
    ]
    assert tasks.holder(comments).agent == "codex-b"
    comments.append(claim("2026-11-18T12:00:00Z", "claude-a", "feat/a2"))
    assert tasks.holder(comments).agent == "codex-b"
    assert tasks.active_claims(comments)[-1].branch == "feat/a2"


def test_ordinary_comments_are_not_claims():
    comments = [comment("2026-11-18T10:00:00Z", "I might take this later"), comment("2026-11-18T10:01:00Z", "<!-- rm-claim branch=x -->")]
    assert tasks.holder(comments) is None


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
