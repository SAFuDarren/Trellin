"""Verify Trellin's startup protocol and child-process lifetime."""

from contextlib import contextmanager
from hashlib import sha256
from http.client import HTTPConnection
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
from queue import SimpleQueue
import sys
from threading import Event, Thread
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from research_multiplier.storage import credentials, paths
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workbench import desktop
from research_multiplier.workbench.server import _MissingSchema
from research_multiplier.workflows import desktop_library, desktop_source, desktop_workspace
from research_multiplier.workflows.desktop_library import LibraryError


pytestmark = pytest.mark.offline


def _startup(workspace):
    output = StringIO()
    code = desktop.run(str(workspace), BytesIO(), output)
    return code, json.loads(output.getvalue())


@pytest.fixture
def bound_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "credentials"))
    for key in credentials.KEYS:
        monkeypatch.setenv(key, "")
    workspace = paths.initialize_workspace(tmp_path / "workspace")
    paths.bind_database(workspace, uuid4())
    return workspace


def _save_login(workspace, **values):
    return credentials.write(paths.workspace_id(workspace), values)


def _unexpected_start(*_):
    pytest.fail("Desktop started a workbench before checking its project and saved logins.")


def test_invalid_workspace_does_not_fall_back_or_create_files(monkeypatch, tmp_path):
    initialized = paths.initialize_workspace(tmp_path / "configured")
    monkeypatch.setenv("RM_DATA_ROOT", str(initialized))
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    assert _startup(chosen) == (2, {"version": 1, "status": "error", "code": "workspace"})
    assert _startup(Path("relative")) == (2, {"version": 1, "status": "error", "code": "workspace"})
    assert list(chosen.iterdir()) == []


@pytest.mark.parametrize("bad_marker", [None, "decommissioned"])
def test_unbound_workspace_is_reported_without_writing_files(monkeypatch, tmp_path, bad_marker):
    workspace = paths.initialize_workspace(tmp_path / "workspace")
    marker = workspace / paths.DATABASE_FILE
    if bad_marker is not None:
        marker.write_text(bad_marker, encoding="ascii")
    original = set(workspace.iterdir())
    monkeypatch.setattr(desktop, "WorkbenchServer", _unexpected_start)

    assert _startup(workspace) == (2, {"version": 1, "status": "error", "code": "unbound"})
    assert set(workspace.iterdir()) == original
    assert (marker.read_text(encoding="ascii") if marker.exists() else None) == bad_marker


@pytest.mark.parametrize("saved_worker_only", [False, True])
def test_missing_saved_login_does_not_use_inherited_connection(monkeypatch, bound_workspace, saved_worker_only):
    monkeypatch.setenv("RM_HUMAN_DSN", "service=another-workspace")
    monkeypatch.setattr(desktop, "WorkbenchServer", _unexpected_start)
    location = credentials.location(paths.workspace_id(bound_workspace))
    if saved_worker_only:
        _save_login(bound_workspace, RM_AGENT_DSN="service=selected-agent")
    original = location.read_bytes() if location.exists() else None

    assert _startup(bound_workspace) == (2, {"version": 1, "status": "error", "code": "login-missing"})
    assert (location.read_bytes() if location.exists() else None) == original


def test_invalid_saved_login_is_redacted_and_not_rewritten(monkeypatch, bound_workspace):
    monkeypatch.setattr(desktop, "WorkbenchServer", _unexpected_start)
    location = _save_login(bound_workspace, RM_HUMAN_DSN="service=workspace-human")
    original = b"Malformed private connection settings."
    location.write_bytes(original)

    assert _startup(bound_workspace) == (2, {"version": 1, "status": "error", "code": "login-invalid"})
    assert location.read_bytes() == original


@pytest.mark.parametrize(("error", "code"), [
    (ValueError("private login details"), "postgres"),
    (DatabaseOperationError("human", "28P01", "private authentication detail"), "postgres"),
    (DatabaseOperationError("human", "42883", "private schema details"), "schema"),
    (_MissingSchema("private schema details"), "schema"),
    (RuntimeError("unexpected private details"), "startup"),
])
def test_startup_errors_send_only_a_fixed_code(monkeypatch, bound_workspace, error, code):
    _save_login(bound_workspace, RM_HUMAN_DSN="service=workspace-human")

    def fail_read(*_):
        raise error

    monkeypatch.setattr(desktop.WorkbenchServer, "read", fail_read)
    assert _startup(bound_workspace) == (2, {"version": 1, "status": "error", "code": code})


