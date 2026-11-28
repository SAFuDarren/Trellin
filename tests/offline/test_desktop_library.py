"""The default library and checked move must preserve project identity."""

from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from research_multiplier.cli import main
from research_multiplier.storage import paths
from research_multiplier.storage.managed_workspaces import LOCATION_FILE, MANAGED_FILE, LibraryError, workspace_registration
from research_multiplier.workbench import desktop
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
    ] == [("My research", str(default), True), ("legacy", str(legacy), False), ("Other theorem", str(named), True)]
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


def test_sidebar_preflight_rejects_a_reused_registered_path(library, tmp_path, monkeypatch, capsys):
    external = paths.initialize_workspace(tmp_path / "external")
    paths.bind_database(external, uuid4())
    registered = library.register_external(external)
    monkeypatch.setattr(sys, "argv", ["desktop", "--storage-resolve", registered["id"]])
    assert desktop.main() == 0
    assert json.loads(capsys.readouterr().out) == {"version": 1, "status": "ready", "workspace": str(external)}

    with monkeypatch.context() as unavailable:
        def offline(_path, _row):
            raise LibraryError("storage-database")

        unavailable.setattr(desktop_library, "_check_binding", offline)
        assert desktop.main() == 2
        assert json.loads(capsys.readouterr().out) == {"version": 1, "status": "error", "code": "storage-database"}

    external.rename(tmp_path / "moved")
    replacement = paths.initialize_workspace(external)
    paths.bind_database(replacement, uuid4())
    assert desktop.main() == 2
    assert json.loads(capsys.readouterr().out) == {"version": 1, "status": "error", "code": "storage-verification"}


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


def test_initialized_status_preview_and_describe_share_active_workspace_lease(library, tmp_path):
    workspace = library.open_default()
    target = tmp_path / "moved"
    with paths.use_workspace(workspace, strict=True):
        state = library.status()
        assert state["projects"] == [library.describe(workspace) | {"default": True, "hidden": False}]
        assert library.preview(target)["project_count"] == 1


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


def test_a_project_from_another_library_home_opens_a_new_default_library(monkeypatch, library, tmp_path):
    # #285: a trial build with the same bundle identifier saved a project of its own library home.
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "trial home"))
    other = desktop_library.Library().open_default()
    monkeypatch.setenv("RM_TRELLIN_HOME", str(library.anchor))
    assert not library.anchor.exists()

    opened = []

    class Server:
        launch_url = "test"

        def __init__(self, workspace):
            self.workspace = Path(workspace)

        def serve_forever(self, *, poll_interval):
            pass

        def shutdown(self):
            pass

    def open_project(path, _resources, *, expected_project):
        opened.append(Path(path))
        return Server(path)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "credentials"))
    monkeypatch.setattr(desktop, "_open", open_project)
    monkeypatch.setattr(desktop.shutil, "which", lambda _: "/fixture/lean")
    failures = []
    for launch in ({"workspace_path": str(other)}, {"workspace_path": None, "project_id": paths.workspace_id(other)}):
        output = StringIO()
        assert desktop.run(lifetime=BytesIO(), output=output, library=desktop_library.Library(), **launch) == 0
        reply = json.loads(output.getvalue())
        default = Path(desktop_library.Library().status()["projects"][0]["path"])
        assert default.parent == library.anchor / "Library" / "Workspaces"
        assert (reply["workspace"], reply["name"]) == (str(default), "My research")
        failures.append(reply["reopen_failed"])
    # First launch: the library did not exist yet. Second: it exists but does not hold that project.
    assert failures == ["storage-unavailable", "storage-project"]
    assert other not in opened
    # The trial library is left as it was.
    assert desktop_library.Library(tmp_path / "trial home").status()["projects"][0]["path"] == str(other)


# #302: the default project has a name of its own, leaves the switcher while empty, and projects can be
# renamed or removed from the list without touching their folders.

def _registry(library):
    return json.loads((library.anchor / "Library" / "registry.json").read_text())


def test_an_older_default_named_library_is_renamed_on_the_next_launch(library):
    default = library.open_default()
    registry = _registry(library)
    registry["projects"][0]["name"] = "Library"
    (library.anchor / "Library" / "registry.json").write_text(json.dumps(registry))
    assert [row["name"] for row in library.status()["projects"]] == ["My research"]
    assert _registry(library)["projects"][0]["name"] == "My research"
    # A researcher's own project named Library keeps its name.
    library.create("Library")
    assert [row["name"] for row in library.status()["projects"]] == ["My research", "Library"]
    assert library.open_default() == default


