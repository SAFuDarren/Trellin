"""Local Team profiles reference model connections without granting dispatch."""

from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from uuid import UUID

from research_multiplier.models.connections import ConnectionError
from research_multiplier.storage import credentials, paths
from research_multiplier.storage.managed_workspaces import LibraryError, application_home


MAX_REQUEST_BYTES = 120_000
_MAX_PROFILES = 100
_MAX_REVISION = 2**53 - 1
_AVATARS = {"initials", "book", "compass", "spark"}
_FIELDS = {"id", "revision", "connection_id", "name", "description", "avatar", "instructions", "starters"}
_SCHEMA = """
    CREATE TABLE IF NOT EXISTS team_agents (
        workspace_id TEXT NOT NULL,
        id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        profile TEXT NOT NULL,
        PRIMARY KEY (workspace_id, id)
    )
"""


class AgentProfileError(ValueError):
    pass


def _settings_file():
    root = application_home() / "Settings" if sys.platform == "darwin" else credentials.directory().parent
    return root / "team-agents.sqlite3"


def _id(value):
    if not isinstance(value, str) or len(value) != 36 or str(UUID(value)) != value:
        raise ValueError
    return value


def _text(value, limit, *, required=False, multiline=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError
    # JSON escape sequences can decode to invalid Unicode or control characters.
    value.encode("utf-8")
    allowed = "\t\r\n" if multiline else ""
    if any((ord(char) < 32 or 127 <= ord(char) < 160) and char not in allowed for char in value):
        raise ValueError
    value = value.strip()
    if required and not value:
        raise ValueError
    return value


def _validated(document):
    try:
        if not isinstance(document, dict) or document.keys() != _FIELDS:
            raise ValueError
        revision, avatar, starters = document["revision"], document["avatar"], document["starters"]
        if (type(revision) is not int or not 0 <= revision <= _MAX_REVISION
                or not isinstance(avatar, str) or avatar not in _AVATARS
                or not isinstance(starters, list) or len(starters) > 3):
            raise ValueError
        profile = {
            "connection_id": _id(document["connection_id"]),
            "name": _text(document["name"], 80, required=True),
            "description": _text(document["description"], 400, multiline=True),
            "avatar": avatar,
            "instructions": _text(document["instructions"], 8000, multiline=True),
            "starters": [_text(starter, 240, required=True) for starter in starters],
        }
        return _id(document["id"]), revision, profile
    except ValueError:
        raise AgentProfileError("team_agents.invalid") from None


def _available(registry, connection_id):
    try:
        return any(connection["id"] == connection_id and connection["fingerprint"]
                   and connection["status"] in {"connected", "untested"} for connection in registry.list())
    except ConnectionError:
        raise AgentProfileError("team_agents.connection_unavailable") from None


class AgentProfiles:
    """Store one workspace's configuration in this device's private settings."""

    def __init__(self, workspace, *, settings_file=None):
        try:
            identifier = paths.workspace_id(workspace)
        except (OSError, ValueError):
            identifier = None
        self._workspace_id = str(identifier) if identifier is not None else None
        self._file = Path(settings_file) if settings_file is not None else None

    @contextmanager
    def _database(self, *, write=False):
        if self._workspace_id is None:
            raise AgentProfileError("team_agents.unavailable")
        try:
            file = self._file if self._file is not None else _settings_file()
            if write:
                file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                try:
                    descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    pass
                else:
                    os.close(descriptor)
            try:
                info = file.lstat()
            except FileNotFoundError:
                if write:
                    raise
                yield None
                return
            if not stat.S_ISREG(info.st_mode) or os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise AgentProfileError("team_agents.unavailable")
            mode = "rw" if write else "ro"
            with closing(sqlite3.connect(file.absolute().as_uri() + f"?mode={mode}", uri=True, timeout=2)) as database, database:
                if write:
                    database.execute("BEGIN IMMEDIATE")
                    database.execute(_SCHEMA)
                yield database
        except (OSError, sqlite3.Error, json.JSONDecodeError, LibraryError):
            raise AgentProfileError("team_agents.unavailable") from None

    def list(self):
        with self._database() as database:
            if database is None or database.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'team_agents' AND type = 'table'"
            ).fetchone() is None:
                return []
            rows = database.execute("SELECT id, revision, profile FROM team_agents WHERE workspace_id = ?",
                                    (self._workspace_id,))
            profiles = [{**json.loads(profile), "id": identifier, "revision": revision}
                        for identifier, revision, profile in rows]
        return sorted(profiles, key=lambda profile: (profile["name"].casefold(), profile["id"]))

    def save(self, document, registry):
        identifier, revision, profile = _validated(document)
        serialized = json.dumps(profile, ensure_ascii=False, sort_keys=True)
        key = self._workspace_id, identifier
        with self._database(write=True) as database:
            current = database.execute("SELECT revision, profile FROM team_agents WHERE workspace_id = ? AND id = ?",
                                       key).fetchone()
            if current is not None and current[1] == serialized and revision in {current[0], current[0] - 1}:
                return {**profile, "id": identifier, "revision": current[0]}
            if revision == _MAX_REVISION or revision != (current[0] if current else 0):
                raise AgentProfileError("team_agents.conflict")
            if not _available(registry, profile["connection_id"]):
                raise AgentProfileError("team_agents.connection_unavailable")
            if current is None:
                count, = database.execute("SELECT count(*) FROM team_agents WHERE workspace_id = ?",
                                          (self._workspace_id,)).fetchone()
                if count >= _MAX_PROFILES:
                    raise AgentProfileError("team_agents.limit")
            next_revision = revision + 1
            database.execute("""
                INSERT INTO team_agents (workspace_id, id, revision, profile) VALUES (?, ?, ?, ?)
                ON CONFLICT (workspace_id, id) DO UPDATE SET revision = excluded.revision, profile = excluded.profile
            """, (*key, next_revision, serialized))
        return {**profile, "id": identifier, "revision": next_revision}
