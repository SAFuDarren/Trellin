# SPDX-License-Identifier: Apache-2.0

from io import StringIO
import json
from pathlib import Path

import pytest

from research_multiplier.storage import credentials, paths, project_archive
from research_multiplier.storage.migrate import migration_head, upgrade
from research_multiplier.workbench import desktop
from tests._native_db import disposable_database, project_logins


pytestmark = pytest.mark.postgres


def assert_current_schema(connection):
    revision, = connection.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
    assert revision == migration_head()
    assert connection.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone() == ("vector",)


def test_empty_database_upgrades_to_current_schema():
    import psycopg

    with disposable_database() as dsn:
        upgrade(dsn)
        with psycopg.connect(dsn) as connection:
            assert_current_schema(connection)
            assert connection.execute("SELECT rm.current_database_id()").fetchone()[0] is not None


def test_upgrade_from_0068_preserves_database_identity():
    import psycopg

    with disposable_database() as dsn:
        upgrade(dsn, "0068")
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT version_num FROM public.rm_alembic_version").fetchone() == ("0068",)
            original_identity, = connection.execute("SELECT rm.current_database_id()").fetchone()

        upgrade(dsn)
        with psycopg.connect(dsn) as connection:
            assert_current_schema(connection)
            assert original_identity is not None
            assert connection.execute("SELECT rm.current_database_id()").fetchone() == (original_identity,)


@pytest.mark.parametrize("revision", ["0070", "0080", "0081", "0084"])
def test_native_upgrade_archives_bound_project_and_reopens_same_database(tmp_path, monkeypatch, revision):
    import psycopg

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for key in credentials.KEYS:
        monkeypatch.setenv(key, "")
    workspace = paths.initialize_workspace(tmp_path / "workspace")
    workspace_id = paths.workspace_id(workspace)
    original = workspace / "inputs" / "original.txt"
    original.write_bytes(b"Disposable project archive fixture.\n")

    with disposable_database() as dsn, disposable_database() as other_dsn:
        upgrade(dsn, revision)
        upgrade(other_dsn, "0080")
        with psycopg.connect(dsn, autocommit=True) as connection:
            database_id, = connection.execute("SELECT rm.current_database_id()").fetchone()
            connection.execute("SELECT rm.bind_research_workspace(%s, %s)", (workspace_id, database_id))
        paths.bind_database(workspace, database_id)

        with project_logins(dsn) as logins:
            credentials.write(workspace_id, {**logins, "RM_MIGRATION_DSN": other_dsn})
            refused = StringIO()
            assert desktop._upgrade_project(str(workspace), refused) == 2
            assert json.loads(refused.getvalue()) == {"version": 1, "status": "error", "code": "unbound"}
            assert list((workspace / "backups").iterdir()) == []

            credentials.write(workspace_id, {**logins, "RM_MIGRATION_DSN": dsn})
            output = StringIO()
            assert desktop._upgrade_project(str(workspace), output) == 0
            reply = json.loads(output.getvalue())
            assert (reply["version"], reply["status"]) == (1, "ready")
            archive = Path(reply["backup"])
            assert archive.parent == workspace / "backups"
            archived = project_archive.verify(archive)
            assert archived["schema_revision"] == revision
            assert archived["database_id"] == str(database_id)
            assert (archive / "project" / "inputs" / original.name).read_bytes() == original.read_bytes()

            repeated = StringIO()
            assert desktop._upgrade_project(str(workspace), repeated) == 0
            assert json.loads(repeated.getvalue()) == {"version": 1, "status": "ready", "backup": None}
            assert list((workspace / "backups").iterdir()) == [archive]

        with psycopg.connect(dsn) as connection:
            assert_current_schema(connection)
            assert connection.execute("SELECT * FROM rm.current_workspace_binding()").fetchone() == (
                database_id, workspace_id,
            )
            assert connection.execute("""
                SELECT to_regprocedure('rm.workbench_codex_release_available(uuid)') IS NOT NULL
                       AND to_regprocedure('rm.read_workbench_proof_draft(uuid,uuid,text)') IS NOT NULL
                       AND to_regprocedure('rm.offer_research_issue(uuid,uuid,jsonb,jsonb)') IS NOT NULL
            """).fetchone() == (True,)