def test_the_empty_default_is_hidden_only_once_another_project_exists(library, tmp_path, monkeypatch):
    monkeypatch.setattr(desktop_library, "_has_claims", lambda _row: False)
    default = library.open_default()
    assert [row["hidden"] for row in library.status()["projects"]] == [False]
    library.create("Own research")
    state = library.status()
    assert [(row["name"], row["default"], row["hidden"]) for row in state["projects"]] == [
        ("My research", True, True), ("Own research", False, False),
    ]
    # Inputs or claims make the default a project of its own again.
    (default / "inputs").mkdir()
    (default / "inputs" / "notes.md").write_text("Notes")
    assert library.status()["projects"][0]["hidden"] is False
    (default / "inputs" / "notes.md").unlink()
    monkeypatch.setattr(desktop_library, "_has_claims", lambda _row: True)
    assert library.status()["projects"][0]["hidden"] is False
    # It stays the fallback either way.
    assert library.open_default() == default


def test_an_unreadable_database_keeps_the_default_listed(library, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no logins"))
    library.open_default()
    library.create("Own research")
    # No saved login for this project: the claim check cannot answer, so nothing is hidden.
    assert [row["hidden"] for row in library.status()["projects"]] == [False, False]


def test_projects_are_renamed_and_removed_without_touching_their_folders(library, tmp_path):
    default = library.open_default()
    external = paths.initialize_workspace(tmp_path / "external")
    paths.bind_database(external, uuid4())
    outside = library.register_external(external)
    managed = library.create("Draft")
    managed_id = str(paths.workspace_id(managed))

    assert library.rename(outside["id"], "  Field notes ")["name"] == "Field notes"
    with pytest.raises(LibraryError, match="storage-project"):
        library.rename(outside["id"], " ")
    with pytest.raises(LibraryError, match="storage-project"):
        library.rename(str(uuid4()), "Missing")

    with pytest.raises(LibraryError, match="storage-project"):
        library.unregister(str(paths.workspace_id(default)))
    assert library.unregister(outside["id"])["name"] == "Field notes"
    assert library.unregister(managed_id)["name"] == "Draft"
    assert [row["name"] for row in library.status()["projects"]] == ["My research"]
    assert external.is_dir() and paths.workspace_id(external) is not None
    assert managed.is_dir() and workspace_registration(managed) is not None

    # Opening a removed folder again puts it back in the list: the managed one under its last name.
    assert library.resolve_existing(managed) == managed
    assert library.register_external(external)["name"] == "external"
    assert [row["name"] for row in library.status()["projects"]] == ["My research", "Draft", "external"]
    assert library.unregister(managed_id)["name"] == "Draft"
    library.resolve_existing(managed)
    assert not json.loads((library.anchor / "Library" / "removed.json").read_text())["projects"]


def test_storage_cli_renames_and_removes(library, monkeypatch, capsys):
    library.open_default()
    project = str(paths.workspace_id(library.create("Draft")))
    monkeypatch.setattr(sys, "argv", ["desktop", "--storage-rename", project, "Thesis"])
    assert desktop.main() == 0
    assert json.loads(capsys.readouterr().out)["project"]["name"] == "Thesis"
    monkeypatch.setattr(sys, "argv", ["desktop", "--storage-remove", project])
    assert desktop.main() == 0
    assert json.loads(capsys.readouterr().out)["project"]["id"] == project
    monkeypatch.setattr(sys, "argv", ["desktop", "--storage-remove", project])
    assert desktop.main() == 2
    assert json.loads(capsys.readouterr().out) == {"version": 1, "status": "error", "code": "storage-project"}


def _stub_desktop_open(monkeypatch, tmp_path):
    opened = []

    class Server:
        launch_url = "test"

        def __init__(self, workspace):
            self.workspace = Path(workspace)

        def serve_forever(self, *, poll_interval):
            pass

        def shutdown(self):
            pass

    def open_project(path, _resources, *, expected_project):
        opened.append(Path(path))
        return Server(path)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "credentials"))
    monkeypatch.setattr(desktop, "_open", open_project)
    monkeypatch.setattr(desktop.shutil, "which", lambda _: "/fixture/lean")
    return opened


