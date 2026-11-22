# SPDX-License-Identifier: Apache-2.0
"""The weekly report keeps its scope remote and edits only its own comment."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "branch_report.py"
MARKER = "<!-- trellin-weekly-branch-inventory -->"


def snapshot():
    def branch(name, state, **details):
        return {"branch": name, "head": "a" * 40, "state": state, "reason": state, **details}

    def pull(number, state, merge_commit=None):
        return {"number": number, "state": state, "merge_commit": merge_commit, "final_head": "a" * 40, "base": "main"}

    return {
        "repository": "SAFuDarren/Trellin",
        "observed_at": "2026-11-22T08:40:00+00:00",
        "remote_coverage": {"heads_missing_locally": 0},
        "remote_branches": [
            branch("active", "active"),
            branch("feature/merged", "ready_to_retire", pull_request=pull(8, "merged", "b" * 40)),
            branch("test/unmerged", "ready_to_retire", pull_request=pull(9, "closed")),
            branch("old/<candidate>", "stale"),
            branch("test/baseline", "preserved", review_due=True,
                   preservation={"owner": "maintainer", "review_on": "2026-11-21"}),
        ],
        "local_branches": [branch("private-branch", "stale")],
        "worktrees": [{"path": "/local/private-worktree"}],
    }


def invoke(data, *arguments, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments], input=json.dumps(data), env=env,
        text=True, capture_output=True, check=True,
    ).stdout


def test_report_lists_remote_review_states_with_exact_commit_evidence():
    output = invoke(snapshot())
    assert "5 upstream branches" in output
    assert "Merged PR heads to verify (1)" in output
    assert "PR #8 · merged" in output
    assert "merge " + "b" * 40 in output
    assert "Closed unmerged branches to review (1)" in output
    assert "PR #9 · closed" in output
    assert "old/&lt;candidate&gt;" in output
    assert "maintainer · review 2026-11-21" in output
    assert "private-branch" not in output
    assert "private-worktree" not in output


def test_publishing_reuses_the_bot_comment_and_ignores_other_authors(tmp_path, monkeypatch):
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "requests.jsonl"
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps([{"id": 12, "user": {"login": "someone"}, "body": MARKER + "\nspoof"}]))
    fake_gh = commands / "gh"
    fake_gh.write_text(f"""#!{sys.executable}
import json
import sys
from pathlib import Path

args = sys.argv[1:]
comments = Path({str(comments)!r})
records = json.loads(comments.read_text())
if args[1] == "--paginate":
    print(json.dumps([records]))
    raise SystemExit
with Path({str(log)!r}).open("a") as stream:
    stream.write(json.dumps(args[:4]) + "\\n")
body = args[-1].removeprefix("body=")
if args[2] == "POST":
    records.append({{"id": 27, "user": {{"login": "github-actions[bot]"}}, "body": body}})
else:
    records[1]["body"] = body
comments.write_text(json.dumps(records))
""")
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GITHUB_REPOSITORY", "SAFuDarren/Trellin")
    data = snapshot()
    invoke(data, "--publish")
    invoke(data, "--publish")
    data["observed_at"] = "2026-11-29T08:40:00+00:00"
    invoke(data, "--publish")
    assert [json.loads(line) for line in log.read_text().splitlines()] == [
        ["api", "--method", "POST", "repos/SAFuDarren/Trellin/issues/205/comments"],
        ["api", "--method", "PATCH", "repos/SAFuDarren/Trellin/issues/comments/27"],
    ]
