"""Verify Trellin's startup protocol and child-process lifetime."""

from http.client import HTTPConnection
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workbench import desktop
from research_multiplier.workbench.server import _MissingSchema


pytestmark = pytest.mark.offline


def _startup(workspace):
    output = StringIO()
    code = desktop.run(str(workspace), BytesIO(), output)
    return code, json.loads(output.getvalue())


def test_invalid_workspace_does_not_fall_back_or_create_files(monkeypatch, tmp_path):
    initialized = paths.initialize_workspace(tmp_path / "configured")
    monkeypatch.setenv("RM_DATA_ROOT", str(initialized))
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    assert _startup(chosen) == (2, {"version": 1, "status": "error", "code": "workspace"})
    assert _startup(Path("relative")) == (2, {"version": 1, "status": "error", "code": "workspace"})
    assert list(chosen.iterdir()) == []


@pytest.mark.parametrize(("error", "code"), [
    (ValueError("private login details"), "postgres"),
    (DatabaseOperationError("human", "28P01", "private authentication detail"), "postgres"),
    (DatabaseOperationError("human", "42883", "private schema details"), "schema"),
    (_MissingSchema("private schema details"), "schema"),
    (RuntimeError("unexpected private details"), "startup"),
])
def test_startup_errors_send_only_a_fixed_code(monkeypatch, tmp_path, error, code):
    workspace = paths.initialize_workspace(tmp_path / "workspace")

    def fail_read(*_):
        raise error

    monkeypatch.setattr(desktop.credentials, "load", lambda _: None)
    monkeypatch.setattr(desktop.WorkbenchServer, "read", fail_read)
    assert _startup(workspace) == (2, {"version": 1, "status": "error", "code": code})


def test_missing_assets_does_not_start_a_session(monkeypatch, tmp_path):
    workspace = paths.initialize_workspace(tmp_path / "workspace")
    monkeypatch.setattr(desktop.credentials, "load", lambda _: None)
    monkeypatch.setattr("research_multiplier.workbench.server._app_assets", dict)
    assert _startup(workspace) == (2, {"version": 1, "status": "error", "code": "install"})


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


def test_local_session_uses_selected_workspace_and_drains_on_parent_disconnect(monkeypatch, tmp_path):
    workspace = paths.initialize_workspace(tmp_path / "workspace")
    started = Event()
    release = Event()
    loaded = []

    def read(server, action):
        assert server.workspace == workspace
        if action is desktop._check_schema:
            return None
        started.set()
        assert release.wait(5)
        return []

    monkeypatch.setattr(desktop.credentials, "load", loaded.append)
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
            assert loaded == [workspace]
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
