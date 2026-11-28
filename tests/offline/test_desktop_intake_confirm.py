# SPDX-License-Identifier: Apache-2.0
"""Consent, durable replay and interruption boundaries for native pre-project intake."""

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from research_multiplier.storage import credentials, paths, postgres
from research_multiplier.storage.managed_workspaces import LibraryError, MANAGED_FILE, workspace_marker
from research_multiplier.workbench import desktop
from research_multiplier.workflows import desktop_intake, desktop_intake_confirm, desktop_library, desktop_pdf, desktop_workspace
from research_multiplier.workflows.desktop_workspace import WorkspaceCreationError


pytestmark = [pytest.mark.offline, pytest.mark.skipif(os.name != "posix", reason="Native POSIX launcher.")]


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "app"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    created = []

    def create(workspace, *, minimal, before_commit=None):
        assert minimal
        paths.initialize_workspace(workspace, minimal=True)
        paths.bind_database(workspace, uuid4())
        if before_commit is not None:
            before_commit()
        credentials.write(paths.workspace_id(workspace), {key: "fixture" for key in credentials.KEYS})
        created.append(workspace)

    def check(workspace, row):
        assert str(paths.workspace_id(workspace)) == row["id"]
        assert str(paths.database_id(workspace)) == row["database"]

    monkeypatch.setattr(desktop_library, "create_workspace", create)
    monkeypatch.setattr(desktop_library, "_check_binding", check)
    return desktop_library.Library(), created


@pytest.fixture
def request_body(tmp_path):
    root = tmp_path.resolve() / "source"
    root.mkdir()
    note = root / "notes.md"
    note.write_bytes(b"A unit is positive. Its square is positive.")
    ref = {"path": note.name, "sha256": sha256(note.read_bytes()).hexdigest()}
    draft = {"title": "Edited title", "question": "How does squaring act?", "sources": [ref],
             "items": [{"id": "i2", "kind": "hypothesis", "label": "Edited hypothesis",
                        "source": {**ref, "quote": "Its square is positive."}},
                       {"id": "i1", "kind": "definition", "label": "Edited definition",
                        "source": {**ref, "quote": "A unit is positive."}}],
             "links": [{"from": "i2", "to": "i1", "kind": "suggested_dependency"}]}
    return {"operation_id": str(uuid4()), "mode": "folder", "root": str(root),
            "identity": desktop_intake.inspect(str(root))["identity"], "draft": draft}


def command(monkeypatch, capsys, *args, body=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    with TextIOWrapper(BytesIO(raw)) as stdin:
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "argv", ["desktop", *args])
        status = desktop.main()
    captured = capsys.readouterr()
    assert captured.err == ""
    reply = json.loads(captured.out)
    assert status == (0 if reply["status"] == "ready" else 2)
    assert reply.pop("version") == 1
    return reply


def test_confirm_read_replay_move_and_explicit_removal(profile, request_body, monkeypatch, capsys, tmp_path):
    library, created = profile
    old = library.open_default()
    old_id = str(paths.workspace_id(old))
    library.remember_project(old_id)
    settings = library.settings()
    old_files = list(old.iterdir())
    note = Path(request_body["root"]) / "notes.md"
    source_bytes = note.read_bytes()
    project = command(monkeypatch, capsys, "--intake-confirm", body=request_body)["project"]
    workspace = Path(project["path"])
    assert project == library.describe(workspace)
    assert project["name"] == "Edited title" and project["managed"] is True
    assert created == [old, workspace]
    assert old_files == list(old.iterdir()) and settings == library.settings()
    assert note.read_bytes() == source_bytes and list(note.parent.iterdir()) == [note]
    material = workspace / "inputs" / "project-intake.json"
    assert request_body["root"] not in material.read_text()
    marker = workspace_marker(workspace)
    assert (marker["source_root"], marker["source_identity"]) == (note.parent, request_body["identity"])
    note.unlink()
    note.parent.rmdir()
    assert command(monkeypatch, capsys, "--intake-read", project["id"])["draft"] == request_body["draft"]
    assert command(monkeypatch, capsys, "--intake-confirm", body=request_body)["project"] == project
    changed = deepcopy(request_body)
    changed["draft"]["title"] = "Different"
    assert command(monkeypatch, capsys, "--intake-confirm", body=changed)["code"] == "storage-stale"

    plan = library.preview(tmp_path / "moved")
    library.move(plan["destination"], plan["sha256"])
    moved = library.confirm_intake(**request_body)
    assert moved["id"] == project["id"] and Path(moved["path"]).is_relative_to(tmp_path / "moved")
    assert created == [old, workspace]
    library.unregister(project["id"])
    with pytest.raises(LibraryError, match="storage-project"):
        library.confirm_intake(**request_body)
    assert [row["id"] for row in library.status()["projects"]] == [old_id]