def test_deleted_registry_and_lock_file_rebuild_all_managed_projects(library, tmp_path):
    default = library.open_default()
    second = library.create("Second theorem")
    renamed = library.create("Initial title")
    library.rename(str(paths.workspace_id(renamed)), "Field notes")
    removed = library.create("Unregistered draft")
    library.unregister(str(paths.workspace_id(removed)))

    # Leave an incomplete folder without a managed marker; repair must preserve it untouched.
    root = library.anchor / "Library"
    incomplete = root / "Workspaces" / str(uuid4())
    incomplete.mkdir()
    (incomplete / "keep.txt").write_text("Pending")

    (root / "registry.json").unlink()
    (library.anchor / ".library.lock").unlink()

    rebuilt = library.status()
    assert rebuilt["default_id"] == str(paths.workspace_id(default))
    assert {(row["id"], row["name"], row["path"], row["default"]) for row in rebuilt["projects"]} == {
        (str(paths.workspace_id(default)), "My research", str(default), True),
        (str(paths.workspace_id(second)), "Second theorem", str(second), False),
        (str(paths.workspace_id(renamed)), "Field notes", str(renamed), False),
    }
    assert library.open_default() == default
    assert library.resolve_project(str(paths.workspace_id(second))) == second
    assert (incomplete / "keep.txt").read_text() == "Pending"


def _select_folder(library, folder):
    output = StringIO()
    code = desktop.run(str(folder), BytesIO(), output, select=True, library=library)
    return code, json.loads(output.getvalue())


def test_native_folder_selection_binds_existing_files_without_changing_them(monkeypatch, library, tmp_path):
    opened = _stub_desktop_open(monkeypatch, tmp_path)
    source = tmp_path / "research"
    child = source / "Trellin"
    child.mkdir(parents=True)
    notes = source / "notes.md"
    notes.write_bytes(b"Original research notes")
    report = child / "draft.txt"
    report.write_bytes(b"User-owned folder")
    before = {path.relative_to(source): path.lstat().st_mode for path in source.rglob("*")}
    alias = tmp_path / "source alias"
    alias.symlink_to(source, target_is_directory=True)

    code, first = _select_folder(library, source)
    assert code == 0 and first["status"] == "ready"
    workspace = Path(first["workspace"])
    assert workspace.parent == library.anchor / "Library" / "Workspaces"
    assert first["project_id"] == str(paths.workspace_id(workspace))
    assert paths.database_id(workspace) is not None
    assert _select_folder(desktop_library.Library(), alias) == (0, first)

    output = StringIO()
    assert desktop.run(None, BytesIO(), output, library=desktop_library.Library(),
                       project_id=paths.workspace_id(workspace)) == 0
    assert json.loads(output.getvalue()) == first
    assert opened == [workspace, workspace, workspace]
    assert [project["id"] for project in library.status()["projects"]] == [first["project_id"]]
    assert {path.relative_to(source): path.lstat().st_mode for path in source.rglob("*")} == before
    assert notes.read_bytes() == b"Original research notes"
    assert report.read_bytes() == b"User-owned folder"


def test_native_folder_selection_reuses_initialized_legacy_workspace(monkeypatch, library, tmp_path):
    opened = _stub_desktop_open(monkeypatch, tmp_path)
    workspace = paths.initialize_workspace(tmp_path / "old research" / "Trellin")
    database = uuid4()
    paths.bind_database(workspace, database)

    code, first = _select_folder(library, workspace.parent)
    assert code == 0 and first["workspace"] == str(workspace)
    assert first["project_id"] == str(paths.workspace_id(workspace))
    assert _select_folder(desktop_library.Library(), workspace) == (0, first)
    assert opened == [workspace, workspace]
    assert paths.database_id(workspace) == database
    assert not (workspace / MANAGED_FILE).exists()
    assert not list((library.anchor / "Library" / "Workspaces").iterdir())