def test_missing_assets_does_not_start_a_session(monkeypatch, bound_workspace):
    _save_login(bound_workspace, RM_HUMAN_DSN="service=workspace-human")
    monkeypatch.setattr("research_multiplier.workbench.server._app_assets", dict)
    assert _startup(bound_workspace) == (2, {"version": 1, "status": "error", "code": "install"})


def _pipe():
    reader, writer = os.pipe()
    return os.fdopen(reader, "rb"), os.fdopen(writer, "wb")


def _read_ready(stream):
    lines = SimpleQueue()
    Thread(target=lambda: lines.put(stream.readline()), daemon=True).start()
    return json.loads(lines.get(timeout=5))


def _request(port, route, session=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        headers = {} if session is None else {"X-RM-Session": session}
        connection.request("GET", route, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_local_session_uses_selected_workspace_and_drains_on_parent_disconnect(monkeypatch, bound_workspace):
    workspace = bound_workspace
    _save_login(workspace, RM_HUMAN_DSN="service=workspace-human")
    monkeypatch.setenv("RM_HUMAN_DSN", "service=another-workspace")
    monkeypatch.setenv("RM_AGENT_DSN", "service=another-workspace-agent")
    started = Event()
    release = Event()

    def read(server, action):
        assert server.workspace == workspace
        assert os.environ["RM_HUMAN_DSN"] == "service=workspace-human"
        assert "RM_AGENT_DSN" not in os.environ
        if action is desktop._check_schema:
            return None
        started.set()
        assert release.wait(5)
        return {"claims": []}

    monkeypatch.setattr(desktop.shutil, "which", lambda _: None)
    monkeypatch.setattr(desktop.WorkbenchServer, "read", read)
    lifetime, parent = _pipe()
    replies, output = _pipe()
    completed = []

    with lifetime, parent, replies, output, open(output.fileno(), "w", closefd=False) as text_output:
        process = Thread(
            target=lambda: completed.append(desktop.run(str(workspace), lifetime, text_output)),
            daemon=True,
        )
        process.start()
        try:
            reply = _read_ready(replies)
            url = urlsplit(reply.pop("url"))
            assert reply == {"version": 1, "status": "ready", "warnings": ["lean"]}
            assert (url.scheme, url.hostname, url.path) == ("http", "127.0.0.1", "/")
            assert _request(url.port, "/")[0] == 200
            assert _request(url.port, "/api/claims")[0] == 403
            token = parse_qs(url.fragment)["session"][0]
            response = []
            request = Thread(target=lambda: response.append(_request(url.port, "/api/claims", token)), daemon=True)
            request.start()
            assert started.wait(5)

            parent.close()
            process.join(timeout=0.5)
            assert process.is_alive(), "Trellin ended while a request was still active"
            release.set()
            request.join(timeout=5)
            assert response and response[0][0] == 200
            process.join(timeout=5)
            assert completed == [0]
        finally:
            release.set()
            parent.close()
            process.join(timeout=5)


@pytest.fixture
def observation_library(monkeypatch, tmp_path):
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "app support"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "credentials"))
    for key in credentials.KEYS:
        monkeypatch.setenv(key, "")

    def create(workspace, *, minimal=False):
        assert minimal
        workspace = paths.initialize_workspace(workspace, minimal=minimal)
        paths.bind_database(workspace, uuid4())
        _save_login(workspace, RM_HUMAN_DSN="service=fixture-human")
        return workspace

    def read(server, action):
        assert action is desktop._check_schema
        assert paths.database_id(server.workspace) is not None
        assert os.environ["RM_HUMAN_DSN"] == "service=fixture-human"

    monkeypatch.setattr(desktop_library, "create_workspace", create)
    monkeypatch.setattr(desktop.WorkbenchServer, "read", read)
    return desktop_library.Library()


@contextmanager
def _desktop_http_session(project):
    lifetime, parent = _pipe()
    replies, output = _pipe()
    completed = []
    with lifetime, parent, replies, output, open(output.fileno(), "w", closefd=False) as text_output:
        process = Thread(target=lambda: completed.append(
            desktop.run(None, lifetime, text_output, library=desktop_library.Library(), project_id=project),
        ), daemon=True)
        process.start()
        try:
            reply = _read_ready(replies)
            assert reply["status"] == "ready" and reply["project_id"] == str(project)
            url = urlsplit(reply["url"])
            yield url.port, parse_qs(url.fragment)["session"][0]
        finally:
            parent.close()
            process.join(timeout=5)
    assert not process.is_alive(), "Trellin did not stop after native launcher disconnect."
    assert completed == [0]


_SOURCE_OBSERVATIONS = "/api/source-observations"


