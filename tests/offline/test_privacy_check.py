# SPDX-License-Identifier: Apache-2.0
"""Contributors' local information stays out of the public repository and its records (#307)."""

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# Assemble sample local details at run time so this file itself passes the tracked scan.
HOME = "/" + "Users/" + "alice"
LINUX_HOME = "/" + "home/" + "alice"
TEMP = "/private/" + "tmp/trellin-149-native." + "k2tEeRCp"
FOLDERS = "/private/var/" + "folders/x7/abc"
EMAIL = "alice" + "@" + "gmail.com"
NOREPLY = "1+alice@users." + "noreply.github.com"


@pytest.fixture
def privacy(monkeypatch):
    spec = importlib.util.spec_from_file_location("check_privacy", SCRIPTS / "check_privacy.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_privacy", module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("text, kind", [
    (f"Open {HOME}/Documents/project", "home path"),
    (f"cd {LINUX_HOME}/src", "home path"),
    ("C:\\" + "Users\\alice\\repo", "home path"),
    (f"saved at {TEMP}/proof.png", "temporary directory"),
    (f"under {FOLDERS}/T/run", "temporary directory"),
    (f"Contact {EMAIL}", "personal email"),
])
def test_local_details_are_reported_with_a_generic_rewrite(privacy, text, kind):
    found = privacy.scan_text(text, "sample", terms=[])
    assert [finding.kind for finding in found] == [kind]
    assert found[0].line == 1 and found[0].hint


@pytest.mark.parametrize("text", [
    "Open /" + "Users/you/project or ~/Library/Application Support/Trellin",
    "The runner checks out /" + "home/runner/work",
    "A test uses /private/" + "tmp/name and mktemp -d \"${TMPDIR:-/tmp}/rm-smoke.XXXXXX\"",
    f"Signed-off-by: Alice <{NOREPLY}>",
])
def test_placeholders_and_noreply_addresses_stay_allowed(privacy, text):
    assert privacy.scan_text(text, "sample", terms=[]) == []


def test_private_terms_come_from_a_local_file_and_match_case_insensitively(privacy, tmp_path, monkeypatch):
    terms = tmp_path / "private-terms.txt"
    terms.write_text("# my projects\nSecret Lattice Project\n", encoding="utf-8")
    monkeypatch.setenv("RM_PRIVATE_TERMS_FILE", str(terms))
    found = privacy.scan_text("Loaded the secret lattice project into Trellin.", "issue")
    assert [(finding.kind, finding.text) for finding in found] == [("private term", "Secret Lattice Project")]


def test_pull_request_text_commits_and_identities_are_scanned(privacy):
    records = {
        "pull": {"number": 9, "title": "fix(web): tidy", "body": f"Tested on {HOME}/Desktop"},
        "comments": [{"id": 1, "body": f"Screens saved in {TEMP}"}],
        "review_comments": [],
        "commits": [
            {"sha": "a" * 40, "commit": {
                "message": f"fix: tidy\n\nSigned-off-by: Alice <{EMAIL}>",
                "author": {"name": "Alice", "email": EMAIL},
                "committer": {"name": "GitHub", "email": "noreply@github.com"},
            }},
            {"sha": "b" * 40, "commit": {
                "message": f"docs: note\n\nSigned-off-by: Alice <{NOREPLY}>\nCo-Authored-By: Claude <noreply@anthropic.com>",
                "author": {"name": "Alice", "email": NOREPLY},
                "committer": {"name": "Alice", "email": NOREPLY},
            }},
        ],
    }
    found = privacy.scan_pull_request(records)
    assert sorted((finding.where, finding.kind) for finding in found) == [
        ("PR #9", "home path"),
        ("comment 1", "temporary directory"),
        ("commit aaaaaaa", "personal email"),
        ("commit aaaaaaa author", "commit identity"),
        ("commit aaaaaaa sign-off", "commit identity"),
    ]


def test_every_tracked_file_is_free_of_local_details(privacy):
    found = privacy.scan_tracked()
    assert not found, "\n".join(str(finding) for finding in found)
