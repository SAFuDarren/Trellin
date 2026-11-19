"""Share one clock and proof-worker liveness check across a workbench's event streams."""

from threading import Lock
from time import monotonic


# PostgreSQL releases advisory locks on connection loss without a row update.
# Probe only while a worker is present. Job deadlines need no regular probe.
_ACTIVE_WORKER_CHECK_SECONDS = 1.0


class RuntimeUpdates:
    def __init__(self, local_worker):
        self._local_worker = local_worker
        self._lock = Lock()
        self._state = None
        self._local = None
        self._next_check = 0.0
        self._cursor = -1
        self._version = 0

    def version(self, store, cursor):
        """Return a process-local revision of the read models affected by time or worker state."""
        with self._lock:
            now = monotonic()
            if cursor > self._cursor or now >= self._next_check:
                observed, = store.query("human", "SELECT * FROM rm.workbench_runtime_clock(%s)", store.workspace_id)
                state = (observed["month"], observed["leased_jobs"], observed["waiting_jobs"], observed["lean_workers"])
                if state != self._state:
                    self._state = state
                    self._version += 1
                delay = (observed["next_at"] - observed["observed_at"]).total_seconds()
                if observed["lean_workers"]:
                    delay = min(delay, _ACTIVE_WORKER_CHECK_SECONDS)
                self._next_check = now + max(0.0, delay)
                self._cursor = max(cursor, self._cursor)
            local = self._local_worker()
            if local != self._local:
                self._local = local
                self._version += 1
            return self._version
