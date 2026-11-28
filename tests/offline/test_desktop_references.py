from hashlib import sha256
import os
from stat import S_IMODE
from uuid import uuid4

import pytest

from research_multiplier.storage.managed_workspaces import LibraryError, publish_metadata
from research_multiplier.workflows import desktop_references, desktop_source


pytestmark = [
    pytest.mark.offline,
    pytest.mark.skipif(not desktop_source._DESCRIPTOR_IO, reason="requires POSIX directory descriptors"),
]


@pytest.fixture
def linked(tmp_path):
    workspace = tmp_path.resolve() / "app-workspace"
    workspace.mkdir()
    root = workspace.with_name("source")
    root.mkdir()
    binding = {"source_root": root, "source_identity": desktop_source.source_identity(root)}
    return workspace, root, binding


def _record(path):
    return {"id": str(uuid4()), "revision": str(uuid4()), "path": path, "sha256": "0" * 64}


def test_tracking_and_reconciliation_preserve_the_recorded_hash_and_original_tree(linked):
    workspace, root, binding = linked
    original = b"theorem draft\n"
    digest = sha256(original).hexdigest()
    file = root / "proof.txt"
    file.write_bytes(original)
    file.chmod(0o640)
    assert desktop_references.list_references(workspace) == []
    record = desktop_references.track_reference(workspace, binding, "proof.txt")
    metadata = workspace / desktop_references.REFERENCES_FILE
    snapshot = metadata.read_bytes()
    assert record["sha256"] == digest
    assert desktop_references.list_references(workspace) == [record]
    assert file.read_bytes() == original
    assert S_IMODE(file.stat().st_mode) == 0o640
    assert S_IMODE(metadata.stat().st_mode) == 0o600
    assert list(root.iterdir()) == [file]

    changed = b"external editor save\n"
    editor = root / "editor-save"
    editor.write_bytes(changed)
    editor.replace(file)
    assert desktop_references.track_reference(workspace, binding, "proof.txt") == record
    assert desktop_references.check_tracked_references(workspace, binding, [record["id"]]) == {
        "status": "available", "files": [{
            "path": "proof.txt", "status": "changed", "sha256": sha256(changed).hexdigest(),
            "reference_id": record["id"], "recorded_sha256": digest,
        }],
    }
    assert metadata.read_bytes() == snapshot
    assert file.read_bytes() == changed
    assert list(root.iterdir()) == [file]


def test_reconciliation_checks_only_the_chosen_locators_in_request_order(linked, monkeypatch):
    workspace, root, binding = linked
    (root / "one").write_bytes(b"unchanged")
    (root / "two").write_bytes(b"deleted")
    (root / "three").write_bytes(b"unselected")
    one, two, three = (desktop_references.track_reference(workspace, binding, path)
                       for path in ("one", "two", "three"))
    (root / "two").unlink()
    original_open = desktop_source._regular_file

    def only_selected(descriptor, path):
        assert path.as_posix() in {"one", "two"}
        return original_open(descriptor, path)

    monkeypatch.setattr(desktop_source, "_regular_file", only_selected)
    result = desktop_references.check_tracked_references(workspace, binding, [two["id"], one["id"]])
    assert result == {"status": "available", "files": [
        {"path": "two", "status": "missing", "reference_id": two["id"], "recorded_sha256": two["sha256"]},
        {"path": "one", "status": "unchanged", "sha256": one["sha256"], "reference_id": one["id"],
         "recorded_sha256": one["sha256"]},
    ]}
    assert desktop_references.list_references(workspace) == [one, two, three]


@pytest.mark.parametrize("value", [[], "missing", ["not-a-uuid"], ["duplicate"], ["oversize"]])
def test_reconciliation_validates_batch_before_opening_source(linked, monkeypatch, value):
    workspace, _, binding = linked
    values = {
        "duplicate": [str(uuid4())] * 2,
        "oversize": [str(uuid4()) for _ in range(17)],
    }
    identifiers = values.get(value[0], value) if value and isinstance(value, list) else value

    def unexpected_check(*args, **kwargs):
        pytest.fail("invalid or unknown IDs must not open the source")

    monkeypatch.setattr(desktop_references, "check_references", unexpected_check)
    with pytest.raises(LibraryError, match="storage-invalid"):
        desktop_references.check_tracked_references(workspace, binding, identifiers)
    with pytest.raises(LibraryError, match="storage-project"):
        desktop_references.check_tracked_references(workspace, binding, [str(uuid4())])


