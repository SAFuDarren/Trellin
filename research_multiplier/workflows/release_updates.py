"""Check the official stable release using local application preferences."""

from contextlib import closing, contextmanager
from http import HTTPStatus
from http.client import HTTPException, HTTPSConnection
from importlib.metadata import PackageNotFoundError, version
import json
import re
import sqlite3
import time

from research_multiplier.storage import credentials


_REPOSITORY = "SAFuDarren/Trellin"
_RELEASE_PATH = f"/repos/{_REPOSITORY}/releases/latest"
_NOTES_URL = f"https://github.com/{_REPOSITORY}/releases/tag/"
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,8})(?:\.(?:0|[1-9][0-9]{0,8})){2}")
_AUTO_DELAY = 24 * 60 * 60
_RESPONSE_LIMIT = 256 * 1024
_DEFAULT_STATE = (False, None, "not_checked", None, 0)


class SettingsUnavailable(ValueError):
    """The local preference file could not be read or written."""


def _version(value):
    if isinstance(value, str) and _VERSION.fullmatch(value):
        return tuple(int(part) for part in value.split("."))
    return None


def _installed_version():
    try:
        return version("research-multiplier")
    except PackageNotFoundError:
        return None


def _latest_release():
    # This client has no access token, cookies, proxy configuration, redirects,
    # workspace context, or caller-supplied network destination.
    with closing(HTTPSConnection("api.github.com", timeout=4)) as connection:
        connection.request("GET", _RELEASE_PATH, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Trellin-Release-Check",
        })
        with closing(connection.getresponse()) as response:
            if response.status == HTTPStatus.NOT_FOUND:
                return "no_release", None
            if response.status != HTTPStatus.OK:
                return "unavailable", None
            payload = response.read(_RESPONSE_LIMIT + 1)
    if len(payload) > _RESPONSE_LIMIT:
        return "unavailable", None
    document = json.loads(payload)
    if not isinstance(document, dict):
        return "unavailable", None
    if document.get("draft") is not False or document.get("prerelease") is not False:
        return "no_release", None
    tag = document.get("tag_name")
    if not isinstance(tag, str) or _version(tag.removeprefix("v")) is None:
        return "unavailable", None
    return "release", tag


def _read(connection):
    return connection.execute("""
        SELECT automatic, last_checked_at, status, tag, check_number
        FROM release_state WHERE id = 1
    """).fetchone() or _DEFAULT_STATE


class ReleaseUpdates:
    """Application-wide settings and check reservations, shared by workspace servers."""

    def __init__(self, settings_file=None, *, installed_version=_installed_version, clock=time.time):
        self._file = settings_file or credentials.directory().parent / "release-updates.sqlite3"
        self._installed_version = installed_version
        self._clock = clock

    @contextmanager
    def _write(self):
        try:
            self._file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._file, timeout=1)) as connection, connection:
                # Claim the automatic check once even if several workspaces are
                # open in separate processes. The HTTPS call owns no database lock.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS release_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        automatic INTEGER NOT NULL DEFAULT 0,
                        last_checked_at INTEGER,
                        status TEXT NOT NULL DEFAULT 'not_checked',
                        tag TEXT,
                        check_number INTEGER NOT NULL DEFAULT 0
                    )
                """)
                connection.execute("CREATE TABLE IF NOT EXISTS dismissed_releases (version TEXT PRIMARY KEY)")
                connection.execute("INSERT OR IGNORE INTO release_state (id) VALUES (1)")
                yield connection
        except (OSError, sqlite3.Error):
            raise SettingsUnavailable("release_updates.settings_unavailable") from None

    def snapshot(self):
        state, dismissed = _DEFAULT_STATE, False
        installed = self._installed_version()
        try:
            if self._file.is_file():
                url = self._file.absolute().as_uri() + "?mode=ro"
                with closing(sqlite3.connect(url, uri=True, timeout=1)) as connection:
                    state = _read(connection)
                    if state[3] is not None:
                        dismissed = connection.execute(
                            "SELECT 1 FROM dismissed_releases WHERE version = ?", (state[3].removeprefix("v"),)
                        ).fetchone() is not None
        except (OSError, sqlite3.Error):
            state = (False, None, "unavailable", None, 0)
        automatic, checked, status, tag, _ = state
        available = None
        if status == "release":
            local = _version(installed)
            remote = _version(tag.removeprefix("v"))
            if local is None or remote is None:
                status = "unavailable"
            elif remote > local:
                status = "available"
                available = {"version": tag.removeprefix("v"), "url": _NOTES_URL + tag, "dismissed": dismissed}
            else:
                status = "current"
        return {"installed_version": installed, "automatic": bool(automatic), "last_checked_at": checked,
                "status": status, "available": available}

    def apply(self, document):
        if not isinstance(document, dict):
            raise ValueError("release_updates.invalid")
        action = document.get("action")
        if action in ("check", "auto_check") and document.keys() == {"action"}:
            self._check(automatic=action == "auto_check")
        elif (action == "automatic" and document.keys() == {"action", "enabled"}
              and isinstance(document["enabled"], bool)):
            with self._write() as connection:
                connection.execute("UPDATE release_state SET automatic = ? WHERE id = 1", (document["enabled"],))
        elif action == "dismiss" and document.keys() == {"action", "version"} and _version(document["version"]):
            with self._write() as connection:
                tag = _read(connection)[3]
                if tag is not None and tag.removeprefix("v") == document["version"]:
                    connection.execute("INSERT OR IGNORE INTO dismissed_releases VALUES (?)", (document["version"],))
        else:
            raise ValueError("release_updates.invalid")
        return self.snapshot()

    def _check(self, *, automatic):
        if automatic and not self._file.is_file():
            return  # Without persisted opt-in nothing is written or requested.
        if automatic and _version(self._installed_version()) is None:
            return
        now = int(self._clock())
        with self._write() as connection:
            enabled, checked, _, _, check_number = _read(connection)
            if automatic and (not enabled or (checked is not None and now - checked < _AUTO_DELAY)):
                return
            check_number += 1
            connection.execute("UPDATE release_state SET last_checked_at = ?, check_number = ? WHERE id = 1",
                               (now, check_number))
        try:
            status, tag = _latest_release() if _version(self._installed_version()) else ("unavailable", None)
        except (HTTPException, OSError, ValueError):
            status, tag = "unavailable", None
        with self._write() as connection:
            # A newer manual check may have finished first in another window.
            connection.execute("UPDATE release_state SET status = ?, tag = ? WHERE id = 1 AND check_number = ?",
                               (status, tag, check_number))
