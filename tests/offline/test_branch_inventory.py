"""Exercise the read-only branch command with local remotes and offline GitHub responses."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

COMMAND = Path(__file__).resolve().parents[2] / "scripts" / "tasks.py"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def git(path, *args, when=None):
    environment = os.environ.copy()
    if when:
        environment.update(GIT_AUTHOR_DATE=when.isoformat(), GIT_COMMITTER_DATE=when.isoformat())
    return subprocess.check_output(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.org", "-C", str(path), *args],
        env=environment, text=True, stderr=subprocess.PIPE,
    ).strip()


def commit(path, title, days_ago=0):
    git(path, "commit", "--allow-empty", "-m", title, when=NOW - timedelta(days=days_ago))
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    github = tmp_path / "fixture" / "sample.git"
    github.parent.mkdir()
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(github))
    git(root, "init", "--initial-branch=main")
    (root / ".gitignore").write_text("scratch\n")
    git(root, "add", ".gitignore")
    original = commit(root, "common root", 23)
    tips = {}
    for name in ("open", "merged", "closed", "stale", "kept", "stacked", "claimed", "expired", "recent", "fork"):
        git(root, "checkout", "-b", name, original)
        tips[name] = commit(root, name, 10 if name in {"stale", "expired", "fork", "merged"} else 3)
    git(root, "checkout", "main")
    squashed = commit(root, "squash from merged", 2)
    git(root, "remote", "add", "origin", str(github))
    git(root, "push", "origin", "--all")
    git(root, "checkout", "merged")
    moved = commit(root, "extra unsent commit")
    git(root, "checkout", "-b", "local-only", original)
    local = commit(root, "local work", 1)
    git(root, "checkout", "main")
    worktree = tmp_path / "unpublished workspace"
    git(root, "worktree", "add", str(worktree), "local-only")
    (worktree / "local-notes.txt").write_text("Synthetic work")
    (worktree / "scratch").mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "preserved-branches.json").write_text(json.dumps([
        {"branch": "kept", "reason": "Fixture baseline", "owner": "fixture-maintainer",
         "review_on": (NOW.date() - timedelta(days=1)).isoformat()},
    ]))

    def pr(number, name, state, base="main", head_repository="fixture/sample"):
        return {
            "number": number, "state": "open" if state == "open" else "closed",
            "head": {"ref": name, "sha": tips[name], "repo": {"full_name": head_repository}},
            "base": {"ref": base},
            "merged_at": NOW.isoformat() if state == "merged" else None,
            "merge_commit_sha": squashed if state == "merged" else "discard-test-merge",
        }

    def claim(branch, days_ago):
        return {"created_at": (NOW - timedelta(days=days_ago)).isoformat(),
                "body": f"<!-- rm-claim agent=fixture-codex branch={branch} -->"}

    payload = {
        "pulls": [[pr(1, "open", "open"), pr(2, "merged", "merged")],
                  [pr(3, "closed", "closed"), pr(4, "fork", "open", "stacked", "another/fork")]],
        "issues": [[{"number": 8}, {"number": 11, "pull_request": {}}], [{"number": 9}]],
        "issues/8/comments": [[claim("claimed", 0.5)]],
        "issues/9/comments": [[claim("expired", 4)]],
    }
    responses = tmp_path / "responses.json"
    responses.write_text(json.dumps(payload))
    commands = tmp_path / "bin"
    commands.mkdir()
    fake_gh = commands / "gh"
    fake_gh.write_text(f"""#!{sys.executable}
import json
import sys
from pathlib import Path

args = sys.argv[1:]
if args[:2] == ["repo", "view"]:
    print(json.dumps({{"nameWithOwner": "fixture/sample", "defaultBranchRef": {{"name": "main"}}}}))
else:
    assert args[:5] == ["api", "--method", "GET", "--paginate", "--slurp"], args
    endpoint = args[-1].removeprefix("repos/fixture/sample/").split("?")[0]
    print(json.dumps(json.loads(Path({str(responses)!r}).read_text())[endpoint]))
""")
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    return root, github, squashed, tips, moved, local, worktree


def report(root):
    return json.loads(subprocess.check_output([sys.executable, str(COMMAND), "branches", "--json"], cwd=root, text=True))


def refs(path):
    return git(path, "for-each-ref", "--format=%(refname) %(objectname)")


def test_reports_squash_closed_stale_preserved_and_unpublished_without_ref_changes(repository):
    root, github, squashed, tips, moved, local, worktree = repository
    before = refs(root), refs(github), git(root, "worktree", "list", "--porcelain")
    index = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    index_before = index.stat().st_mtime_ns, index.read_bytes()
    with pytest.raises(subprocess.CalledProcessError) as ancestry:
        git(root, "merge-base", "--is-ancestor", tips["merged"], squashed)
    assert ancestry.value.returncode == 1
    result = report(root)
    remote = {row["branch"]: row for row in result["remote_branches"]}
    local_rows = {row["branch"]: row for row in result["local_branches"]}

    assert remote["open"]["state"] == "active"
    assert remote["merged"]["state"] == "ready_to_retire"
    assert remote["merged"]["pull_request"]["final_head"] == tips["merged"]
    assert remote["merged"]["pull_request"]["merge_commit"] == squashed
    assert remote["closed"]["state"] == "ready_to_retire"
    assert remote["closed"]["pull_request"]["state"] == "closed"
    assert remote["closed"]["pull_request"]["merge_commit"] is None
    assert remote["stale"]["state"] == "stale"
    assert remote["kept"]["state"] == "preserved"
    assert remote["kept"]["preservation"]["owner"] == "fixture-maintainer"
    assert remote["kept"]["review_due"]
    assert remote["stacked"]["dependent_open_prs"] == [4]
    assert remote["stacked"]["state"] == "active"
    assert remote["fork"]["state"] == "stale"
    assert remote["claimed"]["claim"]["issue"] == 8
    assert remote["claimed"]["state"] == "active"
    assert remote["expired"]["reason"] == "stale claim"
    assert remote["recent"]["reason"] == "recent unclaimed tip"
    assert remote["main"]["reason"] == "default branch"

    assert local_rows["merged"]["head"] == moved
    assert local_rows["merged"]["state"] != "ready_to_retire"
    assert local_rows["merged"]["local_only_commits"] == 1
    assert local_rows["local-only"]["head"] == local
    assert local_rows["local-only"]["local_only_commits"] == 1
    checkout = next(tree for tree in result["worktrees"] if tree["branch"] == "local-only")
    assert Path(checkout["path"]).resolve() == worktree.resolve()
    assert checkout["dirty"] and checkout["ignored_files"]
    assert checkout["local_only_commits"] == 1
    assert "local-notes.txt" not in json.dumps(result)
    assert before == (refs(root), refs(github), git(root, "worktree", "list", "--porcelain"))
    assert index_before == (index.stat().st_mtime_ns, index.read_bytes())


def test_unknown_remote_head_keeps_unpublished_count_unknown(repository, tmp_path):
    root, upstream, *_ = repository
    peer = tmp_path / "observer"
    git(tmp_path, "clone", str(upstream), str(peer))
    git(peer, "checkout", "-b", "new-remote-branch", "main")
    commit(peer, "remote object outside the local checkout")
    git(peer, "push", "origin", "new-remote-branch")
    result = report(root)

    assert result["remote_coverage"]["heads_missing_locally"] == 1
    local = {row["branch"]: row for row in result["local_branches"]}
    assert local["local-only"]["local_only_commits"] is None
    assert local["main"]["local_only_commits"] == 0
    assert git(root, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/new-remote-branch") == ""
