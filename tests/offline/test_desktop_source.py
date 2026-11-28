from errno import EACCES
from hashlib import sha256
import os

import pytest

from research_multiplier.storage.managed_workspaces import LibraryError
from research_multiplier.workflows import desktop_source


pytestmark = [pytest.mark.offline, pytest.mark.skipif(os.name != "posix", reason="POSIX directory descriptors.")]


def test_check_references_reports_current_digests_without_changing_files(tmp_path):
    root = tmp_path.resolve()
    directory = root / "稿件"
    directory.mkdir()
    script = directory / "solve.py"
    content = b"result = 7\n"
    digest = sha256(content).hexdigest()
    script.write_bytes(content)
    original_mode = script.stat().st_mode
    (root / "empty.txt").write_bytes(b"")
    (root / "different.txt").write_bytes(content)
    identity = desktop_source.source_identity(root)

    result = desktop_source.check_references(root, [
        {"path": "稿件/solve.py", "sha256": digest},
        {"path": "different.txt", "sha256": "0" * 64},
        {"path": "empty.txt"},
        {"path": "deleted.txt"},
    ], expected_identity=identity)

    assert result == {"status": "available", "files": [
        {"path": "稿件/solve.py", "status": "unchanged", "sha256": digest},
        {"path": "different.txt", "status": "changed", "sha256": digest},
        {"path": "empty.txt", "status": "available", "sha256": sha256(b"").hexdigest()},
        {"path": "deleted.txt", "status": "missing"},
    ]}
    assert set(root.iterdir()) == {directory, root / "empty.txt", root / "different.txt"}
    assert list(directory.iterdir()) == [script]
    assert script.read_bytes() == content
    assert script.stat().st_mode == original_mode


@pytest.mark.parametrize("references", [
    None,
    [],
    [{"path": str(n)} for n in range(17)],
    [{"path": "a"}, {"path": "a"}],
    [{"path": "a"}, {"path": "/absolute"}],
    [{"path": "a/../b"}],
    [{"path": "./a"}],
    [{"path": "a//b"}],
    [{"path": "a/"}],
    [{"path": "a\\b"}],
    [{"path": "a/.git/config"}],
    [{"path": "a\x00b"}],
    [{"path": "a" * 4097}],
    [{"path": "a", "extra": False}],
    [{"path": "a", "sha256": None}],
    [{"path": "a", "sha256": "f" * 63}],
    [{"path": "a", "sha256": "G" * 64}],
])
def test_check_references_validates_entire_request_before_open(tmp_path, monkeypatch, references):
    def unexpected_open(*args, **kwargs):
        pytest.fail("invalid references must not open the filesystem")

    monkeypatch.setattr(desktop_source, "_directory", unexpected_open)
    with pytest.raises(LibraryError, match="storage-invalid"):
        desktop_source.check_references(tmp_path, references, expected_identity=None)


def test_check_references_does_not_follow_links_or_open_special_files(tmp_path):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "note.txt").write_bytes(b"outside source")
    (root / "linked.txt").symlink_to(outside / "note.txt")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    os.mkfifo(root / "named-pipe")
    (root / "ordinary-dir").mkdir()
    (root / "large.txt").touch()
    with (root / "large.txt").open("r+b") as stream:
        stream.truncate(4 * 1024 * 1024 + 1)
    identity = desktop_source.source_identity(root)

    result = desktop_source.check_references(root, [
        {"path": "linked.txt"}, {"path": "linked-dir/note.txt"}, {"path": "named-pipe"},
        {"path": "ordinary-dir"}, {"path": "large.txt"},
    ], expected_identity=identity)

    assert result == {"status": "available", "files": [
        {"path": "linked.txt", "status": "unsupported"},
        {"path": "linked-dir/note.txt", "status": "unsupported"},
        {"path": "named-pipe", "status": "unsupported"},
        {"path": "ordinary-dir", "status": "unsupported"},
        {"path": "large.txt", "status": "too_large"},
    ]}
    assert (outside / "note.txt").read_bytes() == b"outside source"


def test_check_references_reports_inaccessible_file(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "restricted.txt").write_bytes(b"restricted")
    identity = desktop_source.source_identity(root)
    open_file = os.open

    def deny_file(path, flags, **kwargs):
        if path == "restricted.txt":
            raise PermissionError(EACCES, "access denied")
        return open_file(path, flags, **kwargs)

    monkeypatch.setattr(desktop_source.os, "open", deny_file)
    assert desktop_source.check_references(root, [{"path": "restricted.txt"}], expected_identity=identity) == {
        "status": "available", "files": [{"path": "restricted.txt", "status": "inaccessible"}],
    }