@pytest.mark.parametrize("point", ["input", "marker", "registry"])
def test_interrupt_and_retry_partially_published_slot(profile, request_body, monkeypatch, point):
    library, created = profile
    library.ensure_ready()
    with monkeypatch.context() as patch:
        write = desktop_library.publish_metadata
        def interrupt(file, data):
            if (point == "marker" and file.name == MANAGED_FILE
                    or point == "registry" and file.name == "registry.json"
                    or point == "input" and file.name == "project-intake.json"):
                raise OSError
            write(file, data)
        patch.setattr(desktop_library, "publish_metadata", interrupt)
        patch.setattr(desktop_intake_confirm, "publish_metadata", interrupt)
        with pytest.raises(LibraryError, match="storage-unavailable"):
            library.confirm_intake(**request_body)
    assert len(created) == 1 and not library.status()["projects"]
    if point != "input":
        (Path(request_body["root"]) / "notes.md").unlink()
    restarted = desktop_library.Library()
    project = restarted.confirm_intake(**request_body)
    assert created == [Path(project["path"])]
    assert restarted.read_intake(project["id"]) == request_body["draft"]
    assert [row["id"] for row in restarted.status()["projects"]] == [project["id"]]


def test_incomplete_slot_retained_and_binding_failure_never_reprovisions(profile, request_body, monkeypatch):
    library, created = profile
    partial = []
    with monkeypatch.context() as patch:
        def fail(workspace, *, minimal, before_commit):
            workspace.mkdir()
            (workspace / "unfinished").write_text("preserve")
            partial.append(workspace)
            raise WorkspaceCreationError("create-failed")
        patch.setattr(desktop_library, "create_workspace", fail)
        with pytest.raises(LibraryError, match="create-failed"):
            library.confirm_intake(**request_body)
    with monkeypatch.context() as patch:
        def interrupted(*args):
            raise OSError
        patch.setattr(desktop_intake_confirm, "publish", interrupted)
        with pytest.raises(LibraryError, match="storage-unavailable"):
            library.confirm_intake(**request_body)
    with monkeypatch.context() as patch:
        def unavailable(*args):
            raise LibraryError("storage-database")
        patch.setattr(desktop_library, "_check_binding", unavailable)
        with pytest.raises(LibraryError, match="storage-database"):
            library.confirm_intake(**request_body)
    assert not library.status()["projects"] and len(created) == 1
    result = library.confirm_intake(**request_body)
    assert created == [Path(result["path"])] and created != partial
    assert (partial[0] / "unfinished").read_text() == "preserve"


@pytest.mark.parametrize("problem,code", [
    ("quote", "intake-changed"), ("origin", "intake-changed"), ("digest", "intake-changed"),
    ("during-create", "intake-changed"), ("missing", "intake-unavailable"), ("identity", "intake-changed"),
    ("endpoints", "storage-invalid"), ("title", "storage-project"), ("paper", "storage-invalid"),
    ("kind", "storage-invalid"), ("extra", "storage-invalid"),
])
def test_reject_bad_material_without_publishing(profile, request_body, problem, code, monkeypatch):
    library, created = profile
    draft = request_body["draft"]
    item = draft["items"][0]
    note = Path(request_body["root"]) / "notes.md"
    if problem == "quote":
        item["source"]["quote"] = "absent"
    elif problem == "origin":
        item["source"]["page"] = 1
    elif problem == "digest":
        note.write_bytes(b"changed")
    elif problem == "during-create":
        original = desktop_library.create_workspace
        def change(workspace, *, minimal, before_commit):
            note.write_bytes(b"changed")
            original(workspace, minimal=minimal, before_commit=before_commit)
        monkeypatch.setattr(desktop_library, "create_workspace", change)
    elif problem == "missing":
        note.unlink()
    elif problem == "identity":
        request_body["identity"]["inode"] += 1
    elif problem == "endpoints":
        draft["links"][0]["to"] = "i3"
    elif problem == "title":
        draft["title"] = "a" * 121
    elif problem == "paper":
        request_body["mode"] = "paper"
    elif problem == "kind":
        item["kind"] = []
    else:
        draft["user_path"] = str(note)
    with pytest.raises(LibraryError, match=code):
        library.confirm_intake(**request_body)
    assert not library.status()["projects"]
    assert not created
    assert all(not (workspace / MANAGED_FILE).exists() for workspace in created)