def test_linked_folder_binding_survives_library_lifecycle(library, tmp_path):
    source = tmp_path / "research"
    source.mkdir()
    workspace = library.open_folder(source)
    identifier = str(paths.workspace_id(workspace))
    database = paths.database_id(workspace)
    root = workspace.parent.parent
    library.rename(identifier, "Renamed research")
    library.unregister(identifier)
    assert not library.status()["projects"]
    assert desktop_library.Library().open_folder(source) == workspace

    (root / "registry.json").unlink()
    recovered = desktop_library.Library().status()
    assert recovered["default_id"] is None
    assert [(row["id"], row["name"]) for row in recovered["projects"]] == [(identifier, "Renamed research")]
    assert library.open_folder(source) == workspace

    target = tmp_path / "relocated library"
    library.move(target, library.preview(target)["sha256"])
    relocated = target / "Workspaces" / workspace.name
    assert desktop_library.Library().open_folder(source) == relocated
    assert desktop_library.Library().resolve_project(identifier) == relocated
    assert paths.database_id(relocated) == database
    assert not list(source.iterdir())


def test_linked_folder_reuses_workspace_after_interrupted_registry_publication(monkeypatch, library, tmp_path):
    source = tmp_path / "research"
    source.mkdir()
    publish = desktop_library.publish_metadata
    root = library.anchor / "Library"
    interrupted = []

    def interrupt(path, document):
        if path == root / "registry.json" and document["projects"] and not interrupted:
            (workspace,) = (root / "Workspaces").iterdir()
            assert workspace_registration(workspace) is not None
            interrupted.append(workspace)
            raise OSError("Interrupted registry write")
        return publish(path, document)

    monkeypatch.setattr(desktop_library, "publish_metadata", interrupt)
    with pytest.raises(LibraryError, match="storage-unavailable"):
        library.open_folder(source)
    (workspace,) = interrupted
    assert desktop_library.Library().open_folder(source) == workspace
    assert list((root / "Workspaces").iterdir()) == [workspace]
    assert [row["id"] for row in library.status()["projects"]] == [str(paths.workspace_id(workspace))]
    assert not list(source.iterdir())


def test_folder_selection_does_not_initialize_library_inside_source(monkeypatch, library, tmp_path):
    opened = _stub_desktop_open(monkeypatch, tmp_path)
    source = library.anchor.parent
    assert not library.anchor.exists()

    assert _select_folder(library, source) == (
        2, {"version": 1, "status": "error", "code": "storage-destination"},
    )
    assert not library.anchor.exists()
    assert not opened


def test_deleted_registry_rebuilds_legacy_three_field_markers(library):
    default = library.open_default()
    other = library.create("Legacy named")
    for workspace in (default, other):
        marker_file = workspace / MANAGED_FILE
        marker = json.loads(marker_file.read_text())
        marker_file.write_text(json.dumps({
            "format_version": 1, "anchor": marker["anchor"], "slot": marker["slot"],
        }))
    (library.anchor / "Library" / "registry.json").unlink()

    state = library.status()
    assert len(state["projects"]) == 2
    assert {row["id"] for row in state["projects"]} == {str(paths.workspace_id(default)), str(paths.workspace_id(other))}
    assert state["default_id"] in {str(paths.workspace_id(default)), str(paths.workspace_id(other))}


def test_last_project_and_position_restore_from_library_and_migrate_legacy_once(monkeypatch, library, tmp_path):
    opened = _stub_desktop_open(monkeypatch, tmp_path)
    default = library.open_default()
    second = library.create("Second theorem")
    second_id = str(paths.workspace_id(second))
    claim_id = str(uuid4())
    node_id = f"goal:{claim_id}"

    # Opening second project records it in library settings; saving position persists by project ID.
    output = StringIO()
    assert desktop.run(None, BytesIO(), output, library=library, project_id=paths.workspace_id(second)) == 0
    reply = json.loads(output.getvalue())
    assert reply["project_id"] == second_id
    assert "position" not in reply

    saved_output = StringIO()
    assert desktop._storage(library, ["--storage-save-position", second_id, claim_id, node_id], saved_output) == 0
    assert json.loads(saved_output.getvalue())["position"] == {"claimId": claim_id, "nodeId": node_id}

    # Reopen restores from the library's own settings and ignores any legacy macOS preference payload.
    output = StringIO()
    legacy = {"path": str(default), "projectId": str(paths.workspace_id(default))}
    assert desktop.run(None, BytesIO(), output, library=library, reopen=True, legacy=legacy) == 0
    reopened = json.loads(output.getvalue())
    assert reopened["workspace"] == str(second)
    assert reopened["project_id"] == second_id
    assert reopened["position"] == {"claimId": claim_id, "nodeId": node_id}
    assert "reopen_failed" not in reopened

    # Corrupt settings never block startup: Trellin opens the default project cleanly.
    (library.anchor / "Library" / "settings.json").write_text("{corrupt")
    output = StringIO()
    assert desktop.run(None, BytesIO(), output, library=library, reopen=True) == 0
    recovered = json.loads(output.getvalue())
    assert recovered["workspace"] == str(default)
    assert "reopen_failed" not in recovered
    assert opened[-1] == default


