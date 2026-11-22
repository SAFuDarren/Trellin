# SPDX-License-Identifier: Apache-2.0
"""The owner validates current PR metadata and the exact reviewed submission."""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
UPSTREAM = "owner/Trellin"
BASE = "b" * 40
HEAD = "a" * 40
PR = f"repos/{UPSTREAM}/pulls/4"
MAIN = f"repos/{UPSTREAM}/git/ref/heads/main"


@pytest.fixture
def review(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("review_merge", SCRIPTS / "review_merge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def candidate(review, monkeypatch):
    pull = {
        "state": "open",
        "draft": False,
        "head": {"sha": HEAD, "ref": "fix/demo", "repo": {"full_name": UPSTREAM}},
        "base": {"ref": "main", "repo": {"full_name": UPSTREAM}},
        "user": {"login": "owner"},
    }
    records = {
        "user": {"login": "owner"},
        MAIN: {"object": {"sha": BASE}},
        PR: pull,
        f"{PR}/merge": {"merged": True, "sha": "c" * 40},
        f"repos/{UPSTREAM}/issues/24": {"state": "open"},
    }
    api_calls = []
    git_calls = []

    def api(path, *flags):
        api_calls.append((path, *flags))
        return records[path]

    def git(*args):
        git_calls.append(args)
        return {"rev-parse": BASE, "status": "", "diff": "README.md\0"}[args[0]]

    monkeypatch.setenv("GH_REPO", UPSTREAM)
    monkeypatch.setattr(review, "api", api)
    monkeypatch.setattr(review, "git", git)
    monkeypatch.setattr(review, "repository_name", lambda: UPSTREAM)
    monkeypatch.setattr(review, "check_candidate", lambda *args: None)
    monkeypatch.setattr(review, "required_checks", lambda *args: 12)
    monkeypatch.setattr(review, "privacy_findings", lambda *args: [])
    monkeypatch.setattr(review, "gh", lambda *args: json.dumps({"reviewDecision": ""}))
    monkeypatch.setattr(review, "check_pull_request", lambda *args: 0)
    return pull, records, api_calls, git_calls


def test_read_only_preflight_and_explicit_owner_merge(review, candidate, capsys):
    _, _, api_calls, _ = candidate

    assert review.main(["4"]) == 0
    assert not any("--method" in call for call in api_calls)
    assert "12 required checks" in capsys.readouterr().out

    assert review.main(["4", "--head", HEAD, "--merge"]) == 0
    assert api_calls[-1] == (f"{PR}/merge", "--method", "PUT", "-f", "merge_method=squash", "-f", f"sha={HEAD}")


def test_merge_requires_the_reviewed_head(review, candidate):
    _, _, api_calls, _ = candidate

    with pytest.raises(SystemExit):
        review.main(["4", "--merge"])
    assert not api_calls
    with pytest.raises(SystemExit, match="head has changed"):
        review.main(["4", "--head", "d" * 40, "--merge"])
    assert not any("--method" in call for call in api_calls)


def test_old_green_checks_cannot_merge_released_claim(review, candidate, monkeypatch):
    _, _, api_calls, _ = candidate
    monkeypatch.setattr(review, "check_pull_request", lambda *args: 1)

    with pytest.raises(SystemExit, match="current issue claim"):
        review.main(["4", "--head", HEAD, "--merge"])
    assert not any("--method" in call for call in api_calls)


def test_main_advancing_after_preflight_stops_the_merge(review, candidate, monkeypatch):
    _, records, api_calls, _ = candidate

    def passed_on_previous_base(*args):
        records[MAIN]["object"]["sha"] = "e" * 40
        return 12

    monkeypatch.setattr(review, "required_checks", passed_on_previous_base)
    with pytest.raises(SystemExit, match="main changed"):
        review.main(["4", "--head", HEAD, "--merge"])
    assert not any("--method" in call for call in api_calls)


def test_non_owner_cannot_merge(review, candidate):
    _, records, api_calls, _ = candidate
    records["user"]["login"] = "peer"

    with pytest.raises(SystemExit, match="repository owner"):
        review.main(["4", "--head", HEAD, "--merge"])
    assert api_calls == [("user",)]


def test_outside_merge_waits_for_license_and_separate_workflow_review(review, candidate, monkeypatch):
    pull, records, api_calls, _ = candidate
    pull["head"]["repo"]["full_name"] = "peer/Trellin"
    pull["user"]["login"] = "peer"
    args = ["4", "--head", HEAD, "--merge"]
    license_review = records[f"repos/{UPSTREAM}/issues/24"]
    license_review.update(state="closed", state_reason="not_planned")
    with pytest.raises(SystemExit, match="license review"):
        review.main(args)

    license_review.update(state_reason="completed")
    git = review.git
    monkeypatch.setattr(review, "git", lambda *args: ".github/workflows/tests.yml\0" if args[0] == "diff" else git(*args))
    with pytest.raises(SystemExit, match="workflow changes"):
        review.main(args)
    assert not any("--method" in call for call in api_calls)

    assert review.main([*args, "--reviewed-workflow-changes"]) == 0


@pytest.mark.parametrize("head,app_id,conclusion", [(BASE, 10, "success"), (HEAD, 20, "success"), (HEAD, 10, "failure")])
def test_required_check_needs_matching_commit_and_app(review, monkeypatch, head, app_id, conclusion):
    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [
        {"context": "Check this PR", "integration_id": 10},
    ]}}]
    check = {"head_sha": head, "app": {"id": app_id}, "status": "completed",
             "conclusion": conclusion, "name": "Check this PR"}
    monkeypatch.setattr(review, "api", lambda endpoint, *flags: rules if "/rules/" in endpoint else [{"check_runs": [check]}])

    with pytest.raises(SystemExit, match="Check this PR"):
        review.required_checks(UPSTREAM, HEAD)

    check.update(head_sha=HEAD, conclusion="success", app={"id": 10})
    assert review.required_checks(UPSTREAM, HEAD) == 1


