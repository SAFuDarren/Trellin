from hashlib import sha256
from io import BytesIO, TextIOWrapper
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research_multiplier.storage.paths import WORKSPACE_FILE
from research_multiplier.workbench import desktop
from research_multiplier.workflows import desktop_intake, desktop_source
from research_multiplier.workflows.intake import MAX_MODEL_NOTES_BYTES


pytestmark = [pytest.mark.offline, pytest.mark.skipif(os.name != "posix", reason="Native POSIX launcher.")]


def _command(flag, request):
    payload = request if isinstance(request, bytes) else json.dumps(request).encode()
    process = subprocess.run(
        [sys.executable, "-B", "-m", "research_multiplier.workbench.desktop", "--intake-" + flag],
        cwd=Path(__file__).resolve().parents[2], input=payload, capture_output=True, timeout=15,
    )
    assert process.stderr == b""
    reply = json.loads(process.stdout)
    assert process.returncode == (0 if reply["status"] == "ready" else 2)
    return reply


def _error(code):
    return {"version": 1, "status": "error", "code": code}


def _request(selection, *files):
    return {"root": selection["root"], "identity": selection["identity"],
            "references": [{"path": name} for name in files]}


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "library"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return root


def _snapshot(directory):
    return {entry.relative_to(directory): (entry.stat().st_mode, entry.stat().st_mtime_ns,
                                           entry.read_bytes() if entry.is_file() else None)
            for entry in (directory, *directory.rglob("*"))}


@pytest.fixture
def read_only():
    modes = {}

    def protect(root):
        for entry in (*root.rglob("*"), root):
            modes[entry] = entry.stat().st_mode
            entry.chmod(modes[entry] & ~0o222)

    yield protect
    for entry, mode in reversed(modes.items()):
        entry.chmod(mode)


def test_launcher_repeat_preview_preserves_source_config_and_provenance(source, tmp_path, read_only):
    alias = tmp_path / "picked"
    alias.symlink_to(source, target_is_directory=True)
    (source / "Trellin").mkdir()
    (source / "notes").mkdir()
    (source / "unselected.txt").write_text("unselected fixture")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "setting").write_bytes(b"preset")
    content = {"notes/题目.MD": "甲\r\nβ\r\n", "Trellin/note.markdown": "heading\n",
               "plain.txt": "\ufeffpayload", "note.TeX": "a\rb", "claim.Lean": "exact h"}
    for name, text in content.items():
        (source / name).write_bytes(text.encode())
    read_only(source)
    read_only(tmp_path / "config")
    before = _snapshot(source), _snapshot(tmp_path / "config")
    inspection = _command("inspect", {"root": str(alias)})
    selection = inspection["selection"]
    assert selection == {"root": str(source), "identity": desktop_source.source_identity(source), "workspace": None}
    assert inspection == _command("inspect", {"root": str(alias)})
    request = _request(selection, *content)
    expected = [{"path": name, "status": "available", "sha256": sha256(text.encode()).hexdigest(), "text": text}
                for name, text in content.items()]
    for _ in range(2):
        assert _command("preview", request) == {"version": 1, "status": "ready", "files": expected}
    assert before == (_snapshot(source), _snapshot(tmp_path / "config"))
    assert not (tmp_path / "library").exists()


