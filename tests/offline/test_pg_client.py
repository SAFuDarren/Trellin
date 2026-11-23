# SPDX-License-Identifier: Apache-2.0
"""Backups, restores and the desktop upgrade use a PostgreSQL client that can work with the server (#321)."""

import os
from pathlib import Path

import pytest

from research_multiplier.storage import pg_client

pytestmark = [pytest.mark.offline, pytest.mark.skipif(os.name != "posix", reason="Uses shell-script clients.")]


def fake_client(directory, major, tool="pg_dump"):
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / tool
    executable.write_text(f"#!/bin/sh\necho '{tool} (PostgreSQL) {major}.4'\n", encoding="utf-8")
    executable.chmod(0o755)
    return executable


@pytest.fixture
def clients(tmp_path, monkeypatch):
    """PostgreSQL 16 first on PATH and 18 after it, with no other install locations."""
    old, new = tmp_path / "pg16" / "bin", tmp_path / "pg18" / "bin"
    fake_client(old, 16)
    fake_client(new, 18)
    monkeypatch.setenv("PATH", os.pathsep.join([str(old), str(new)]))
    monkeypatch.setattr(pg_client, "install_directories", lambda: [])
    return old, new


def chosen(path):
    return Path(path).parent.parent.name


def test_the_server_major_version_wins_over_path_order(clients):
    assert chosen(pg_client.resolve("pg_dump", 18)) == "pg18"
    assert chosen(pg_client.resolve("pg_dump", 16)) == "pg16"
    # No exact match: the nearest newer client.
    assert chosen(pg_client.resolve("pg_dump", 17)) == "pg18"
    assert chosen(pg_client.resolve("pg_dump", 14)) == "pg16"
    # An unknown server keeps the first client on PATH.
    assert chosen(pg_client.resolve("pg_dump", None)) == "pg16"


def test_an_older_client_only_names_both_versions(tmp_path, monkeypatch):
    fake_client(tmp_path / "pg16" / "bin", 16)
    monkeypatch.setenv("PATH", str(tmp_path / "pg16" / "bin"))
    monkeypatch.setattr(pg_client, "install_directories", lambda: [])
    with pytest.raises(ValueError) as error:
        pg_client.resolve("pg_dump", 18)
    message = str(error.value)
    assert "PostgreSQL 16" in message and "PostgreSQL 18 server" in message and "--pg-bin" in message


def test_install_locations_are_searched_when_path_lacks_a_matching_client(tmp_path, monkeypatch):
    # A Finder-launched app may have only an old client, or none, on PATH.
    fake_client(tmp_path / "pg16" / "bin", 16)
    installed = tmp_path / "opt" / "postgresql@18" / "bin"
    fake_client(installed, 18)
    monkeypatch.setenv("PATH", str(tmp_path / "pg16" / "bin"))
    monkeypatch.setattr(pg_client, "install_directories", lambda: [installed])
    assert pg_client.resolve("pg_dump", 18) == str(installed / "pg_dump")
    monkeypatch.setenv("PATH", "")
    assert pg_client.resolve("pg_dump", 18) == str(installed / "pg_dump")


def test_an_explicit_pg_bin_is_respected_and_a_missing_tool_is_reported(clients, tmp_path):
    old, _ = clients
    assert pg_client.resolve("pg_dump", 18, pg_bin=old) == str(old / "pg_dump")
    with pytest.raises(ValueError, match="Install a PostgreSQL client with pg_restore"):
        pg_client.resolve("pg_restore", 18)
    with pytest.raises(ValueError, match="--pg-bin"):
        pg_client.resolve("pg_dump", 18, pg_bin=tmp_path / "missing")


def test_versions_and_install_directories_are_read_by_number(tmp_path):
    assert pg_client.server_major(180004) == 18 and pg_client.server_major(160014) == 16
    assert pg_client.server_major(None) is None
    assert pg_client.client_major(fake_client(tmp_path / "bin", 18)) == 18
    assert pg_client.client_major(tmp_path / "missing") is None
    paths = [Path("/opt/homebrew/opt/postgresql@9/bin"), Path("/opt/homebrew/opt/postgresql@18/bin"),
             Path("/Applications/Postgres.app/Contents/Versions/16/bin"), Path("/usr/pgsql-15/bin")]
    assert [pg_client._number(path) for path in paths] == [9, 18, 16, 15]