@pytest.mark.parametrize("invalid", ["document", "collection", "entry", "duplicate-id", "duplicate-path",
                                     "id", "revision", "path", "hash", "limit"])
def test_reference_record_codec_rejects_malformed_metadata(linked, invalid):
    workspace, _, _ = linked
    one, two = _record("one"), _record("two")
    document = {"references": [one, two]}
    if invalid == "document":
        document["extra"] = True
    elif invalid == "collection":
        document["references"] = {"one": one}
    elif invalid == "entry":
        one["contents"] = "unexpected"
    elif invalid == "duplicate-id":
        two["id"] = one["id"]
    elif invalid == "duplicate-path":
        two["path"] = one["path"]
    elif invalid == "limit":
        document["references"] = [_record(str(index)) for index in range(129)]
    else:
        field, value = {
            "id": ("id", "not-a-uuid"), "revision": ("revision", False),
            "path": ("path", "outside/../file"), "hash": ("sha256", "f" * 63),
        }[invalid]
        one[field] = value
    publish_metadata(workspace / desktop_references.REFERENCES_FILE, document)
    with pytest.raises(LibraryError, match="storage-invalid"):
        desktop_references.list_references(workspace)


def test_track_respects_metadata_limit_and_duplicate_is_idempotent(linked):
    workspace, _, binding = linked
    records = [_record(str(index)) for index in range(128)]
    metadata = workspace / desktop_references.REFERENCES_FILE
    publish_metadata(metadata, {"references": records})
    snapshot = metadata.read_bytes()
    assert desktop_references.track_reference(workspace, binding, "0") == records[0]
    with pytest.raises(LibraryError, match="storage-invalid"):
        desktop_references.track_reference(workspace, binding, "128")
    assert metadata.read_bytes() == snapshot


def test_track_refuses_unavailable_missing_and_unverified_input(linked):
    workspace, root, binding = linked
    (root / "one").write_bytes(b"authority")
    (root / "linked").symlink_to(root / "one")
    for path in ("missing", "linked"):
        with pytest.raises(LibraryError, match="storage-verification"):
            desktop_references.track_reference(workspace, binding, path)
    with pytest.raises(LibraryError, match="storage-invalid"):
        desktop_references.track_reference(workspace, binding, "one/../linked")
    with pytest.raises(LibraryError, match="storage-verification"):
        desktop_references.track_reference(workspace, {**binding, "source_identity": None}, "one")
    assert list(workspace.iterdir()) == []


def test_repair_reuses_identifier_and_hash_and_is_idempotent(linked):
    workspace, root, binding = linked
    old = root / "first"
    payload = b"unchanged after move"
    old.write_bytes(payload)
    record = desktop_references.track_reference(workspace, binding, "first")
    new = old.rename(root / "second")
    repaired = desktop_references.repair_reference(workspace, binding, record["id"], record["revision"], "second")
    assert repaired == {**record, "revision": repaired["revision"], "path": "second"}
    assert repaired["revision"] != record["revision"]
    metadata = workspace / desktop_references.REFERENCES_FILE
    snapshot = metadata.read_bytes()
    with pytest.raises(LibraryError, match="storage-stale"):
        desktop_references.repair_reference(workspace, binding, record["id"], record["revision"], "second")
    repeated = desktop_references.repair_reference(workspace, binding, record["id"], repaired["revision"], "second")
    assert repeated == repaired
    assert metadata.read_bytes() == snapshot
    assert desktop_references.list_references(workspace) == [repaired]
    assert new.read_bytes() == payload
    assert list(root.iterdir()) == [new]


def test_same_path_repair_checks_source_identity_without_opening_file(linked, monkeypatch):
    workspace, root, binding = linked
    (root / "original").write_bytes(b"same")
    saved = desktop_references.track_reference(workspace, binding, "original")

    def unexpected_file(*args, **kwargs):
        pytest.fail("same-path repair only needs to check the source identity")

    monkeypatch.setattr(desktop_source, "_regular_file", unexpected_file)
    args = (workspace, binding, saved["id"], saved["revision"], "original")
    assert desktop_references.repair_reference(*args) == saved
    root.rename(root.with_name("moved"))
    root.mkdir()
    with pytest.raises(LibraryError, match="storage-verification"):
        desktop_references.repair_reference(*args)
    assert desktop_references.list_references(workspace) == [saved]