def test_local_details_in_the_pull_request_block_the_merge(review, candidate, monkeypatch, capsys):
    # #307: the repository is public, so a PR that names a contributor's local paths cannot merge.
    monkeypatch.setattr(review, "privacy_findings", lambda *args: ["PR #4:1: home path — write ~/…"])
    with pytest.raises(SystemExit, match="Remove local or private details"):
        review.main(["4", "--head", HEAD, "--merge"])
    assert "home path" in capsys.readouterr().out


@pytest.mark.parametrize("variant", [
    "same-pr", "other-pr", "old-base", "other-workflow", "pending-suite", "failed",
    "wrong-pr-without-duplicate", "pending-without-duplicate", "wrong-repository", "fork-without-association",
])
def test_only_a_newer_verified_suite_can_replace_a_cancelled_check(review, monkeypatch, variant):
    pull = {"number": 4, "base": {"repo": {"id": 100}},
            "head": {"sha": HEAD, "ref": "fix/demo", "repo": {"id": 100, "full_name": UPSTREAM}}}
    subject = {"number": 4, "base": {"ref": "main", "sha": BASE, "repo": {"id": 100}},
               "head": {"ref": "fix/demo", "sha": HEAD, "repo": {"id": 100}}}
    catalog = {"event": "pull_request", "head_sha": HEAD, "head_branch": "fix/demo", "workflow_id": 21,
               "path": ".github/workflows/checks.yml", "head_repository": {"full_name": UPSTREAM}}
    old = dict(catalog, check_suite_id=40, run_number=1, status="completed", conclusion="cancelled", pull_requests=[])
    new = dict(catalog, check_suite_id=50, run_number=2, status="completed", conclusion="success",
               pull_requests=[subject])
    common = {"head_sha": HEAD, "app": {"id": 10, "slug": "github-actions"}, "status": "completed",
              "name": "Release check"}
    cancelled = dict(common, check_suite={"id": 40}, conclusion="cancelled")
    passed = dict(common, check_suite={"id": 50}, conclusion="success")
    workflows = [old, new]
    if variant in {"other-pr", "wrong-pr-without-duplicate"}:
        subject["number"] = 5
    elif variant == "old-base":
        subject["base"]["sha"] = "d" * 40
    elif variant == "other-workflow":
        new["workflow_id"] = 22
    elif variant in {"pending-suite", "pending-without-duplicate"}:
        workflows.append(dict(new, check_suite_id=60, run_number=3, status="in_progress", conclusion=None))
    elif variant == "failed":
        cancelled["conclusion"] = "failure"
    elif variant == "wrong-repository":
        subject["base"]["repo"]["id"] = 200
    elif variant == "fork-without-association":
        pull["head"]["repo"]["full_name"] = "peer/Trellin"
        new["pull_requests"] = []
    checks = [passed] if variant.endswith("without-duplicate") or variant == "fork-without-association" else [cancelled, passed]
    rule = {"type": "required_status_checks", "parameters": {"required_status_checks": [
        {"context": "Release check", "integration_id": 10},
    ]}}
    replies = {
        f"repos/{UPSTREAM}/rules/branches/main": [rule],
        f"repos/{UPSTREAM}/commits/{HEAD}/check-runs?filter=latest&per_page=100": [{"check_runs": checks}],
        f"repos/{UPSTREAM}/actions/runs?event=pull_request&head_sha={HEAD}&per_page=100": [{"workflow_runs": workflows}],
    }
    monkeypatch.setattr(review, "api", lambda endpoint, *flags: replies[endpoint])

    if variant in {"same-pr", "fork-without-association"}:
        assert review.required_checks(UPSTREAM, HEAD, pull, BASE) == 1
    else:
        with pytest.raises(SystemExit, match="Release check"):
            review.required_checks(UPSTREAM, HEAD, pull, BASE)