def _wait_for_source_observation(port, session, ready):
    deadline = monotonic() + 8
    while True:
        code, body = _request(port, _SOURCE_OBSERVATIONS, session)
        assert code == 200
        result = json.loads(body)
        if ready(result):
            return result
        if monotonic() >= deadline:
            pytest.fail(f"The workbench did not observe the expected source state: {result}")
        sleep(0.05)


def test_linked_source_observation_tracks_native_session_and_recovers_closed_edits(
    monkeypatch, observation_library, tmp_path,
):
    source = tmp_path / "research"
    source.mkdir()
    paper = source / "paper.md"
    notes = source / "untracked.md"
    initial = b"Accepted paper"
    paper.write_bytes(initial)
    paper.chmod(0o640)
    notes.write_bytes(b"Untracked notes")
    workspace = observation_library.open_folder(source)
    project = paths.workspace_id(workspace)
    binding = observation_library.source_info(str(project))
    expected_digest = sha256(initial).hexdigest()
    tracked = observation_library.track_source_reference(
        str(project), paper.name, expected_revision=binding["revision"],
    )["reference"]
    assert tracked["sha256"] == expected_digest
    initial_inode = paper.stat().st_ino
    approved_root_inode = source.stat().st_ino
    hash_file = desktop_source._hash_file
    open_file = desktop_source._regular_file
    run_observer = desktop.SourceObserver._run
    digests = []
    opened = set()
    observed = Event()
    stopped = SimpleQueue()

    def record_digest(descriptor, size):
        digest = hash_file(descriptor, size)
        digests.append(os.fstat(descriptor).st_ino)
        return digest

    def record_open(root, path):
        opened.add((os.fstat(root).st_ino, path.as_posix()))
        observed.set()
        return open_file(root, path)

    def record_observer_stop(observer):
        try:
            run_observer(observer)
        finally:
            stopped.put(observer)

    def atomically_save(contents):
        stage = paper.with_suffix(".tmp")
        stage.write_bytes(contents)
        stage.chmod(0o640)
        stage.replace(paper)

    def observed_file(port, session, status, contents):
        expected = {"reference_id": tracked["id"], "recorded_sha256": expected_digest, "status": status,
                    "sha256": sha256(contents).hexdigest()}
        result = _wait_for_source_observation(port, session, lambda reply: reply["files"] == [expected])
        assert set(result) == {"source_id", "revision", "status", "files", "generation"}
        assert (result["source_id"], result["revision"], result["status"]) == (
            str(project), binding["revision"], "available",
        )
        assert isinstance(result["generation"], int)
        return result

    monkeypatch.setattr(desktop_source, "_hash_file", record_digest)
    monkeypatch.setattr(desktop_source, "_regular_file", record_open)
    monkeypatch.setattr(desktop.SourceObserver, "_run", record_observer_stop)
    changed = b"Saved while the workbench was running"
    with _desktop_http_session(project) as (port, session):
        assert _request(port, _SOURCE_OBSERVATIONS)[0] == 403
        baseline = observed_file(port, session, "unchanged", initial)
        notes.write_bytes(b"An unrelated change")
        observed.clear()
        assert observed.wait(5), "The active observer did not run after an unrelated edit."
        assert observed_file(port, session, "unchanged", initial) == baseline
        assert digests == [initial_inode]
        atomically_save(changed)
        saved_inode = paper.stat().st_ino
        update = observed_file(port, session, "changed", changed)
        assert update["generation"] > baseline["generation"]
        assert digests == [initial_inode, saved_inode]
        assert sorted(entry.name for entry in source.iterdir()) == [paper.name, notes.name]
        assert paper.read_bytes() == changed and paper.stat().st_mode & 0o777 == 0o640
        assert notes.read_bytes() == b"An unrelated change"

        displaced = source.rename(tmp_path / "displaced")
        source.mkdir()
        paper.write_bytes(b"Another project at the old address")
        blocked = _wait_for_source_observation(port, session, lambda reply: reply["status"] == "identity_changed")
        assert blocked["generation"] > update["generation"]
        assert blocked["files"] == []
        assert digests == [initial_inode, saved_inode]
        assert paper.read_bytes() == b"Another project at the old address"

    first_observer = stopped.get(timeout=2)
    source.rename(tmp_path / "replacement")
    displaced.rename(source)
    saved_offline = b"Saved while the workbench was closed"
    atomically_save(saved_offline)
    offline_inode = paper.stat().st_ino
    assert digests == [initial_inode, saved_inode]
    with _desktop_http_session(project) as (port, session):
        observed_file(port, session, "changed", saved_offline)
        assert digests == [initial_inode, saved_inode, offline_inode]
    assert stopped.get(timeout=2) is not first_observer
    assert opened == {(approved_root_inode, paper.name)}
    assert observation_library.list_source_references(str(project))["references"] == [tracked]
    assert paper.read_bytes() == saved_offline and paper.stat().st_mode & 0o777 == 0o640