def test_launcher_returns_only_available_material_and_applies_aggregate_cap(source):
    (source / "bad.md").write_bytes(b"\xff")
    (source / "one.txt").write_bytes(b"a" * (MAX_MODEL_NOTES_BYTES - 1))
    (source / "two.txt").write_bytes(b"ab")
    (source / "large.txt").write_bytes(b"a" * (MAX_MODEL_NOTES_BYTES + 1))
    (source / "link.txt").symlink_to(source / "one.txt")
    (source / "parent").symlink_to(source, target_is_directory=True)
    os.mkfifo(source / "pipe.tex")
    os.mkfifo(source / "book.pdf")
    names = ["bad.md", "large.txt", "one.txt", "two.txt", "link.txt", "parent/one.txt", "pipe.tex",
             "missing.lean", "book.pdf", "unknown.bin"]
    request = _request(_command("inspect", {"root": str(source)})["selection"], *names)
    files = _command("preview", request)["files"]
    statuses = ["invalid_utf8", "too_large", "available", "too_large", "unsupported", "unsupported", "unsupported",
                "missing", "pdf_deferred", "unsupported"]
    for name, status, row in zip(names, statuses, files, strict=True):
        if status == "available":
            assert row["text"] == "a" * (MAX_MODEL_NOTES_BYTES - 1)
        else:
            assert row == {"path": name, "status": status}
    (source / "one.txt").write_bytes(b"edited")
    request["references"] = [{"path": "one.txt", "sha256": files[2]["sha256"]}]
    assert _command("preview", request)["files"] == [{"path": "one.txt", "status": "changed"}]


def test_launcher_withholds_empty_and_binary_material(source):
    content = {"blank.md": b"", "space.md": "\ufeff \r\n".encode(), "nul.txt": b"a\x00b", "control.txt": b"\x01"}
    for name, raw in content.items():
        (source / name).write_bytes(raw)
    request = _request(_command("inspect", {"root": str(source)})["selection"], *content)
    assert _command("preview", request)["files"] == [
        {"path": name, "status": status} for name, status in zip(content, ("empty", "empty", "binary", "binary"), strict=True)
    ]


@pytest.mark.parametrize("legacy", [False, True])
def test_launcher_redirects_workspace_even_if_initialized_after_inspection(source, legacy):
    plain = _command("inspect", {"root": str(source)})["selection"]
    workspace = source / "Trellin" if legacy else source
    workspace.mkdir(exist_ok=True)
    # Marker contents belong to the normal workspace opener.
    (workspace / WORKSPACE_FILE).write_bytes(b"invalid fixture")
    assert _command("inspect", {"root": str(source)})["selection"]["workspace"] == str(workspace)
    assert _command("preview", _request(plain, "note.txt")) == _error("workspace-occupied")


def test_launcher_rejects_replaced_root_malformed_input_and_duplicate_keys(source):
    selection = _command("inspect", {"root": str(source)})["selection"]
    source.rename(source.with_name("moved"))
    source.mkdir()
    assert _command("preview", _request(selection, "x.md")) == _error("intake-changed")
    for raw in (b"[]", b'{"root":"x","root":"y"}', b'{"root":"\xff"}', b" " * (64 * 1024 + 1)):
        assert _command("inspect", raw) == _error("storage-invalid")
    assert _command("inspect", {"root": "relative"}) == _error("workspace")


@pytest.mark.parametrize("change", [
    {"references": []}, {"references": [{"path": "a.txt"}] * 2},
    {"references": [{"path": "a.txt"}, {"path": "../other.txt"}]},
    {"references": [{"path": "/absolute.md"}]}, {"references": [{"path": ".git/note.md"}]},
    {"references": [{"path": "a.md", "sha256": None}]}, {"references": [{"path": str(i)} for i in range(17)]},
    {"identity": None}, {"identity": {"device": False, "inode": 1, "birth_time_ns": None}},
    {"root": "/a/../b"}, {"root": "\ud800"}, {"references": [{"path": "\ud800"}]},
])
def test_launcher_validates_entire_request_before_filesystem_access(source, monkeypatch, capsys, change):
    request = _request(desktop_intake.inspect(str(source)), "one.md")
    request.update(change)
    monkeypatch.setattr(desktop_source, "_source_directory", lambda *a: pytest.fail("filesystem access"))
    monkeypatch.setattr(desktop, "Library", lambda: pytest.fail("library access"))
    with TextIOWrapper(BytesIO(json.dumps(request).encode())) as stdin:
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "argv", ["desktop", "--intake-preview"])
        assert desktop.main() == 2
    assert json.loads(capsys.readouterr().out) == _error("storage-invalid")


