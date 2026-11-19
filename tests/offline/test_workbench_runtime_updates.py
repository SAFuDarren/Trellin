"""Exercise online invalidation at deadlines, on writes and on worker loss."""

from datetime import datetime, timedelta, timezone

import pytest

from research_multiplier.workbench import runtime_updates


pytestmark = pytest.mark.offline


class RuntimeStore:
    workspace_id = "workspace"
    observed_at = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def __init__(self):
        self.calls = 0
        self.delay = 5
        self.jobs = ("short-lease",)
        self.waiting = ("backoff",)
        self.workers = ()

    def query(self, role, sql, workspace_id):
        assert role == "human" and "rm.workbench_runtime_clock" in sql and workspace_id == self.workspace_id
        self.calls += 1
        return [{
            "observed_at": self.observed_at, "next_at": self.observed_at + timedelta(seconds=self.delay),
            "month": self.observed_at.date(), "leased_jobs": self.jobs,
            "waiting_jobs": self.waiting, "lean_workers": self.workers,
        }]


def test_runtime_watch_shares_snapshots_and_only_signals_changed_state(monkeypatch):
    now = [0.0]
    local = [(None, None)]
    monkeypatch.setattr(runtime_updates, "monotonic", lambda: now[0])
    store = RuntimeStore()
    watch = runtime_updates.RuntimeUpdates(lambda: local[0])
    initial = watch.version(store, 3)
    assert store.calls == 1

    now[0] = 4
    assert watch.version(store, 3) == initial
    assert watch.version(store, 2) == initial  # An older subscriber shares the clock.
    assert store.calls == 1

    now[0] = 5
    store.jobs = ()
    next_version = watch.version(store, 3)
    assert next_version > initial and store.calls == 2

    local[0] = ("local", None)
    local_version = watch.version(store, 3)
    assert local_version > next_version and store.calls == 2

    store.workers = ("remote",)
    active = watch.version(store, 4)
    assert active > local_version and store.calls == 3

    now[0] = 6
    store.workers = ()
    ended = watch.version(store, 4)
    assert ended > active and store.calls == 4

    now[0] = 8
    assert watch.version(store, 4) == ended and store.calls == 4
    assert watch.version(store, 5) == ended and store.calls == 5  # A write alone is not a time change.
