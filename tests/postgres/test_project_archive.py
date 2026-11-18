# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qsl, quote, urlencode

import pytest

from research_multiplier.cli import main
from research_multiplier.storage import paths
from research_multiplier.storage.migrate import upgrade
from tests._native_db import disposable_database


pytestmark = pytest.mark.postgres


def _restore_uri(dsn, socket_alias):
    from psycopg.conninfo import conninfo_to_dict

    host = conninfo_to_dict(dsn).get("host", "")
    if not host.startswith("/"):
        return dsn

    socket_alias.symlink_to(host, target_is_directory=True)
    base, _, query = dsn.partition("?")
    options = dict(parse_qsl(query, keep_blank_values=True))
    options["host"] = str(socket_alias)
    return f"{base}?{urlencode(options, quote_via=quote)}"


def test_restore_rejects_keyword_dsn_before_import_and_retries_with_uri(tmp_path, monkeypatch, capsys):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    source = paths.initialize_workspace(tmp_path / "source")
    note = source / "inputs" / "note.txt"
    note.write_text("Project archive regression fixture.\n", encoding="utf-8")
    archive = tmp_path / "archive"
    restored = tmp_path / "new" / "workspace"

    with disposable_database() as source_dsn, disposable_database() as target_dsn:
        upgrade(source_dsn)
        with psycopg.connect(source_dsn, autocommit=True) as connection:
            database_id, = connection.execute("SELECT rm.current_database_id()").fetchone()
            connection.execute("SELECT rm.bind_research_workspace(%s, %s)", (paths.workspace_id(source), database_id))
        paths.bind_database(source, database_id)
        monkeypatch.setenv("RM_MIGRATION_DSN", source_dsn)
        assert main(["--workspace", str(source), "research", "backup-project", "--out", str(archive)]) == 0
        capsys.readouterr()

        with TemporaryDirectory(prefix="rmr-") as directory:
            uri = _restore_uri(target_dsn, Path(directory) / "with space")
            command = ["research", "restore-project", str(archive), str(restored)]
            # Converting the URI to keyword form must retain the same target.
            monkeypatch.setenv("RM_MIGRATION_DSN", make_conninfo(**conninfo_to_dict(uri)))
            assert main(command) == 2
            assert capsys.readouterr().err == (
                "Error: Use a postgresql:// URL in RM_MIGRATION_DSN to restore a project.\n"
            )
            assert not restored.parent.exists()
            with psycopg.connect(uri) as connection:
                assert connection.execute("SELECT to_regnamespace('rm') IS NULL").fetchone() == (True,)
                assert connection.execute("""
                    SELECT count(*) FROM pg_catalog.pg_tables
                    WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema'
                """).fetchone() == (0,)

            monkeypatch.setenv("RM_MIGRATION_DSN", uri)
            assert main(command) == 0

        assert (restored / "inputs" / note.name).read_bytes() == note.read_bytes()
        assert paths.workspace_id(restored) != paths.workspace_id(source)
        with psycopg.connect(target_dsn) as connection:
            identity, workspace = connection.execute(
                "SELECT database_id, workspace_id FROM rm.current_workspace_binding()"
            ).fetchone()
        assert identity == paths.database_id(restored) != database_id
        assert workspace == paths.workspace_id(restored)