@pytest.mark.parametrize(("condition", "code"), [
    ("already-tracked", "storage-destination"),
    ("old-present", "storage-stale"),
    ("stale-revision", "storage-stale"),
    ("different-content", "storage-verification"),
    ("missing-candidate", "storage-verification"),
    ("unapproved-root", "storage-verification"),
])
def test_repair_rejects_invalid_moves_without_changing_records(linked, condition, code):
    workspace, root, binding = linked
    original = root / "original"
    original.write_bytes(b"same")
    saved = desktop_references.track_reference(workspace, binding, "original")
    revision = saved["revision"] if condition != "stale-revision" else str(uuid4())
    if condition != "old-present":
        original.unlink()
    if condition != "missing-candidate":
        (root / "candidate").write_bytes(b"different" if condition == "different-content" else b"same")
    if condition == "already-tracked":
        desktop_references.track_reference(workspace, binding, "candidate")
    if condition == "unapproved-root":
        root.rename(root.with_name("moved"))
        root.mkdir()
        (root / "candidate").write_bytes(b"same")
    metadata = workspace / desktop_references.REFERENCES_FILE
    snapshot = metadata.read_bytes()

    with pytest.raises(LibraryError, match=code):
        desktop_references.repair_reference(workspace, binding, saved["id"], revision, "candidate")

    assert metadata.read_bytes() == snapshot


def test_repair_checks_for_restored_original_after_hashing_candidate(linked, monkeypatch):
    workspace, root, binding = linked
    old = root / "original"
    old.write_bytes(b"same")
    saved = desktop_references.track_reference(workspace, binding, "original")
    old.rename(root / "candidate")
    hash_file = desktop_source._hash_file

    def restore_original(descriptor, size):
        digest = hash_file(descriptor, size)
        if not old.exists():
            old.write_bytes(b"restored from an editor")
        return digest

    monkeypatch.setattr(desktop_source, "_hash_file", restore_original)
    with pytest.raises(LibraryError, match="storage-stale"):
        desktop_references.repair_reference(workspace, binding, saved["id"], saved["revision"], "candidate")
    assert desktop_references.list_references(workspace) == [saved]
    assert old.read_bytes() == b"restored from an editor"


def test_repair_rechecks_candidate_after_observing_missing_original(linked, monkeypatch):
    workspace, root, binding = linked
    original = root / "original"
    original.write_bytes(b"accepted")
    saved = desktop_references.track_reference(workspace, binding, "original")
    candidate = original.rename(root / "candidate")
    observe = desktop_source._observe

    def save_during_batch(descriptor, path, previous):
        if path.name == "original":
            replacement = root / "editor-save"
            replacement.write_bytes(b"replacement")
            replacement.replace(candidate)
        return observe(descriptor, path, previous)

    monkeypatch.setattr(desktop_source, "_observe", save_during_batch)
    with pytest.raises(LibraryError, match="storage-verification"):
        desktop_references.repair_reference(workspace, binding, saved["id"], saved["revision"], "candidate")
    assert desktop_references.list_references(workspace) == [saved]
    assert candidate.read_bytes() == b"replacement"


def test_failed_atomic_publication_leaves_repair_retryable(linked, monkeypatch):
    workspace, root, binding = linked
    old = root / "original"
    old.write_bytes(b"same")
    saved = desktop_references.track_reference(workspace, binding, "original")
    metadata = workspace / desktop_references.REFERENCES_FILE
    snapshot = metadata.read_bytes()
    old.rename(root / "candidate")
    replace = os.replace

    def interrupt(source, destination):
        if destination == metadata:
            raise OSError("simulated interruption before atomic publish")
        return replace(source, destination)

    with monkeypatch.context() as failure:
        failure.setattr(os, "replace", interrupt)
        with pytest.raises(OSError, match="before atomic publish"):
            desktop_references.repair_reference(workspace, binding, saved["id"], saved["revision"], "candidate")
    assert metadata.read_bytes() == snapshot
    assert list(workspace.iterdir()) == [metadata]

    repaired = desktop_references.repair_reference(workspace, binding, saved["id"], saved["revision"], "candidate")
    assert repaired["id"] == saved["id"]
    assert repaired["sha256"] == saved["sha256"]
    assert repaired["path"] == "candidate"
