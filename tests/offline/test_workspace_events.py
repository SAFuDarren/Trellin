# SPDX-License-Identifier: Apache-2.0
"""Exercise event cursor and stream behavior without a running database."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Event
from uuid import UUID

import pytest

from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows import workspace_events as events


pytestmark = pytest.mark.offline

WORKSPACE = UUID("11111111-1111-4111-8111-111111111111")
USER = UUID("22222222-2222-4222-8222-222222222222")
EVIDENCE = UUID("33333333-3333-4333-8333-333333333333")


class EventStore:
    workspace_id = WORKSPACE

    def __init__(self, latest=0, *, installed=True, complete=False, user=USER, wait=lambda _timeout: False):
        self.latest = latest
        self.installed = installed
        self.complete = complete
        self.user = user
        self.wait = wait
        self.calls = []

    def local_user(self):
        self.calls.append("local_user")
        return {"id": self.user} if self.user else None

    def query(self, role, statement, *args):
        assert role == "human"
        if "rm.capture_workspace_change_event()" in statement:
            return [{"installed": self.complete}]
        if "to_regprocedure" in statement:
            return [{"installed": self.installed}]
        assert args[0] == self.workspace_id
        if "rm.workspace_event_cursor" in statement:
            self.calls.append("cursor")
            return [{"id": self.latest}]
        assert "rm.read_workspace_events" in statement
        _, after, limit = args
        self.calls.append(("page", after, limit))
        return [
            {
                "id": id_, "type": "evidence.reviewed", "schema_version": 1,
                "workspace_id": WORKSPACE,
                "occurred_at": datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=8))),
                "user_id": USER, "subject_kind": "evidence", "subject_id": EVIDENCE,
                "data": {"action": "approve"},
            }
            for id_ in range(after + 1, min(self.latest, after + limit) + 1)
        ]

    @contextmanager
    def listen_workspace_events(self):
        self.calls.append("listen")
        try:
            yield self.wait
        finally:
            self.calls.append("unlisten")


def test_capability_reports_the_current_cursor_only_for_an_eligible_workspace():
    assert events.capability(EventStore(19)) == {
        "workspace_events": {"workspace_id": str(WORKSPACE), "cursor": "19"},
    }
    assert events.capability(EventStore(installed=False)) == {}
    assert events.capability(EventStore(user=None)) == {}

    assert events.capability(EventStore(19, complete=True)) == {
        "workspace_events": {"workspace_id": str(WORKSPACE), "cursor": "19", "coverage": "workspace-writes-v1"},
    }


def test_unattributed_worker_event_keeps_system_provenance():
    row, = EventStore(1).query("human", "rm.read_workspace_events", WORKSPACE, 0, 10)
    row.update(type="evidence.recorded", user_id=None)
    assert events._envelope(row)["actor"] == {"kind": "system", "id": None}


@pytest.mark.parametrize("cursor", ["", "-1", "+1", "1 ", "١", str(1 << 63), "4", True])
def test_subscribe_rejects_invalid_cursor_before_exposing_an_iterator(cursor):
    with pytest.raises(events.WorkspaceEventError, match="^events.invalid_cursor$"):
        with events.subscribe(EventStore(3), WORKSPACE, cursor, closing=Event()):
            pytest.fail("Invalid cursor opened a stream")


def test_subscribe_rejects_foreign_and_unresolved_workspaces_before_listening():
    store = EventStore(user=None)
    for workspace, code in ((EVIDENCE, "events.not_found"), (WORKSPACE, "events.identity_required")):
        with pytest.raises(events.WorkspaceEventError, match=f"^{code}$"):
            with events.subscribe(store, workspace, None, closing=Event()):
                pytest.fail("Unauthenticated subscriber opened a stream")
    assert "listen" not in store.calls


def test_subscribe_replays_in_order_then_reads_committed_events_on_notification():
    store = EventStore(101)
    stop = Event()

    def notify(timeout):
        assert 0 < timeout <= 1
        store.latest = 102
        return True

    store.wait = notify
    with events.subscribe(store, WORKSPACE, "0", closing=stop) as stream:
        assert store.calls == ["local_user", "listen", "cursor"]
        first = next(stream)
        assert first == {
            "id": "1", "type": "evidence.reviewed", "schema_version": 1, "workspace_id": str(WORKSPACE),
            "occurred_at": "2026-01-01T04:00:00Z",
            "actor": {"kind": "user", "id": str(USER)},
            "subject": {"kind": "evidence", "id": str(EVIDENCE)},
            "data": {"action": "approve"},
        }
        assert [next(stream)["id"] for _ in range(101)] == [str(id_) for id_ in range(2, 103)]
        stop.set()
        assert next(stream, "ended") == "ended"
    assert store.calls[-1] == "unlisten"


def test_absent_cursor_starts_at_head_and_timeout_rechecks_the_log(monkeypatch):
    store = EventStore(7)
    clock = [0.0]
    stop = Event()

    def advance(timeout):
        assert 0 < timeout <= 1
        clock[0] += timeout
        store.latest = 8
        return False

    store.wait = advance
    monkeypatch.setattr(events, "monotonic", lambda: clock[0])
    with events.subscribe(store, WORKSPACE, None, closing=stop) as stream:
        assert next(stream)["id"] == "8"
        assert clock[0] == 1.0


def test_idle_subscriber_yields_heartbeats_only_after_ten_seconds(monkeypatch):
    clock = [0.0]

    def advance(timeout):
        assert 0 < timeout <= 1
        clock[0] += timeout

    monkeypatch.setattr(events, "monotonic", lambda: clock[0])
    with events.subscribe(EventStore(wait=advance), WORKSPACE, None, closing=Event()) as stream:
        assert next(stream) is None
        assert clock[0] == 10.0
        assert next(stream) is None
        assert clock[0] == 20.0


def test_database_connection_failure_is_distinct_from_missing_event_support():
    def disconnect(_timeout):
        raise DatabaseOperationError("human", "08006", retryable=True)

    store = EventStore(wait=disconnect)
    with pytest.raises(DatabaseOperationError, match="08006"):
        with events.subscribe(store, WORKSPACE, None, closing=Event()) as stream:
            next(stream)
    assert store.calls[-1] == "unlisten"
