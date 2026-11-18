"""Check the desktop-only new-project entry without a database or existing project."""

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


def _create(path):
    output = StringIO()
    result = desktop.run(str(path), BytesIO(), output, create=True)
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
        assert _create(invalid) == _error("create-path")
    assert original.read_text() == "preserve me"
    assert list(occupied.iterdir()) == [original]
    assert not (workspace / "new").exists()


@pytest.mark.parametrize(("socket", "port"), [("localhost", "5432"), ("/tmp", "0"), ("/tmp", "65536"),
                                               ("/tmp,/elsewhere", "5432"), ("/tmp", " 5432")])
def test_creation_refuses_nonlocal_or_invalid_postgres_target(monkeypatch, tmp_path, socket, port):
    monkeypatch.setenv("RM_DESKTOP_PG_SOCKET", socket)
    monkeypatch.setenv("RM_DESKTOP_PG_PORT", port)
    destination = tmp_path.resolve() / "new"
    assert _create(destination) == _error("create-postgres")
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
    destination = parent / "new"

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
    assert _create(destination) == _error("create-postgres")
    assert not destination.exists()


def test_creation_binds_saves_and_then_uses_the_normal_workbench(monkeypatch, tmp_path):
    destination = tmp_path.resolve() / "新课题"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("RM_HUMAN_DSN", "service=other")
    values = {"RM_HUMAN_DSN": "service=new-human"}
    database_id = UUID(int=1234)

    @contextmanager
    def fresh(setup, _):
        assert setup.workspace == destination and paths.workspace_id(destination)
        yield values

    def bind(workspace):
        assert workspace == paths.active_workspace() == destination
        assert os.environ["RM_HUMAN_DSN"] == values["RM_HUMAN_DSN"]
        paths.bind_database(workspace, database_id)

    output = StringIO()
    ready = {"version": 1, "status": "ready", "url": "local", "warnings": []}
    server = SimpleNamespace(launch_url="local", shutdown=lambda: None, serve_forever=lambda **_: None)

    def open_bound(workspace, _):
        assert workspace == destination
        assert paths.database_id(workspace) == database_id
        assert credentials.read(paths.workspace_id(workspace)) == values
        return server

    monkeypatch.setattr(desktop_workspace._DesktopSetup, "admin_connection", lambda _: nullcontext())
    monkeypatch.setattr(desktop_workspace._DesktopSetup, "check_server", lambda *args: None)
    monkeypatch.setattr(desktop_workspace._DesktopSetup, "fresh_database", fresh)
    monkeypatch.setattr(postgres, "bind_workspace", bind)
    monkeypatch.setattr(desktop, "_open", open_bound)
    monkeypatch.setattr(desktop.shutil, "which", lambda _: "/installed/lean")
    assert desktop.run(str(destination), BytesIO(), output, create=True) == 0
    assert json.loads(output.getvalue()) == ready
    assert (destination / "AGENTS.md").is_file()


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
