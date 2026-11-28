"""Observe only the selected file locators of an active desktop project."""

from copy import deepcopy
from threading import Event, Lock, Thread

from research_multiplier.storage.managed_workspaces import LibraryError
from research_multiplier.workflows.desktop_source import MAX_FILE_CHECKS, check_references, source_status


SAMPLE_INTERVAL = 1.0


class SourceObserver:
    """One bounded, read-only observer for the lifetime of a native workbench."""

    def __init__(self, library, project_id, *, interval=SAMPLE_INTERVAL):
        self._library = library
        self._project = project_id
        self._interval = interval
        self._stop = Event()
        self._lock = Lock()
        self._worker = Thread(target=self._run, name="trellin-source-observer", daemon=True)
        self._scope = None
        self._cache = {}
        self._results = {}
        self._offset = 0
        self._snapshot = self._result("checking", None, []) | {"generation": 0}

    def __enter__(self):
        self._worker.start()
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._lock:
            self._stop.set()
        # A blocked filesystem call must not prevent the native app closing.
        # The worker cannot publish after stop and exits with the process.
        self._worker.join(timeout=self._interval)

    def snapshot(self):
        with self._lock:
            return deepcopy(self._snapshot)

    def _result(self, status, revision, files):
        return {"source_id": self._project, "revision": revision, "status": status, "files": files}

    def _publish(self, status, revision, files):
        result = self._result(status, revision, files)
        with self._lock:
            if self._stop.is_set():
                return
            current = self._snapshot
            if result != {key: current[key] for key in result}:
                self._snapshot = result | {"generation": current["generation"] + 1}

    def _clear(self):
        self._cache.clear()
        self._results.clear()
        self._offset = 0

    def _run(self):
        while not self._stop.is_set():
            try:
                self._library.observe_source_references(self._project, self._sample)
            except LibraryError as error:
                if error.code != "storage-busy":
                    self._scope = None
                    self._clear()
                    self._publish("unavailable", None, [])
            if self._stop.wait(self._interval):
                return

    def _sample(self, revision, binding, records):
        if self._stop.is_set():
            return
        scope = revision, tuple((record["id"], record["revision"]) for record in records)
        if scope != self._scope:
            self._scope = scope
            self._clear()
        if not records:
            status = source_status(binding["source_root"], binding["source_identity"])
            self._publish(status, revision, [])
            return

        batch = records[self._offset:self._offset + MAX_FILE_CHECKS]
        report = check_references(
            binding["source_root"], [{"path": record["path"], "sha256": record["sha256"]} for record in batch],
            expected_identity=binding["source_identity"], cache=self._cache,
        )
        if report["status"] != "available":
            self._clear()
            self._publish(report["status"], revision, [])
            return

        for record, row in zip(batch, report["files"], strict=True):
            self._results[record["id"]] = {
                "reference_id": record["id"], "recorded_sha256": record["sha256"],
                **{key: row[key] for key in ("status", "sha256") if key in row},
            }
        self._offset += len(batch)
        if self._offset == len(records):
            self._offset = 0
        if len(self._results) < len(records):
            self._publish("checking", revision, [])
        else:
            self._publish("available", revision, [self._results[record["id"]] for record in records])