def test_unlinked_native_workbench_reports_inactive_source_observation(observation_library):
    workspace = observation_library.create("Independent project")
    with _desktop_http_session(paths.workspace_id(workspace)) as (port, session):
        code, body = _request(port, _SOURCE_OBSERVATIONS, session)
        assert code == 200
        assert json.loads(body) == {
            "status": "inactive", "source_id": None, "revision": None, "files": [], "generation": 0,
        }


def test_run_resolves_a_moved_managed_project_before_starting_server(monkeypatch, tmp_path):
    old, confirmed = tmp_path / "previous", tmp_path / "active"
    project_id = str(uuid4())
    calls = []

    class Library:
        anchor = tmp_path

        def resolve_existing(self, path):
            assert path == str(old)
            return confirmed

        def describe(self, path):
            assert path == confirmed
            calls.append(("describe", path))
            return {"id": project_id, "name": "Original project"}

    class Server:
        workspace = confirmed
        launch_url = "test"

        def serve_forever(self, *, poll_interval):
            calls.append(("serve", self.workspace))

        def shutdown(self):
            pass

    def open_project(path, _resources, *, expected_project):
        assert expected_project is None
        calls.append(("open", Path(path)))
        return Server()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "credentials"))
    monkeypatch.setattr(desktop, "_open", open_project)
    monkeypatch.setattr(desktop, "workspace_registration", lambda path: (tmp_path, "slot"))
    monkeypatch.setattr(desktop.shutil, "which", lambda _: "/fixture/lean")
    output = StringIO()
    assert desktop.run(str(old), BytesIO(), output, library=Library()) == 0
    assert calls == [("open", confirmed), ("describe", confirmed), ("serve", confirmed)]
    assert json.loads(output.getvalue()) == {
        "version": 1, "status": "ready", "workspace": str(confirmed), "name": "Original project",
        "project_id": project_id, "url": "test", "warnings": [],
    }


def test_registered_launch_rejects_a_different_identity_before_loading_credentials(
    monkeypatch, tmp_path, bound_workspace,
):
    project_id = uuid4()

    class Library:
        anchor = tmp_path

        def resolve_project(self, identifier):
            assert identifier == str(project_id)
            return bound_workspace

        def open_default(self):
            # The mismatch falls back to the library; stop there to see what the first attempt did.
            raise LibraryError("storage-busy")

    monkeypatch.setattr(desktop, "_configure_database", _unexpected_start)
    output = StringIO()
    assert desktop.run(None, BytesIO(), output, library=Library(), project_id=project_id) == 2
    assert json.loads(output.getvalue()) == {"version": 1, "status": "error", "code": "storage-busy"}


@pytest.mark.parametrize("code, falls_back", [
    ("storage-project", True), ("storage-unavailable", True), ("storage-invalid", True), ("workspace", True),
    ("unbound", True), ("login-missing", True), ("storage-busy", False), ("postgres", False), ("schema", False),
])
def test_a_project_that_cannot_be_reopened_opens_the_library_instead(monkeypatch, tmp_path, code, falls_back):
    remembered, default = tmp_path / "remembered", tmp_path / "default"
    project_id = str(uuid4())

    class Library:
        anchor = tmp_path

        def resolve_existing(self, path):
            if code.startswith("storage-"):
                raise LibraryError(code)
            return remembered

        def open_default(self):
            return default

        def describe(self, path):
            assert path == default
            return {"id": project_id, "name": "Library"}

    class Server:
        launch_url = "test"

        def __init__(self, workspace):
            self.workspace = workspace

        def serve_forever(self, *, poll_interval):
            pass

        def shutdown(self):
            pass

    def open_project(path, _resources, *, expected_project):
        if Path(path) == remembered:
            raise desktop._StartError(code)
        return Server(Path(path))

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "credentials"))
    monkeypatch.setattr(desktop, "_open", open_project)
    monkeypatch.setattr(desktop, "workspace_registration", lambda path: (tmp_path, "slot"))
    monkeypatch.setattr(desktop.shutil, "which", lambda _: "/fixture/lean")
    output = StringIO()
    result = desktop.run(str(remembered), BytesIO(), output, library=Library())
    reply = json.loads(output.getvalue())
    if falls_back:
        assert result == 0
        assert reply == {
            "version": 1, "status": "ready", "workspace": str(default), "name": "Library",
            "project_id": project_id, "reopen_failed": code, "url": "test", "warnings": [],
        }
    else:
        assert result == 2
        assert reply == {"version": 1, "status": "error", "code": code}


