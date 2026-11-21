"""Local Team profiles reference model connections without granting dispatch."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from uuid import UUID

from research_multiplier.models.connections import ConnectionError
from research_multiplier.storage import paths
from research_multiplier.storage.local_settings import has_table, private_database, settings_directory
from research_multiplier.storage.managed_workspaces import LibraryError


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
_CONVERSATION_SCHEMA = """
    CREATE TABLE IF NOT EXISTS team_conversation_bindings (
        workspace_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        PRIMARY KEY (workspace_id, user_id, request_id),
        UNIQUE (workspace_id, user_id, thread_id)
    )
"""


class AgentProfileError(ValueError):
    pass


def _settings_file():
    return settings_directory() / "team-agents.sqlite3"


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
            with private_database(file, write=write) as database:
                if write:
                    database.execute(_SCHEMA)
                yield database
        except (OSError, sqlite3.Error, json.JSONDecodeError, LibraryError):
            raise AgentProfileError("team_agents.unavailable") from None

    def list(self):
        with self._database() as database:
            if not has_table(database, "team_agents"):
                return []
            rows = database.execute("SELECT id, revision, profile FROM team_agents WHERE workspace_id = ?",
                                    (self._workspace_id,))
            profiles = [{**json.loads(profile), "id": identifier, "revision": revision}
                        for identifier, revision, profile in rows]
        return sorted(profiles, key=lambda profile: (profile["name"].casefold(), profile["id"]))

    def conversation_bindings(self, user_id, thread_id=None):
        with self._database() as database:
            if not has_table(database, "team_conversation_bindings"):
                return {}
            target = str(thread_id) if thread_id is not None else None
            return dict(database.execute("""
                SELECT thread_id, agent_id FROM team_conversation_bindings
                WHERE workspace_id = ? AND user_id = ? AND (? IS NULL OR thread_id = ?)
            """, (self._workspace_id, user_id, target, target)))

    def conversation_recipient(self, user_id, thread_id):
        with self._database() as database:
            if not has_table(database, "team_conversation_bindings"):
                return None
            row = database.execute("""
                SELECT binding.agent_id, agent.revision, agent.profile
                FROM team_conversation_bindings AS binding
                LEFT JOIN team_agents AS agent
                    ON agent.workspace_id = binding.workspace_id AND agent.id = binding.agent_id
                WHERE binding.workspace_id = ? AND binding.user_id = ? AND binding.thread_id = ?
            """, (self._workspace_id, user_id, str(thread_id))).fetchone()
            if row is None:
                return None
            agent_id, revision, profile = row
            if profile is None:
                raise AgentProfileError("team_agents.unavailable")
            return {"id": agent_id, "revision": revision, "connection_id": json.loads(profile)["connection_id"]}

    def bind_conversation(self, user_id, request_id, agent_id, request_fingerprint, create_thread):
        """Commit the binding before the caller can publish the new conversation."""
        with self._database(write=True) as database:
            database.execute(_CONVERSATION_SCHEMA)
            key = self._workspace_id, user_id, request_id
            existing = database.execute("""
                SELECT thread_id, request_fingerprint FROM team_conversation_bindings
                WHERE workspace_id = ? AND user_id = ? AND request_id = ?
            """, key).fetchone()
            if existing is not None:
                thread_id, original_fingerprint = existing
                if original_fingerprint != request_fingerprint:
                    raise AgentProfileError("team_agents.conflict")
                return thread_id
            if database.execute("SELECT 1 FROM team_agents WHERE workspace_id = ? AND id = ?",
                                (self._workspace_id, agent_id)).fetchone() is None:
                raise AgentProfileError("team_agents.unavailable")
            thread_id = str(create_thread())
            database.execute("""
                INSERT INTO team_conversation_bindings
                    (workspace_id, user_id, request_id, thread_id, agent_id, request_fingerprint)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (*key, thread_id, agent_id, request_fingerprint))
            return thread_id

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