@pytest.mark.parametrize("change", ["file", "directory", "root", "workspace"])
def test_later_read_can_invalidate_earlier_file_or_entire_preview(source, monkeypatch, change):
    folder = source / "draft"
    folder.mkdir()
    first = folder / "one.md"
    first.write_bytes(b"first")
    second = source / "two.md"
    second.write_bytes(b"second")
    selected = desktop_intake.inspect(str(source))
    chunks = desktop_source._file_chunks

    def edit_during_second(descriptor, size):
        yield from chunks(descriptor, size)
        if size != len(b"second"):
            return
        if change == "file":
            first.write_bytes(b"edited")
        elif change == "directory":
            folder.rename(source / "prior")
            folder.mkdir()
            first.write_bytes(b"first")
        elif change == "root":
            source.rename(source.with_name("prior"))
            source.mkdir()
        else:
            (source / WORKSPACE_FILE).touch()

    monkeypatch.setattr(desktop_source, "_file_chunks", edit_during_second)
    request = _request(selected, "draft/one.md", "two.md")
    if change in {"workspace", "root"}:
        with pytest.raises(desktop.LibraryError, match="intake-changed"):
            desktop_intake.preview(str(source), selected["identity"], request["references"])
    else:
        files = desktop_intake.preview(str(source), selected["identity"], request["references"])
        assert files[0] == {"path": "draft/one.md", "status": "unstable"}
        assert files[1]["text"] == "second"


def test_unreadable_file_and_deferred_pdf_do_not_expose_content(source, monkeypatch):
    selection = desktop_intake.inspect(str(source))
    opened = []

    def denied(root, path):
        opened.append(str(path))
        raise PermissionError("private diagnostic")

    monkeypatch.setattr(desktop_source, "_regular_file", denied)
    files = desktop_intake.preview(str(source), selection["identity"], [{"path": "book.PDF"}, {"path": "note.md"}])
    assert opened == ["note.md"]
    assert files == [{"path": "book.PDF", "status": "pdf_deferred"}, {"path": "note.md", "status": "inaccessible"}]


@pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else True, reason="Requires ordinary POSIX permissions.")
def test_launcher_reports_denied_file(source):
    note = source / "private.txt"
    note.write_text("synthetic private fixture")
    note.chmod(0)
    try:
        request = _request(_command("inspect", {"root": str(source)})["selection"], note.name)
        assert _command("preview", request)["files"] == [{"path": note.name, "status": "inaccessible"}]
    finally:
        note.chmod(0o600)


def test_preview_only_visits_explicit_paths_and_workspace_markers(source, monkeypatch):
    (source / "picked.md").write_bytes(b"selected")
    (source / "other.txt").write_bytes(b"unselected")
    opened = []
    open_file = desktop_source._regular_file

    def record(root, path):
        opened.append(str(path))
        return open_file(root, path)

    monkeypatch.setattr(os, "scandir", lambda *a: pytest.fail("directory scan"))
    monkeypatch.setattr(desktop_source, "_regular_file", record)
    selection = desktop_intake.inspect(str(source))
    files = desktop_intake.preview(str(source), selection["identity"], [{"path": "book.pdf"}, {"path": "picked.md"}])
    assert files[1]["text"] == "selected"
    assert set(opened) == {"picked.md"}


def test_picker_alias_retargeted_after_resolution_fails_closed(source, tmp_path, monkeypatch):
    alias = tmp_path / "picked"
    other = tmp_path / "other"
    other.mkdir()
    alias.symlink_to(source)
    resolve = desktop_intake.resolve_selected_folder

    def retarget(location):
        observation = resolve(location)
        alias.unlink()
        alias.symlink_to(other)
        return observation

    monkeypatch.setattr(desktop_intake, "resolve_selected_folder", retarget)
    with pytest.raises(desktop.LibraryError, match="intake-changed"):
        desktop_intake.inspect(str(alias))
