"""Keep canvas positions and research links outside the proof record."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from research_multiplier.storage import paths
from research_multiplier.storage.local_settings import has_table, private_database, settings_directory
from research_multiplier.storage.managed_workspaces import LibraryError, uuid_text

from . import proof_graph


MAX_REQUEST_BYTES = 1_024
_MAX_COORDINATE = 1_000_000
_POSITION_TABLE = """
    CREATE TABLE IF NOT EXISTS canvas_positions (
        workspace_id TEXT NOT NULL,
        claim_id TEXT NOT NULL,
        x REAL NOT NULL,
        y REAL NOT NULL,
        PRIMARY KEY (workspace_id, claim_id)
    )
"""
_LINK_TABLE = """
    CREATE TABLE IF NOT EXISTS canvas_links (
        workspace_id TEXT NOT NULL,
        source TEXT NOT NULL,
        target TEXT NOT NULL CHECK (target > source),
        PRIMARY KEY (workspace_id, source, target)
    )
"""


class ProjectCanvasError(ValueError):
    pass


def _operation(document):
    try:
        if not isinstance(document, dict):
            raise ValueError
        action = document.get("action")
        if action == "move" and document.keys() == {"action", "id", "x", "y"}:
            identifier, x, y = uuid_text(document["id"]), document["x"], document["y"]
            if all(type(value) in (int, float) and -_MAX_COORDINATE <= value <= _MAX_COORDINATE for value in (x, y)):
                return action, (identifier,), (x, y)
        if action in ("link", "unlink") and document.keys() == {"action", "source", "target"}:
            source, target = sorted((uuid_text(document["source"]), uuid_text(document["target"])))
            if source != target:
                return action, (source, target), ()
    except ValueError:
        pass
    raise ProjectCanvasError("project_canvas.invalid")


def _read(database, workspace):
    if not has_table(database, "canvas_positions"):
        return {"positions": {}, "links": []}
    positions = database.execute("""
        SELECT claim_id, x, y FROM canvas_positions WHERE workspace_id = ? ORDER BY claim_id
    """, (workspace,))
    links = database.execute("""
        SELECT source, target FROM canvas_links WHERE workspace_id = ? ORDER BY source, target
    """, (workspace,))
    return {
        "positions": {identifier: {"x": x, "y": y} for identifier, x, y in positions},
        "links": [{"source": source, "target": target} for source, target in links],
    }


class ProjectCanvas:
    """Store display-only metadata on this device under the launcher's workspace ID."""

    def __init__(self, workspace, *, settings_file=None):
        try:
            identifier = paths.workspace_id(workspace)
        except (OSError, ValueError):
            identifier = None
        self._workspace = str(identifier) if identifier is not None else None
        self._file = Path(settings_file) if settings_file is not None else None

    @contextmanager
    def _database(self, *, write=False):
        if self._workspace is None:
            raise ProjectCanvasError("project_canvas.unavailable")
        try:
            file = self._file if self._file is not None else settings_directory() / "project-canvas.sqlite3"
            with private_database(file, write=write) as database:
                if write:
                    database.execute(_POSITION_TABLE)
                    database.execute(_LINK_TABLE)
                elif database is not None:
                    database.execute("BEGIN")
                yield database
        except (OSError, sqlite3.Error, LibraryError):
            raise ProjectCanvasError("project_canvas.unavailable") from None

    def snapshot(self):
        with self._database() as database:
            return _read(database, self._workspace)

    def apply(self, document, eligible_claim_ids):
        action, endpoints, coordinates = _operation(document)
        if action != "unlink" and not set(endpoints).issubset(eligible_claim_ids(endpoints)):
            raise ProjectCanvasError("project_canvas.missing")
        with self._database(write=True) as database:
            values = (self._workspace, *endpoints, *coordinates)
            if action == "move":
                database.execute("""
                    INSERT INTO canvas_positions (workspace_id, claim_id, x, y) VALUES (?, ?, ?, ?)
                    ON CONFLICT (workspace_id, claim_id) DO UPDATE SET x = excluded.x, y = excluded.y
                """, values)
            elif action == "link":
                database.execute("INSERT OR IGNORE INTO canvas_links (workspace_id, source, target) VALUES (?, ?, ?)",
                                 values)
            else:
                database.execute("DELETE FROM canvas_links WHERE workspace_id = ? AND source = ? AND target = ?", values)
            return _read(database, self._workspace)


def apply(store, canvas, document):
    return canvas.apply(document, lambda identifiers: proof_graph.visible_claim_ids(store, identifiers))
