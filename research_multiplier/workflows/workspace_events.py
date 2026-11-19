"""Read a resumable feed of committed events for one authenticated workspace."""

from contextlib import contextmanager
from datetime import timezone
from time import monotonic

from research_multiplier.storage.postgres import DatabaseOperationError


_MAX_CURSOR = (1 << 63) - 1
_PAGE_SIZE = 100
_HEARTBEAT_SECONDS = 10
_WAIT_SECONDS = 1


class WorkspaceEventError(ValueError):
    """A safe error code for the workspace event stream."""


def _installed(store):
    return store.query("human", """
        SELECT to_regprocedure('rm.workspace_event_cursor(uuid)') IS NOT NULL AS installed
    """)[0]["installed"]


def _head(store, workspace_id):
    return store.query("human", "SELECT rm.workspace_event_cursor(%s) AS id", workspace_id)[0]["id"]


def _require_user(store):
    try:
        user = store.local_user()
    except DatabaseOperationError:
        raise
    except ValueError:
        raise WorkspaceEventError("events.identity_required") from None
    if user is None:
        raise WorkspaceEventError("events.identity_required")


def capability(store):
    """Advertise the cursor visible in the same snapshot as the workbench inbox."""
    workspace_id = store.workspace_id
    if workspace_id is None or not _installed(store):
        return {}
    try:
        _require_user(store)
        cursor = _head(store, workspace_id)
    except WorkspaceEventError:
        return {}
    except DatabaseOperationError as error:
        if error.state == "42501":
            return {}
        raise
    return {"workspace_events": {"workspace_id": str(workspace_id), "cursor": str(cursor)}}


def _parse_cursor(after):
    if after is None:
        return None
    if isinstance(after, str) and 0 < len(after) <= 19 and after.isascii() and after.isdecimal():
        after = int(after)
    if type(after) is not int or not 0 <= after <= _MAX_CURSOR:
        raise WorkspaceEventError("events.invalid_cursor")
    return after


def _envelope(row):
    return {
        "id": str(row["id"]),
        "type": row["type"],
        "schema_version": row["schema_version"],
        "workspace_id": str(row["workspace_id"]),
        "occurred_at": row["occurred_at"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": {"kind": "user", "id": str(row["user_id"])},
        "subject": {"kind": row["subject_kind"], "id": str(row["subject_id"])},
        "data": row["data"],
    }


def _events(store, workspace_id, cursor, wait, closing):
    next_heartbeat = monotonic() + _HEARTBEAT_SECONDS
    while not closing.is_set():
        rows = store.query("human", "SELECT * FROM rm.read_workspace_events(%s,%s,%s)",
                           workspace_id, cursor, _PAGE_SIZE)
        for row in rows:
            if closing.is_set():
                return
            cursor = row["id"]
            yield _envelope(row)
            next_heartbeat = monotonic() + _HEARTBEAT_SECONDS
        if len(rows) == _PAGE_SIZE:
            continue
        remaining = next_heartbeat - monotonic()
        if remaining <= 0 and not closing.is_set():
            yield None
            remaining = _HEARTBEAT_SECONDS
            next_heartbeat = monotonic() + remaining
        if not closing.is_set():
            # Wake immediately on NOTIFY, or read the durable log after the timeout.
            wait(min(_WAIT_SECONDS, remaining))


@contextmanager
def subscribe(store, workspace_id, after, *, closing):
    """Validate the stream before headers, then replay in order and follow commits."""
    if store.workspace_id is None or workspace_id != store.workspace_id:
        raise WorkspaceEventError("events.not_found")
    cursor = _parse_cursor(after)
    try:
        if not _installed(store):
            raise WorkspaceEventError("events.unavailable")
        _require_user(store)
        with store.listen_workspace_events() as wait:
            # LISTEN must be active before the first snapshot to cover concurrent commits.
            latest = _head(store, workspace_id)
            if cursor is None:
                cursor = latest
            if cursor > latest:
                raise WorkspaceEventError("events.invalid_cursor")
            yield _events(store, workspace_id, cursor, wait, closing)
    except DatabaseOperationError as error:
        code = {"22023": "events.invalid_cursor", "42501": "events.identity_required"}.get(error.state)
        if code:
            raise WorkspaceEventError(code) from None
        raise