@pytest.mark.parametrize("change", ["in-place", "file", "parent"])
def test_check_references_suppresses_digest_when_editor_changes_a_file(tmp_path, monkeypatch, change):
    root = tmp_path.resolve()
    directory = root / "draft"
    directory.mkdir()
    file = directory / "proof.txt"
    file.write_bytes(b"first version")
    identity = desktop_source.source_identity(root)
    hash_file = desktop_source._hash_file

    def save_before_verification(descriptor, size):
        digest = hash_file(descriptor, size)
        if change == "file":
            replacement = directory / "editor-save"
            replacement.write_bytes(b"other version")
            replacement.replace(file)
        else:
            if change == "parent":
                directory.rename(root / "old-draft")
                directory.mkdir()
            file.write_bytes(b"other version")
        return digest

    monkeypatch.setattr(desktop_source, "_hash_file", save_before_verification)
    selected = [{"path": "draft/proof.txt", "sha256": sha256(b"first version").hexdigest()}]
    assert desktop_source.check_references(root, selected, expected_identity=identity) == {
        "status": "available", "files": [{"path": "draft/proof.txt", "status": "unstable"}],
    }
    monkeypatch.setattr(desktop_source, "_hash_file", hash_file)
    assert desktop_source.check_references(root, selected, expected_identity=identity) == {"status": "available", "files": [
        {"path": "draft/proof.txt", "status": "changed", "sha256": sha256(b"other version").hexdigest()},
    ]}


def test_check_references_limits_reads_when_source_grows(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    file = root / "growing.txt"
    file.write_bytes(b"initial")
    identity = desktop_source.source_identity(root)
    read_file = os.read
    bytes_read = []

    def grow_file(descriptor, size):
        with file.open("ab") as stream:
            stream.write(b"appended")
        content = read_file(descriptor, size)
        bytes_read.append(len(content))
        return content

    monkeypatch.setattr(desktop_source.os, "read", grow_file)
    assert desktop_source.check_references(root, [{"path": "growing.txt"}], expected_identity=identity) == {
        "status": "available", "files": [{"path": "growing.txt", "status": "unstable"}],
    }
    assert sum(bytes_read) == len(b"initial")


def test_check_references_rejects_symlink_in_saved_root_path(tmp_path):
    root = tmp_path.resolve()
    (root / "direct" / "source").mkdir(parents=True)
    (root / "linked").symlink_to(root / "direct", target_is_directory=True)
    identity = desktop_source.source_identity(root / "direct" / "source")
    selected = [{"path": "proof.txt"}]
    for path in (root / "linked" / "source", root / "missing"):
        assert desktop_source.source_identity(path) is None
        assert desktop_source.source_status(path, identity) == "unavailable"
        assert desktop_source.check_references(path, selected, expected_identity=identity) == {
            "status": "unavailable", "files": [],
        }


def test_check_references_returns_no_digests_if_root_is_replaced(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    (root / "proof.txt").write_bytes(b"first")
    identity = desktop_source.source_identity(root)
    hash_file = desktop_source._hash_file

    def replace_root_before_verification(descriptor, size):
        digest = hash_file(descriptor, size)
        root.rename(root.parent / "moved-source")
        root.mkdir()
        (root / "proof.txt").write_bytes(b"second")
        return digest

    monkeypatch.setattr(desktop_source, "_hash_file", replace_root_before_verification)
    assert desktop_source.check_references(root, [{"path": "proof.txt"}], expected_identity=identity) == {
        "status": "unstable", "files": [],
    }


def test_directory_identity_survives_child_edits_and_rename(tmp_path):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    identity = desktop_source.source_identity(root)
    info = root.stat()
    assert set(identity) == {"device", "inode", "birth_time_ns"}
    assert identity["device"] == info.st_dev >= 0
    assert identity["inode"] == info.st_ino > 0
    if hasattr(info, "st_birthtime"):
        assert identity["birth_time_ns"] == round(info.st_birthtime * 1_000_000_000)
    else:
        assert identity["birth_time_ns"] is None

    file = root / "draft.txt"
    file.write_bytes(b"edit")
    file.unlink()
    moved = root.with_name("moved")
    root.rename(moved)
    assert desktop_source.source_identity(moved) == identity
    assert desktop_source.source_status(moved, identity) == "available"
    assert desktop_source.source_status(root, identity) == "unavailable"


@pytest.mark.parametrize("legacy", [True, False])
def test_source_gate_blocks_file_open_without_matching_identity(tmp_path, monkeypatch, legacy):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    if legacy:
        identity = None
        status = "needs_confirmation"
    else:
        identity = desktop_source.source_identity(root)
        root.rename(root.with_name("moved"))
        root.mkdir()
        status = "identity_changed"
    (root / "draft.txt").write_bytes(b"unapproved contents")

    def unexpected_file(*args, **kwargs):
        pytest.fail("the directory identity must be verified before opening selected files")

    monkeypatch.setattr(desktop_source, "_regular_file", unexpected_file)
    assert desktop_source.source_status(root, identity) == status
    assert desktop_source.check_references(root, [{"path": "draft.txt"}], expected_identity=identity) == {
        "status": status, "files": [],
    }
