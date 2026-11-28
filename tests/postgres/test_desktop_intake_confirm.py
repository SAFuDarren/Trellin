# SPDX-License-Identifier: Apache-2.0
"""Confirm a folder intake and reopen its persisted proposal through the installed desktop launcher."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from tests.offline.test_desktop_intake import _snapshot
from tests.postgres.test_registered_workbench import _registered_session
from tests.postgres.test_workbench_routes import _request


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(os.environ.get("GITHUB_ACTIONS") != "true", reason="Requires the disposable CI cluster."),
]


def _desktop(*args, body=None):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "research_multiplier.workbench.desktop", *args],
        input=json.dumps(body) if body is not None else "", capture_output=True, text=True, timeout=90,
    )
    reply = json.loads(result.stdout)
    assert result.returncode == 0 and reply["status"] == "ready", reply.get("code")
    return reply


def _databases(admin):
    return {name for name, in admin.execute("SELECT datname FROM pg_database")}


@pytest.fixture
def ci_cluster(tmp_path, monkeypatch):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    runner = Path(os.environ["RUNNER_TEMP"]).resolve()
    endpoint = conninfo_to_dict(os.environ["RM_TEST_DSN"])
    if (set(endpoint) != {"user", "dbname", "host", "port"} or endpoint["user"] != "postgres"
            or endpoint["dbname"] != "research_multiplier_test" or endpoint["host"] != str(runner / "rm-postgres-socket")
            or not endpoint["port"].isdecimal() or not 0 < int(endpoint["port"]) < 65536):
        pytest.fail("Expected the dedicated Unix socket from the disposable CI cluster.", pytrace=False)

    for key in tuple(os.environ):
        if key.startswith(("RM_", "PG")) or key == "PYTHONPATH":
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    anchor = tmp_path / "library"
    config = tmp_path / "config"
    monkeypatch.setenv("RM_TRELLIN_HOME", str(anchor))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("RM_DESKTOP_PG_SOCKET", endpoint["host"])
    monkeypatch.setenv("RM_DESKTOP_PG_PORT", endpoint["port"])

    with psycopg.connect(**endpoint, autocommit=True, connect_timeout=5) as admin:
        assert Path(admin.execute("SHOW data_directory").fetchone()[0]).resolve() == runner / "rm-postgres-data"
        yield admin


@pytest.fixture
def proposal(tmp_path, ci_cluster):
    """Pin a synthetic source without creating any library or profile state."""
    source = tmp_path / "source"
    source.mkdir()
    note = source / "note.md"
    quote = "A token has a label."
    note.write_text(quote + "\n", encoding="utf-8")
    config = tmp_path / "config"
    settings = config / "existing"
    settings.mkdir(parents=True)
    (settings / "preferences").write_bytes(b"fixture")
    before = _snapshot(source), _snapshot(config)
    selection = _desktop("--intake-inspect", body={"root": str(source)})["selection"]
    chosen = {key: selection[key] for key in ("root", "identity")}
    file, = _desktop("--intake-preview", body={**chosen, "references": [{"path": note.name}]})["files"]
    assert file["status"] == "available" and file["text"] == quote + "\n"
    assert not (tmp_path / "library").exists()
    assert (_snapshot(source), _snapshot(config)) == before
    reference = {"path": note.name, "sha256": file["sha256"]}
    draft = {"title": "Token study", "question": "How are tokens labeled?", "sources": [reference], "links": [],
             "items": [{"id": "i1", "kind": "definition", "label": "Token", "source": {**reference, "quote": quote}}]}
    return {"operation_id": str(uuid4()), "mode": "folder", **chosen, "draft": draft}


def test_confirm_retry_and_open_after_source_removal(tmp_path, ci_cluster, proposal):
    source = Path(proposal["root"])
    settings = tmp_path / "config" / "existing"
    original_source, original_settings = _snapshot(source), _snapshot(settings)
    before = _databases(ci_cluster)
    project = _desktop("--intake-confirm", body=proposal)["project"]
    identifier = project["id"]
    workspace = Path(project["path"])
    draft = proposal["draft"]
    assert project["name"] == draft["title"] and project["managed"] is True
    assert workspace.is_dir() and workspace.is_relative_to(tmp_path / "library")
    assert (_snapshot(source), _snapshot(settings)) == (original_source, original_settings)
    (source / draft["sources"][0]["path"]).unlink()
    source.rmdir()

    assert _desktop("--intake-confirm", body=proposal)["project"] == project
    assert _desktop("--intake-read", identifier)["draft"] == draft
    rows = _desktop("--storage-info")["projects"]
    assert [row["id"] for row in rows if row["name"] == draft["title"]] == [identifier]
    with _registered_session(identifier) as server:
        assert isinstance(_request(server, "GET", "/api/claims")["claims"], list)
    assert _databases(ci_cluster) - before == {f"rm_{UUID(identifier).hex[:12]}"}


def test_source_change_after_binding_rolls_back_database_and_logins(ci_cluster, proposal, monkeypatch):
    from research_multiplier.storage import credentials, paths, postgres
    from research_multiplier.storage.managed_workspaces import LibraryError, MANAGED_FILE
    from research_multiplier.workflows.desktop_library import Library

    note = Path(proposal["root"]) / proposal["draft"]["sources"][0]["path"]
    bind = postgres.bind_workspace
    before = _databases(ci_cluster)
    bound = []

    def change_after_binding(workspace):
        result = bind(workspace)
        identifier = paths.workspace_id(workspace)
        database = f"rm_{identifier.hex[:12]}"
        assert _databases(ci_cluster) - before == {database}
        bound.append((identifier, database, workspace))
        note.write_bytes(b"Changed during provisioning.")
        return result

    monkeypatch.setattr(postgres, "bind_workspace", change_after_binding)
    library = Library()
    # The native launcher normally owns the process in which setup installs its role DSNs.
    with patch.dict(os.environ), pytest.raises(LibraryError, match="^intake-changed$"):
        library.confirm_intake(**proposal)
    (identifier, database, workspace), = bound
    assert _databases(ci_cluster) == before
    roles = ci_cluster.execute("SELECT 1 FROM pg_roles WHERE starts_with(rolname::text, %s)", (database + "_",))
    assert roles.fetchone() is None
    assert not credentials.location(identifier).exists()
    assert not (workspace / MANAGED_FILE).exists()
    assert not (workspace / "inputs" / "project-intake.json").exists()
    assert library.status()["projects"] == []
