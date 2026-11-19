"""The default library and checked move must preserve project identity."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from research_multiplier.cli import main
from research_multiplier.storage import paths
from research_multiplier.storage.managed_workspaces import LOCATION_FILE, MANAGED_FILE, LibraryError, workspace_registration
from research_multiplier.workflows import desktop_library
from research_multiplier.workflows.desktop_workspace import WorkspaceCreationError


pytestmark = [pytest.mark.offline, pytest.mark.skipif(os.name != "posix", reason="Mac file lease behavior.")]


@pytest.fixture
def library(monkeypatch, tmp_path):
    def create(workspace, *, minimal=False):
        assert minimal
        result = paths.initialize_workspace(workspace, minimal=minimal)
        paths.bind_database(result, uuid4())
        return result

    def check(workspace, row):
        assert str(paths.workspace_id(workspace)) == row["id"]
        assert str(paths.database_id(workspace)) == row["database"]

    monkeypatch.setattr(desktop_library, "create_workspace", create)
    monkeypatch.setattr(desktop_library, "_check_binding", check)
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "app support"))
    return desktop_library.Library()


def test_first_start_creates_one_minimal_default_and_keeps_old_folders(library, tmp_path):
    anchor = library.anchor
    assert not anchor.exists()
    legacy = paths.initialize_workspace(tmp_path / "legacy" / "Trellin")
    paths.bind_database(legacy, uuid4())
    legacy_text = legacy / "inputs" / "source.md"
    legacy_text.write_text("Existing research")

    # Merely resolving an old session need not initialize a library.
    assert library.resolve_existing(legacy) == legacy
    assert not anchor.exists()
    default = library.open_default()
    assert desktop_library.Library().open_default() == default
    assert default.parent == anchor / "Library" / "Workspaces"
    assert not any((default / item).exists() for item in paths.WORKSPACE_DIRECTORIES)

    captured = library.register_external(legacy)
    assert library.register_external(legacy) == captured
    named = library.create("Other theorem")
    state = library.status()
    assert state["default_id"] == str(paths.workspace_id(default))
    assert [
        (row["name"], row["path"], row["managed"]) for row in state["projects"]
    ] == [("Library", str(default), True), ("legacy", str(legacy), False), ("Other theorem", str(named), True)]
    assert legacy_text.read_text() == "Existing research"
    assert not (legacy / MANAGED_FILE).exists()
    assert len({paths.database_id(workspace) for workspace in (default, legacy, named)}) == 3
    assert library.describe(legacy)["id"] == str(paths.workspace_id(legacy))


def test_cli_init_creates_inputs_when_first_used(library):
    workspace = library.open_default()
    draft = workspace / "inputs" / "question.json"
    assert not draft.parent.exists()
    assert main(["--workspace", str(workspace), "init", str(draft)]) == 0
    assert json.loads(draft.read_text())["kind"] == "open"
    assert not (workspace / "runs").exists()


def test_default_retries_the_same_provision_after_postgres_is_available(monkeypatch, library):
    slots = []
    create = desktop_library.create_workspace

    def launch(workspace, *, minimal=False):
        slots.append(workspace)
        if len(slots) == 1:
            raise WorkspaceCreationError("create-postgres")
        return create(workspace, minimal=minimal)

    monkeypatch.setattr(desktop_library, "create_workspace", launch)
    with pytest.raises(LibraryError, match="create-postgres"):
        library.open_default()
    assert library.status()["projects"] == []
    assert slots[0] == library.open_default() == slots[1]


def test_default_retries_after_incomplete_provision_without_touching_its_files(monkeypatch, library):
    create = desktop_library.create_workspace
    interrupted = []

    def launch(workspace, *, minimal=False):
        if not interrupted:
            interrupted.append(workspace)
            workspace.mkdir()
            (workspace / "unfinished").write_text("Retain for recovery")
            raise WorkspaceCreationError("create-failed")
        return create(workspace, minimal=minimal)

    monkeypatch.setattr(desktop_library, "create_workspace", launch)
    with pytest.raises(LibraryError, match="create-failed"):
        library.open_default()
    recovered = library.open_default()
    assert recovered != interrupted[0]
    assert library.open_default() == recovered
    assert (interrupted[0] / "unfinished").read_text() == "Retain for recovery"
    assert [item["path"] for item in library.status()["projects"]] == [str(recovered)]


def test_complete_unpublished_default_is_reused_after_transient_database_failure(monkeypatch, library, tmp_path):
    create = desktop_library.create_workspace
    check = desktop_library._check_binding
    pending = []
    saved_login = tmp_path / "existing-login"
    monkeypatch.setattr(desktop_library.credentials, "location", lambda _: saved_login)

    def launch(workspace, *, minimal=False):
        pending.append(workspace)
        create(workspace, minimal=minimal)
        saved_login.touch()
        raise WorkspaceCreationError("create-failed")

    def unavailable(*_):
        raise LibraryError("storage-database")

    monkeypatch.setattr(desktop_library, "create_workspace", launch)
    with pytest.raises(LibraryError, match="create-failed"):
        library.open_default()
    monkeypatch.setattr(desktop_library, "_check_binding", unavailable)
    with pytest.raises(LibraryError, match="storage-database"):
        library.open_default()
    monkeypatch.setattr(desktop_library, "_check_binding", check)
    assert library.open_default() == pending[0]
    assert len(pending) == 1


def test_incidental_external_alias_does_not_block_managed_library(library, tmp_path):
    default = library.open_default()
    legacy = paths.initialize_workspace(tmp_path / "old" / "Trellin")
    paths.bind_database(legacy, uuid4())
    saved = library.register_external(legacy)
    relocated_parent = legacy.parent.rename(tmp_path / "relocated")
    legacy.parent.symlink_to(relocated_parent, target_is_directory=True)

    assert library.open_default() == default
    assert library.resolve_project(saved["id"]) == relocated_parent / legacy.name
    assert library.register_external(relocated_parent / legacy.name) == saved
    move = library.preview(tmp_path / "new library")
    assert move["project_count"] == 1


def test_default_recovers_after_registry_publication_interruption(library):
    root = library.anchor / "Library"
    (root / "Workspaces").mkdir(parents=True)
    interrupted = root / ".trellin-failed"
    interrupted.write_bytes(b'{"incomplete')
    assert library.open_default().parent == root / "Workspaces"
    assert interrupted.read_bytes() == b'{"incomplete'


def test_move_preserves_workspace_ids_bytes_and_external_registration(library, tmp_path):
    default = library.open_default()
    secondary = library.create("Second theorem")
    legacy = paths.initialize_workspace(tmp_path / "legacy")
    paths.bind_database(legacy, uuid4())
    library.register_external(legacy)
    for index, workspace in enumerate((default, secondary)):
        (workspace / "objects").mkdir()
        (workspace / "objects" / "receipt.json").write_text(json.dumps({"evidence": f"receipt-{index}"}))

    destination = tmp_path / "other disk" / "Trellin Library"
    destination.parent.mkdir()
    preview = library.preview(destination)
    assert preview["project_count"] == 2
    assert not destination.exists()
    moved = library.move(destination, preview["sha256"])
    assert moved["root"] == str(destination)
    assert moved["backup"] == str(default.parent.parent)
    assert library.status()["root"] == str(destination)
    for workspace in (default, secondary):
        new = library.resolve_existing(workspace)
        assert new == destination / "Workspaces" / workspace.name
        assert library.resolve_project(str(paths.workspace_id(workspace))) == new
        assert (workspace / "objects" / "receipt.json").read_bytes() == (new / "objects" / "receipt.json").read_bytes()
        assert paths.database_id(new) == paths.database_id(workspace)
        with paths.use_workspace(new, strict=True):
            assert paths.active_workspace() == new
        with pytest.raises(LibraryError, match="storage-stale"), paths.use_workspace(workspace, strict=True):
            pytest.fail("A stale path must not reopen the retired copy for writes.")
        with pytest.raises(LibraryError, match="storage-stale"):
            paths.initialize_workspace(workspace)
    assert library.resolve_existing(legacy) == legacy
    assert library.describe(legacy)["path"] == str(legacy)
    assert not (destination / legacy.name).exists()


def test_move_refuses_stale_plan_busy_workspace_link_and_failed_binding(monkeypatch, library, tmp_path):
    workspace = library.open_default()
    source_info = library.status()
    target = tmp_path / "moved"
    preview = library.preview(target)
    with paths.use_workspace(workspace, strict=True):
        with pytest.raises(LibraryError, match="storage-busy"):
            library.move(target, preview["sha256"])

    file = workspace / "new.md"
    file.write_text("Changing source")
    with pytest.raises(LibraryError, match="storage-stale"):
        library.move(target, preview["sha256"])
    assert library.status() == source_info

    link = workspace / "linked notes"
    link.symlink_to(file)
    with pytest.raises(LibraryError, match="storage-verification"):
        library.preview(target)
    link.unlink()

    def reject(*_):
        raise LibraryError("storage-database")

    monkeypatch.setattr(desktop_library, "_check_binding", reject)
    preview = library.preview(target)
    with pytest.raises(LibraryError, match="storage-database"):
        library.move(target, preview["sha256"])
    assert not target.exists()
    assert list(tmp_path.glob(".trellin-move-*")) == []
    assert file.read_text() == "Changing source"
    assert library.open_default() == workspace


def test_move_does_not_use_nonempty_nested_or_program_destination(library, tmp_path):
    workspace = library.open_default()
    occupied = tmp_path / "already exists"
    occupied.mkdir()
    planted = occupied / "keep.md"
    planted.write_text("Keep")
    package = tmp_path / "Example.app"
    package.mkdir()
    for target in (occupied, workspace / "copy", workspace.parent / "copy", library.anchor,
                   package / "copy", Path("relative"), tmp_path / "missing parent" / "copy"):
        with pytest.raises(LibraryError, match="storage-destination"):
            library.preview(target)
    assert planted.read_text() == "Keep"


def test_failed_commit_keeps_original_registered_and_validated_copy(monkeypatch, library, tmp_path):
    workspace = library.open_default()
    target = tmp_path / "validated"
    preview = library.preview(target)
    save = desktop_library.publish_metadata

    def fail_pointer(file, document):
        if file.name == LOCATION_FILE:
            raise OSError("Simulate a local pointer publication failure")
        save(file, document)

    monkeypatch.setattr(desktop_library, "publish_metadata", fail_pointer)
    with pytest.raises(LibraryError, match="storage-unavailable"):
        library.move(target, preview["sha256"])
    assert library.open_default() == workspace
    assert library.status()["root"] == str(workspace.parent.parent)
    saved_copy = target / "Workspaces" / workspace.name
    assert workspace_registration(saved_copy) == workspace_registration(workspace)
    assert paths.workspace_id(saved_copy) == paths.workspace_id(workspace)
    with paths.use_workspace(workspace, strict=True):
        assert paths.active_workspace() == workspace


def test_missing_location_cannot_activate_retained_copy(library, tmp_path):
    workspace = library.open_default()
    target = tmp_path / "validated"
    library.move(target, library.preview(target)["sha256"])
    (library.anchor / LOCATION_FILE).unlink()
    with pytest.raises(LibraryError, match="storage-unavailable"), paths.use_workspace(workspace, strict=True):
        pytest.fail("A retained copy must remain inactive when the control pointer is missing.")
    with pytest.raises(LibraryError, match="storage-invalid"):
        library.open_default()
