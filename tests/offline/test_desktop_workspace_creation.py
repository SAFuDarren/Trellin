"""Check desktop folder selection and fresh database safety without a server."""

from contextlib import contextmanager, nullcontext
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest

from research_multiplier.storage import credentials, paths, postgres
from research_multiplier.workbench import desktop
from research_multiplier.workflows import desktop_workspace
from research_multiplier.workflows.setup import Setup, SetupError


pytestmark = pytest.mark.offline


def _launch(path, *, select=True):
    output = StringIO()
    result = desktop.run(str(path), BytesIO(), output, select=select)
    return result, json.loads(output.getvalue())


def _error(code):
    return 2, {"version": 1, "status": "error", "code": code}


def test_creation_refuses_existing_folders_aliases_and_nested_workspaces(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    occupied = root / "prior work"
    occupied.mkdir()
    original = occupied / "notes.txt"
    original.write_text("preserve me")
    alias = root / "alias"
    alias.symlink_to(occupied, target_is_directory=True)
    workspace = paths.initialize_workspace(root / "workspace")
    checkout = root / "source"
    (checkout / "research_multiplier").mkdir(parents=True)
    (checkout / "pyproject.toml").touch()
    (checkout / "research_multiplier" / "__main__.py").touch()

    def unexpected(_):
        pytest.fail("An invalid path reached PostgreSQL.")

    monkeypatch.setattr(desktop_workspace._DesktopSetup, "admin_connection", unexpected)
    for invalid in (occupied, alias, alias / "new", workspace / "new", checkout / "new", root / ".hidden",
                    root / "missing parent" / "new", root / "workspace" / ".." / "new", root / "bad:name",
                    Path("relative")):
        with pytest.raises(desktop_workspace.WorkspaceCreationError, match="^create-path$"):
            desktop_workspace.create_workspace(invalid)
    assert original.read_text() == "preserve me"
    assert list(occupied.iterdir()) == [original]
    assert not (workspace / "new").exists()


@pytest.mark.parametrize(("socket", "port"), [("localhost", "5432"), ("/tmp", "0"), ("/tmp", "65536"),
                                               ("/tmp,/elsewhere", "5432"), ("/tmp", " 5432")])
def test_creation_refuses_nonlocal_or_invalid_postgres_target(monkeypatch, tmp_path, socket, port):
    monkeypatch.setenv("RM_DESKTOP_PG_SOCKET", socket)
    monkeypatch.setenv("RM_DESKTOP_PG_PORT", port)
    parent = tmp_path.resolve()
    destination = parent / "Trellin"
    assert _launch(parent) == _error("create-postgres")
    assert not destination.exists()


def test_creation_preflights_exact_postgres_target_before_writing(monkeypatch, tmp_path):
    parent = tmp_path.resolve()
    socket = parent / "PostgreSQL Socket"
    socket.mkdir()
    monkeypatch.setenv("RM_DESKTOP_PG_SOCKET", str(socket))
    monkeypatch.setenv("RM_DESKTOP_PG_PORT", "55432")
    monkeypatch.setenv("PGHOSTADDR", "example.invalid")
    monkeypatch.setenv("RM_ADMIN_DSN", "service=other")
    monkeypatch.setenv("RM_HUMAN_DSN", "service=other-human")
    destination = parent / "Trellin"

    def connect(setup):
        assert (setup.host, setup.port) == (str(socket), 55432)
        assert not setup.interactive and not setup.assume_yes and setup.skip_lean
        assert all(key not in os.environ for key in ("PGHOSTADDR", "RM_ADMIN_DSN", "RM_HUMAN_DSN"))
        return nullcontext()

    def check(*_):
        assert not destination.exists()
        raise SetupError("Private server error")

    monkeypatch.setattr(desktop_workspace._DesktopSetup, "admin_connection", connect)
    monkeypatch.setattr(desktop_workspace._DesktopSetup, "check_server", check)
    assert _launch(parent) == _error("create-postgres")
    assert not destination.exists()


def test_selection_creates_once_and_opens_the_resolved_workspace(monkeypatch, tmp_path):
    parent = tmp_path.resolve() / "新课题"
    parent.mkdir()
    notes = parent / "research.md"
    notes.write_text("Existing research")
    destination = parent / "Trellin"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("RM_HUMAN_DSN", "service=other")
    values = {"RM_HUMAN_DSN": "service=new-human"}
    database_id = UUID(int=1234)
    provisions = []

    @contextmanager
    def fresh(setup, _):
        assert setup.workspace == destination and paths.workspace_id(destination)
        provisions.append(destination)
        yield values

    def bind(workspace):
        assert workspace == paths.active_workspace() == destination
        assert os.environ["RM_HUMAN_DSN"] == values["RM_HUMAN_DSN"]
        paths.bind_database(workspace, database_id)

    ready = {"version": 1, "status": "ready", "url": "local", "warnings": [], "workspace": str(destination)}
    server = SimpleNamespace(workspace=destination, launch_url="local", shutdown=lambda: None,
                             serve_forever=lambda **_: None)

    def open_bound(workspace, _):
        assert Path(workspace) == destination
        assert paths.database_id(workspace) == database_id
        assert credentials.read(paths.workspace_id(workspace)) == values
        return server

    monkeypatch.setattr(desktop_workspace._DesktopSetup, "admin_connection", lambda _: nullcontext())
    monkeypatch.setattr(desktop_workspace._DesktopSetup, "check_server", lambda *args: None)
    monkeypatch.setattr(desktop_workspace._DesktopSetup, "fresh_database", fresh)
    monkeypatch.setattr(postgres, "bind_workspace", bind)
    monkeypatch.setattr(desktop, "_open", open_bound)
    monkeypatch.setattr(desktop.shutil, "which", lambda _: "/installed/lean")
    assert _launch(parent) == (0, ready)
    assert _launch(parent) == (0, ready)
    assert _launch(destination) == (0, ready)
    ready.pop("workspace")
    assert _launch(destination, select=False) == (0, ready)
    assert provisions == [destination]
    assert notes.read_text() == "Existing research"
    assert (destination / "AGENTS.md").is_file()
    assert not (destination / "Trellin").exists()


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "broken symlink", "invalid workspace"])
def test_selection_refuses_an_occupied_trellin_child(monkeypatch, tmp_path, kind):
    parent = tmp_path / "research"
    parent.mkdir()
    child = parent / "Trellin"
    if kind == "file":
        child.write_text("Preserve this file")
    elif kind == "symlink":
        other_workspace = paths.initialize_workspace(tmp_path / "elsewhere")
        child.symlink_to(other_workspace, target_is_directory=True)
    elif kind == "broken symlink":
        child.symlink_to(tmp_path / "missing", target_is_directory=True)
    else:
        child.mkdir()
        (child / "notes.md").write_text("Preserve this directory")
        if kind == "invalid workspace":
            (child / paths.WORKSPACE_FILE).write_text("invalid marker")

    def unexpected(*_):
        pytest.fail("A blocked path attempted to open or create a workspace.")

    monkeypatch.setattr(desktop, "_open", unexpected)
    monkeypatch.setattr(desktop_workspace, "create_workspace", unexpected)
    before = {path.name for path in child.iterdir()} if child.is_dir() else None
    assert _launch(parent) == _error("workspace-occupied")
    if child.is_dir():
        assert {path.name for path in child.iterdir()} == before
    if kind == "file":
        assert child.read_text() == "Preserve this file"
    if kind.endswith("symlink"):
        assert child.is_symlink()