def test_a_folder_the_researcher_chose_does_not_fall_back(tmp_path):
    class Library:
        anchor = tmp_path

        def open_folder(self, path):
            assert Path(path) == tmp_path
            raise LibraryError("storage-project")

        def open_default(self):
            pytest.fail("A chosen folder that cannot open must be reported, not replaced.")

    output = StringIO()
    assert desktop.run(str(tmp_path), BytesIO(), output, select=True, library=Library()) == 2
    assert json.loads(output.getvalue()) == {"version": 1, "status": "error", "code": "storage-project"}


def test_storage_preview_translates_the_exact_confirmation_token(tmp_path):
    target = str(tmp_path / "Trellin Library")
    source = str(tmp_path / "Library")

    class Library:
        def preview(self, destination):
            assert destination == target
            return {"source": source, "destination": target, "sha256": "a" * 64,
                    "files": 6, "bytes": 1024, "project_count": 1}

    output = StringIO()
    assert desktop._storage(Library(), ["--storage-preview", target], output) == 0
    assert json.loads(output.getvalue()) == {
        "version": 1, "status": "ready", "root": source, "destination": target, "confirmation": "a" * 64,
        "files": 6, "bytes": 1024, "project_count": 1,
    }


@pytest.mark.parametrize("fails_at", ["backup", "verify"])
def test_project_upgrade_does_not_run_migration_without_a_verified_backup(
    monkeypatch, bound_workspace, capsys, fails_at,
):
    saved_owner = "postgresql://selected/fixture"
    _save_login(bound_workspace, RM_HUMAN_DSN="service=selected-human", RM_MIGRATION_DSN=saved_owner)
    monkeypatch.setenv("RM_MIGRATION_DSN", "postgresql://inherited/another-workspace")
    monkeypatch.setattr(sys, "argv", ["desktop", "--upgrade-project", str(bound_workspace)])

    def status(dsn, workspace):
        assert dsn == saved_owner
        assert workspace == paths.require_workspace() == bound_workspace
        assert paths.strict_workspace()
        return "0081", False

    def migration(*_):
        pytest.fail("Upgrade ran without verifying a project backup.")

    def backup_failure(*_):
        raise ValueError("private fixture diagnostics")

    monkeypatch.setattr(desktop_workspace, "_upgrade_status", status)
    monkeypatch.setattr(desktop_workspace.migrate, "migration_url", lambda dsn: dsn)
    monkeypatch.setattr(desktop_workspace.migrate, "migration_head", lambda: "0084")
    monkeypatch.setattr(desktop_workspace.migrate, "upgrade", migration)
    monkeypatch.setattr(desktop_workspace.project_archive, "backup", lambda: {"directory": "/fixture/archive"})
    monkeypatch.setattr(desktop_workspace.project_archive, fails_at, backup_failure)

    assert desktop.main() == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"version": 1, "status": "error", "code": "upgrade-backup"}


def test_project_upgrade_from_proof_drafts_adds_agent_work_after_backup(monkeypatch, bound_workspace):
    owner = "postgresql://selected/fixture"
    monkeypatch.setenv("RM_MIGRATION_DSN", owner)
    states = iter([("0082", False), ("0084", True)])
    monkeypatch.setattr(desktop_workspace, "_upgrade_status", lambda *_: next(states))
    monkeypatch.setattr(desktop_workspace.migrate, "migration_url", lambda dsn: dsn)
    monkeypatch.setattr(desktop_workspace.migrate, "migration_head", lambda: "0084")
    steps = []

    def verify(archive):
        assert archive == "/fixture/archive"
        steps.append("verified")
        return {"schema_revision": "0082", "database_id": str(paths.database_id(bound_workspace))}

    def migrate(dsn, target):
        assert (dsn, target) == (owner, "0084")
        steps.append("upgraded")

    monkeypatch.setattr(desktop_workspace.project_archive, "backup", lambda: {"directory": "/fixture/archive"})
    monkeypatch.setattr(desktop_workspace.project_archive, "verify", verify)
    monkeypatch.setattr(desktop_workspace.migrate, "upgrade", migrate)
    with paths.use_workspace(bound_workspace, strict=True):
        assert desktop_workspace.upgrade_project_database(bound_workspace) == "/fixture/archive"
    assert steps == ["verified", "upgraded"]
