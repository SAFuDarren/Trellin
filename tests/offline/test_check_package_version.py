# SPDX-License-Identifier: Apache-2.0
"""Exercise the package version preflight with committed candidate branches."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

CHECK = Path(__file__).resolve().parents[2] / "scripts" / "check_package_version.py"
MARKER = Path("tests/compat_validation/PACKAGE_VERSION")


def git(repo, *args):
    return subprocess.run(
        [
            "git", "-c", "user.name=Version Test", "-c", "user.email=version-test@example.test",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.git/no-hooks",
            *args,
        ],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def check(repo, *args):
    return subprocess.run([sys.executable, str(CHECK), *args], cwd=repo, capture_output=True, text=True)


def commit_version(repo, version, *, marker=None):
    (repo / "pyproject.toml").write_text(f'[project]\nname = "example"\nversion = "{version}"\n', encoding="utf-8")
    (repo / MARKER).write_text(f"{marker or version}\n", encoding="ascii")
    git(repo, "add", ".")
    git(repo, "commit", "-m", f"Candidate {version}")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "--initial-branch=main")
    (tmp_path / MARKER).parent.mkdir(parents=True)
    commit_version(tmp_path, "1.0.22")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "main")
    return tmp_path


def test_parallel_candidates_must_rebase_and_advance_the_duplicate_version(repo):
    git(repo, "switch", "-c", "first")
    (repo / "first.txt").write_text("First candidate\n", encoding="utf-8")
    commit_version(repo, "1.0.23")
    checked = check(repo)
    assert checked.returncode == 0, checked.stderr

    git(repo, "switch", "-c", "second", "main")
    (repo / "second.txt").write_text("Second candidate\n", encoding="utf-8")
    commit_version(repo, "1.0.23")
    checked = check(repo)
    assert checked.returncode == 0, checked.stderr

    git(repo, "branch", "-f", "main", "first")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    stale_base = check(repo)
    assert stale_base.returncode != 0
    assert "does not include origin/main" in stale_base.stderr

    git(repo, "rebase", "origin/main")
    duplicate = check(repo)
    assert duplicate.returncode != 0
    assert "Expected patch version 1.0.24 from origin/main, found 1.0.23" in duplicate.stderr

    commit_version(repo, "1.0.24", marker="1.0.23")
    stale_marker = check(repo)
    assert stale_marker.returncode != 0
    assert f"{MARKER.as_posix()} on HEAD must match package version 1.0.24" in stale_marker.stderr

    commit_version(repo, "1.0.24")
    corrected = check(repo)
    assert corrected.returncode == 0, corrected.stderr
    assert "HEAD uses 1.0.24" in corrected.stdout


@pytest.mark.parametrize(("bump", "version"), [("minor", "1.1.0"), ("major", "2.0.0")])
def test_explicit_increment_resets_lower_components(repo, bump, version):
    git(repo, "switch", "-c", "candidate")
    commit_version(repo, version)
    default = check(repo)
    assert default.returncode != 0
    assert "Expected patch version 1.0.23" in default.stderr
    selected = check(repo, "--base", "main", "--candidate", "candidate", "--bump", bump)
    assert selected.returncode == 0, selected.stderr
    assert f"candidate uses {version}, the expected {bump} version from main" in selected.stdout