def test_unflagged_open_does_not_search_or_create_a_child(monkeypatch, tmp_path):
    parent = tmp_path / "research"
    child = paths.initialize_workspace(parent / "Trellin")
    other_parent = tmp_path / "other"
    other_parent.mkdir()

    def unexpected(*_):
        pytest.fail("A restored path entered the folder-selection workflow.")

    monkeypatch.setattr(desktop_workspace, "select_workspace", unexpected)
    assert _launch(parent, select=False) == _error("workspace")
    assert _launch(other_parent, select=False) == _error("workspace")
    assert not (other_parent / "Trellin").exists()
    assert (child / paths.WORKSPACE_FILE).is_file()


def test_fresh_database_never_reuses_collision_or_rolls_back_a_foreign_object(monkeypatch, tmp_path):
    # These SQL strings are inspected only up to the object name. No passwords
    # or database settings are emitted in failures.
    class FakeSQL(str):
        def format(self, *parameters):
            return FakeSQL(super().format(*parameters))

        def join(self, parameters):
            return FakeSQL(super().join(parameters))

    sql = SimpleNamespace(SQL=FakeSQL, Identifier=FakeSQL, Literal=lambda _: "REDACTED")
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(sql=sql))
    target = paths.initialize_workspace(tmp_path / "project")
    prefix = "rm_" + paths.workspace_id(target).hex[:12]
    for scenario in ("database", "role", "racing database"):
        writes = []

        def execute(query, parameters=None):
            if query.startswith("SELECT"):
                name, = parameters
                found = ((scenario == "database" and name == prefix)
                         or (scenario == "role" and name == prefix + "_agent")
                         or name in ("rm_human", "rm_scheduler", "rm_agent", "rm_verifier"))
                return SimpleNamespace(fetchone=lambda: (1,) if found else None)
            verb, kind, name, *_ = query.split()
            writes.append((verb, kind, name))
            if (verb, kind) == ("CREATE", "DATABASE"):
                raise SetupError("Another owner created the database")

        with pytest.raises(SetupError):
            with Setup(target).fresh_database(SimpleNamespace(execute=execute)):
                pytest.fail("This project identifier collides.")
        if scenario != "racing database":
            assert not writes
        else:
            created = {name for verb, kind, name in writes if (verb, kind) == ("CREATE", "ROLE")}
            removed = {name for verb, kind, name in writes if (verb, kind) == ("DROP", "ROLE")}
            assert len(created) == 5 and removed == created
            assert not any(verb == "ALTER" or (verb, kind) == ("DROP", "DATABASE") for verb, kind, _ in writes)
