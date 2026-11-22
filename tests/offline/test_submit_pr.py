# SPDX-License-Identifier: Apache-2.0
"""Preflight catches stale work and safely reads back a submitted PR."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline
UPSTREAM = "owner/Trellin"
SOURCE = "peer/Trellin"
BRANCH = "fix/example"
BASE = "a" * 40
HEAD = "b" * 40


@pytest.fixture
def submission(monkeypatch, tmp_path):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("submit_pr", scripts / "submit_pr.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = tmp_path / "PR body.md"
    body.write_text("Refs #267\n", encoding="utf-8")
    pull = {"number": 4, "base": {"sha": BASE}, "head": {"sha": HEAD, "ref": BRANCH, "repo": {"full_name": SOURCE}}}
    calls = []
    records = {
        f"repos/{UPSTREAM}/git/ref/heads/main": {"object": {"sha": BASE}},
        f"repos/{SOURCE}/git/ref/heads/{BRANCH}": {"object": {"sha": HEAD}},
        "user": {"login": "peer"},
    }

    def fake_api(path, *args):
        calls.append((path, *args))
        if path == f"repos/{UPSTREAM}/pulls":
            if "POST" in args:
                assert Path(args[-1].split("@", 1)[1]).read_text(encoding="utf-8") == "Refs #267\n"
                return pull
            return [[]]
        return records[path]

    monkeypatch.setattr(module, "api", fake_api)
    monkeypatch.setattr(module, "git", lambda *args: None)
    monkeypatch.setattr(module, "check_version", lambda *args: None)
    monkeypatch.setattr(module, "check_pull_request", lambda *args: 0)
    return module, body, pull, records, calls


def submit(submission):
    module, body, *_ = submission
    return module.submit(UPSTREAM, SOURCE, BRANCH, HEAD, "fix: validate submission", body, "patch")


def test_create_after_exact_preflight(submission, monkeypatch):
    module, body, pull, _, calls = submission
    versions = []
    monkeypatch.setattr(module, "check_version", lambda *args: versions.append(args))

    assert submit(submission) == pull
    assert versions == [(BASE, HEAD, "patch")]
    assert calls[-1][:-1] == (f"repos/{UPSTREAM}/pulls", "--method", "POST", "-f", f"head=peer:{BRANCH}",
                              "-f", "base=main", "-f", "title=fix: validate submission", "-F")
    assert calls[-1][-1].startswith("body=@") and calls[-1][-1] != f"body=@{body}"


def test_a_stale_base_rejects_before_create(submission, monkeypatch):
    module, _, _, records, calls = submission

    def advance(*args):
        records[f"repos/{UPSTREAM}/git/ref/heads/main"]["object"]["sha"] = "c" * 40

    monkeypatch.setattr(module, "check_version", advance)
    with pytest.raises(SystemExit, match="moved during preflight"):
        submit(submission)
    assert not any("POST" in call for call in calls)


def test_existing_exact_pr_is_returned_without_creating_again(submission, monkeypatch):
    module, _, pull, _, calls = submission
    monkeypatch.setattr(module, "existing_pull", lambda *args: pull)
    assert submit(submission) == pull
    assert not any("POST" in call for call in calls)

    pull["head"]["sha"] = "c" * 40
    with pytest.raises(SystemExit, match="different source revision"):
        submit(submission)


def test_uncertain_create_reads_back_once(submission, monkeypatch):
    module, _, pull, _, calls = submission
    api = module.api

    def lost_response(path, *args):
        if "POST" in args:
            calls.append((path, *args))
            raise SystemExit("lost response")
        if path == f"repos/{UPSTREAM}/pulls" and any("POST" in call for call in calls):
            return [[pull]]
        return api(path, *args)

    monkeypatch.setattr(module, "api", lost_response)
    assert submit(submission) == pull
    assert sum("POST" in call for call in calls) == 1


def test_response_from_a_changed_base_reports_the_existing_pr(submission):
    _, _, pull, _, calls = submission
    pull["base"]["sha"] = "c" * 40
    with pytest.raises(SystemExit, match="PR #4 was opened against a newer main"):
        submit(submission)
    assert sum("POST" in call for call in calls) == 1


def test_existing_pr_with_a_stale_base_still_requires_rebase(submission, monkeypatch):
    module, _, pull, _, calls = submission
    pull["base"]["sha"] = "c" * 40
    monkeypatch.setattr(module, "existing_pull", lambda *args: pull)
    with pytest.raises(SystemExit, match="Update the existing PR"):
        submit(submission)
    assert not any("POST" in call for call in calls)


def test_private_text_is_refused_before_mutation(submission, monkeypatch, tmp_path):
    _, body, _, _, calls = submission
    terms = tmp_path / "private terms.txt"
    terms.write_text("sample unreleased fixture phrase\n", encoding="utf-8")
    monkeypatch.setenv("RM_PRIVATE_TERMS_FILE", str(terms))
    body.write_text("Inspect sample unreleased fixture phrase\nRefs #267\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Remove local or private details"):
        submit(submission)
    assert not any("POST" in call for call in calls)


def test_create_sends_the_same_body_that_was_checked(submission, monkeypatch):
    module, body, pull, _, _ = submission

    def check_claim(upstream, candidate):
        assert candidate["body"] == "Refs #267\n"
        body.write_text("This file changed while checking the claim.\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(module, "check_pull_request", check_claim)
    assert submit(submission) == pull
