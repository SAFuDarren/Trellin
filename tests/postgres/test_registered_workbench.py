# SPDX-License-Identifier: Apache-2.0
"""Open registered projects through the desktop launcher with real database bindings."""

from contextlib import ExitStack, contextmanager
import json
import os
from select import select
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from research_multiplier.storage import credentials, paths
from research_multiplier.storage.postgres import bind_workspace
from research_multiplier.workflows.desktop_library import Library
from tests._native_db import disposable_project_logins
from tests.postgres.test_workbench_routes import _approved_statement, _request


pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.name != "posix", reason="Mac launcher pipes.")]


@contextmanager
def _registered_session(identifier):
    with subprocess.Popen(
        [sys.executable, "-B", "-m", "research_multiplier.workbench.desktop", "--registered-project", identifier],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    ) as child:
        try:
            assert select([child.stdout], [], [], 20)[0], "The desktop launcher did not send readiness."
            ready = json.loads(child.stdout.readline())
            assert ready["status"] == "ready", ready.get("code")
            assert ready["project_id"] == identifier
            endpoint = urlsplit(ready["url"])
            yield SimpleNamespace(session=endpoint.fragment.removeprefix("session="), server_port=endpoint.port,
                                  origin=f"{endpoint.scheme}://{endpoint.netloc}")
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)
        assert child.returncode == 0


def test_switching_registered_projects_reopens_their_own_records(tmp_path, monkeypatch):
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "library"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    library = Library()
    projects = []
    with ExitStack() as databases:
        for name in ("Alpha", "Beta"):
            logins = databases.enter_context(disposable_project_logins())
            workspace = paths.initialize_workspace(tmp_path / name)
            with monkeypatch.context() as environment, paths.use_workspace(workspace, strict=True):
                for key in credentials.KEYS:
                    environment.setenv(key, logins.get(key, ""))
                bind_workspace(workspace)
                claim, statement = _approved_statement(title=f"Goal in {name}")
            credentials.write(paths.workspace_id(workspace), logins)
            project = library.register_external(workspace)
            assert library.resolve_project(project["id"], verify_database=True) == workspace
            projects.append((project["id"], name, claim, statement))

        for identifier, name, claim, statement in projects:
            with _registered_session(identifier) as server:
                assert [row["id"] for row in _request(server, "GET", "/api/claims")["claims"]] == [claim]
                graph = _request(server, "GET", f"/api/graph?claim={claim}")
                node = next(row["id"] for row in graph["nodes"] if row["kind"] == "goal")
                scope = {"claim_id": claim, "statement_id": statement, "node_id": node}
                _request(server, "POST", "/api/guidance", body={
                    **scope, "text": f"Proof history in {name}", "request_id": str(uuid4()), "audience": "local",
                })
                _request(server, "POST", "/api/conversations/create", body={**scope, "title": f"Chat in {name}"})

        for identifier, name, claim, _ in reversed(projects):
            with _registered_session(identifier) as server:
                assert [row["id"] for row in _request(server, "GET", "/api/claims")["claims"]] == [claim]
                activity = _request(server, "GET", f"/api/activity?claim={claim}")
                assert [row["text"] for row in activity["guidance"]] == [f"Proof history in {name}"]
                threads = _request(server, "GET", "/api/conversations")["threads"]
                assert [(row["claim_id"], row["title"]) for row in threads] == [(claim, f"Chat in {name}")]
