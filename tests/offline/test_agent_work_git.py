import os
import shutil
import subprocess

import pytest

from research_multiplier.workflows.agent_work_git import read_code_inputs


pytestmark = [
    pytest.mark.offline,
    pytest.mark.skipif(shutil.which("git") is None, reason="Git is needed for the repository fixture."),
]


@pytest.fixture
def repository(tmp_path):
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
            cwd=tmp_path,
            text=True,
            env=environment,
        ).strip()

    git("init", "--quiet", "--template=")
    (tmp_path / "selected.txt").write_text("Pinned", encoding="utf-8")
    (tmp_path / "other.txt").write_text("Unselected", encoding="utf-8")
    (tmp_path / "binary").write_bytes(b"\0")
    git("add", "selected.txt", "other.txt", "binary")
    # Use a synthetic index entry so the test also works on hosts without symlink privileges.
    blob = git("hash-object", "selected.txt")
    git("update-index", "--add", "--cacheinfo", f"120000,{blob},linked")
    git("commit", "--quiet", "--no-gpg-sign", "-m", "Fixture")
    revision = git("rev-parse", "HEAD")
    (tmp_path / "selected.txt").write_text("Uncommitted replacement", encoding="utf-8")
    return tmp_path, revision


def test_code_input_is_limited_to_selected_files_and_pinned_version(repository):
    path, revision = repository
    assert read_code_inputs(path, revision, ["selected.txt"], byte_limit=6) == {
        "revision": revision, "files": {"selected.txt": "Pinned"},
    }
    with pytest.raises(ValueError, match="exact Git commit"):
        read_code_inputs(path, "HEAD", ["selected.txt"], byte_limit=6)
    with pytest.raises(ValueError, match="input size"):
        read_code_inputs(path, revision, ["selected.txt", "other.txt"], byte_limit=8)


def test_code_input_rejects_implicit_parent_checkout(repository):
    path, revision = repository
    folder = path / "nested"
    folder.mkdir()
    with pytest.raises(ValueError, match="top-level Git checkout"):
        read_code_inputs(folder, revision, ["selected.txt"], byte_limit=32)


def test_code_input_rejects_external_git_metadata(repository):
    path, revision = repository
    linked = path / "linked-checkout"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: ../.git\n", encoding="utf-8")
    with pytest.raises(ValueError, match="own Git objects"):
        read_code_inputs(linked, revision, ["selected.txt"], byte_limit=32)
    alternate = path / ".git" / "objects" / "info" / "alternates"
    alternate.write_text("../outside\n", encoding="utf-8")
    with pytest.raises(ValueError, match="own Git objects"):
        read_code_inputs(path, revision, ["selected.txt"], byte_limit=32)


@pytest.mark.parametrize(("name", "error"), [
    ("linked", "regular Git file"),
    ("binary", "UTF-8 text"),
    ("../outside", "repository-relative"),
    (".git/config", "repository-relative"),
    ("*", "absent"),
])
def test_code_input_rejects_unsafe_or_unavailable_sources(repository, name, error):
    path, revision = repository
    with pytest.raises(ValueError, match=error):
        read_code_inputs(path, revision, [name], byte_limit=32)
