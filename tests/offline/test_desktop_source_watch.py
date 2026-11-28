from threading import Event

import pytest

from research_multiplier.workflows import desktop_source_watch


pytestmark = pytest.mark.offline


def _references(count):
    return [
        {"id": str(index), "revision": "current", "path": f"note-{index}.md", "sha256": "a" * 64}
        for index in range(count)
    ]


def test_observer_batches_observations_and_drops_previous_binding(monkeypatch):
    batches = []
    state = {"status": "available"}

    def check(root, references, *, expected_identity, cache):
        batches.append(len(references))
        assert len(references) <= desktop_source_watch.MAX_FILE_CHECKS
        if state["status"] != "available":
            return {"status": state["status"], "files": []}
        return {"status": "available", "files": [
            {"path": ref["path"], "status": "unchanged", "sha256": ref["sha256"]} for ref in references
        ]}

    monkeypatch.setattr(desktop_source_watch, "check_references", check)
    observer = desktop_source_watch.SourceObserver(None, "linked")
    records = _references(17)
    binding = {"source_root": object(), "source_identity": object()}
    observer._sample("original", binding, records)
    assert observer.snapshot() == {
        "source_id": "linked", "revision": "original", "status": "checking", "files": [], "generation": 1,
    }
    observer._sample("original", binding, records)
    initial = observer.snapshot()
    assert initial["status"] == "available"
    assert len(initial["files"]) == len(records)
    assert set(initial["files"][0]) == {"reference_id", "recorded_sha256", "status", "sha256"}
    observer._sample("original", binding, records)
    assert observer.snapshot() == initial
    observer._sample("replacement", binding, records)
    assert observer.snapshot()["files"] == []
    assert observer.snapshot()["revision"] == "replacement"
    state["status"] = "identity_changed"
    observer._sample("replacement", binding, records)
    assert observer.snapshot()["status"] == "identity_changed"
    assert observer.snapshot()["files"] == []
    assert batches == [16, 1, 16, 16, 1]


def test_stopping_observer_prevents_late_publication():
    entered, resume, completed = Event(), Event(), Event()

    class BlockedLibrary:
        def observe_source_references(self, _project, sample):
            entered.set()
            try:
                assert resume.wait(2)
                sample("late", {}, [])
            finally:
                completed.set()

    observer = desktop_source_watch.SourceObserver(BlockedLibrary(), "linked", interval=0.01)
    try:
        with observer:
            assert entered.wait(2)
    finally:
        resume.set()
    assert completed.wait(2)
    assert observer.snapshot() == {
        "source_id": "linked", "revision": None, "status": "checking", "files": [], "generation": 0,
    }