def test_known_folder_and_cancel_preserve_workspaces(profile, request_body):
    library, created = profile
    root = Path(request_body["root"])
    before = sorted(root.iterdir())
    selection = desktop_intake.inspect(str(root))
    desktop_intake.preview(str(root), selection["identity"], request_body["draft"]["sources"])
    # Cancel at the preview. No confirmed request is submitted.
    assert not library.anchor.exists() and not created and sorted(root.iterdir()) == before
    old = library.open_folder(root)
    with pytest.raises(LibraryError, match="workspace-occupied"):
        library.confirm_intake(**request_body)
    assert created == [old] and not (old / "inputs" / "project-intake.json").exists()


def test_two_papers_in_one_directory_keep_exact_pages(profile, request_body, monkeypatch):
    library, created = profile
    root = Path(request_body["root"])
    def extract(raw, budget, timeout):
        return json.dumps({"status": "available", "page_count": 5, "pages_included_through": 4,
                           "pages": [{"page": 2, "text": "Introduction."}, {"page": 4, "text": "The same quote."}]}).encode()
    monkeypatch.setattr(desktop_pdf, "bundled_extractor", lambda: extract)
    for filename in ("first.pdf", "second.PDF"):
        file = root / filename
        file.write_bytes(filename.encode().ljust(4 * 1024 * 1024 + 1, b" "))
        pin = {"path": filename, "sha256": sha256(file.read_bytes()).hexdigest()}
        draft = {"title": filename, "question": "Which page?", "links": [],
                 "sources": [{**pin, "page_count": 5, "pages_included_through": 4}],
                 "items": [{"id": "i1", "kind": "theorem", "label": "Edited theorem",
                            "source": {**pin, "page": 4, "quote": "same quote"}}]}
        request = {**request_body, "operation_id": str(uuid4()), "mode": "paper", "draft": draft}
        wrong = deepcopy(request)
        wrong["draft"]["items"][0]["source"]["page"] = 2
        with pytest.raises(LibraryError, match="intake-changed"):
            library.confirm_intake(**wrong)
        project = library.confirm_intake(**request)
        file.unlink()
        assert library.read_intake(project["id"]) == draft
        assert workspace_marker(Path(project["path"]))["source_root"] is None
        assert library.confirm_intake(**request) == project
    (library.anchor / "Library" / "registry.json").unlink()
    assert library.read_intake(project["id"]) == draft
    state = library.status()
    assert len(created) == len(state["projects"]) == 2 and state["default_id"] is None
    # Paper mode leaves the parent unclaimed, so moving the library beside the papers is allowed.
    plan = library.preview(root / "moved-library")
    library.move(plan["destination"], plan["sha256"])
    moved = library.confirm_intake(**request)
    assert moved["id"] == project["id"] and Path(moved["path"]).is_relative_to(plan["destination"])
    with pytest.raises(LibraryError, match="storage-destination"):
        library.confirm_intake(**{**request, "operation_id": str(uuid4())})


def test_ipc_request_bounds_and_nested_duplicate_keys(profile, request_body, monkeypatch, capsys):
    encoded = json.dumps(request_body).encode()
    duplicate = encoded.replace(b'"items":', b'"items": [], "items":')
    assert command(monkeypatch, capsys, "--intake-confirm", body=duplicate)["code"] == "storage-invalid"
    assert command(monkeypatch, capsys, "--intake-confirm", body=encoded.ljust(262145))["code"] == "storage-invalid"
    assert command(monkeypatch, capsys, "--intake-confirm", body=encoded.ljust(262144))["project"]

def test_source_change_uses_provisioning_rollback_before_persisting_logins(tmp_path, monkeypatch, request_body):
    # Real provisioning changes process logins. Keep this simulated launch isolated from later tests.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "app"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    setup = desktop_workspace._DesktopSetup
    recorded = []
    @contextmanager
    def fresh(self, _):
        try:
            yield {key: "fixture" for key in credentials.KEYS}
        except WorkspaceCreationError as error:
            recorded.append((self.workspace, error.args[0]))
            raise

    def bind(workspace):
        paths.bind_database(workspace, uuid4())
        (Path(request_body["root"]) / "notes.md").write_bytes(b"Changed during setup.")

    monkeypatch.setattr(setup, "admin_connection", lambda _: nullcontext())
    monkeypatch.setattr(setup, "check_server", lambda *args: None)
    monkeypatch.setattr(setup, "fresh_database", fresh)
    monkeypatch.setattr(postgres, "bind_workspace", bind)
    library = desktop_library.Library()
    with pytest.raises(LibraryError, match="intake-changed"):
        library.confirm_intake(**request_body)
    workspace, reason = recorded[0]
    assert reason == "intake-changed" and len(recorded) == 1
    assert credentials.read(paths.workspace_id(workspace)) is None
    assert not (workspace / MANAGED_FILE).exists() and not library.status()["projects"]