def test_legacy_migration_from_another_library_home_creates_default_library(monkeypatch, library, tmp_path):
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "trial home"))
    other = desktop_library.Library().open_default()
    monkeypatch.setenv("RM_TRELLIN_HOME", str(library.anchor))
    assert not library.anchor.exists()

    opened = _stub_desktop_open(monkeypatch, tmp_path)
    legacy = {"path": str(other), "projectId": str(paths.workspace_id(other))}
    output = StringIO()
    assert desktop.run(None, BytesIO(), output, library=desktop_library.Library(), reopen=True, legacy=legacy) == 0
    reply = json.loads(output.getvalue())
    default = Path(desktop_library.Library().status()["projects"][0]["path"])
    assert reply["workspace"] == str(default)
    assert reply["reopen_failed"] == "storage-project"
    assert other not in opened


def test_move_data_repairs_deleted_registry_and_restores_last_project_at_new_location(monkeypatch, library, tmp_path):
    _stub_desktop_open(monkeypatch, tmp_path)
    default = library.open_default()
    second = library.create("Moved study")
    second_id = str(paths.workspace_id(second))
    claim_id = str(uuid4())
    node_id = f"obligation:{claim_id}:1"

    output = StringIO()
    assert desktop.run(None, BytesIO(), output, library=library, project_id=paths.workspace_id(second)) == 0
    library.save_position(second_id, claim_id, node_id)

    destination = tmp_path / "other disk" / "Trellin Library"
    destination.parent.mkdir()
    preview = library.preview(destination)
    library.move(destination, preview["sha256"])

    # Delete registry and lock file at the new location after Move Data.
    (destination / "registry.json").unlink()
    (library.anchor / ".library.lock").unlink()

    output = StringIO()
    assert desktop.run(None, BytesIO(), output, library=desktop_library.Library(), reopen=True) == 0
    reply = json.loads(output.getvalue())
    expected_workspace = destination / "Workspaces" / second.name
    assert reply["workspace"] == str(expected_workspace)
    assert reply["name"] == "Moved study"
    assert reply["project_id"] == second_id
    assert reply["position"] == {"claimId": claim_id, "nodeId": node_id}
    assert "reopen_failed" not in reply

    state = desktop_library.Library().status()
    assert state["root"] == str(destination)
    assert {row["name"] for row in state["projects"]} == {"My research", "Moved study"}
    assert library.open_default() == destination / "Workspaces" / default.name


def test_build_script_uses_distinct_bundle_identifiers_for_dev_and_trial():
    script = Path(__file__).resolve().parents[2] / "desktop" / "macos" / "build.sh"
    assert subprocess.check_output(["bash", str(script), "--print-bundle-id"], text=True).strip() == (
        "org.researchmultiplier.trellin.dev"
    )
    assert subprocess.check_output(["bash", str(script), "--dev", "--print-bundle-id"], text=True).strip() == (
        "org.researchmultiplier.trellin.dev"
    )
    assert subprocess.check_output(["bash", str(script), "--trial", "--print-bundle-id"], text=True).strip() == (
        "org.researchmultiplier.trellin.trial"
    )
    assert subprocess.check_output(["bash", str(script), "--release", "--print-bundle-id"], text=True).strip() == (
        "org.researchmultiplier.trellin"
    )
    assert subprocess.check_output(
        ["bash", str(script), "--bundle-id", "org.researchmultiplier.trellin.review", "--print-bundle-id"], text=True,
    ).strip() == "org.researchmultiplier.trellin.review"
    invalid = subprocess.run(["bash", str(script), "--bundle-id", "bad id", "--print-bundle-id"], capture_output=True)
    assert invalid.returncode != 0
