"""Device-local locators for explicitly selected files in linked sources."""

from uuid import uuid4

from research_multiplier.storage.managed_workspaces import LibraryError, publish_metadata, read_metadata, uuid_text
from research_multiplier.workflows.desktop_source import (
    MAX_FILE_CHECKS, check_references, source_status, validate_reference_path, validate_reference_sha256,
)


REFERENCES_FILE = ".trellin-source-references.json"
MAX_REFERENCES = 128
_RECORD_FIELDS = {"id", "revision", "path", "sha256"}


def list_references(workspace):
    """Read the workspace's local locator metadata under the caller's library lock."""
    try:
        document = read_metadata(workspace / REFERENCES_FILE)
    except FileNotFoundError:
        return []
    if document.keys() != {"format_version", "references"}:
        raise LibraryError("storage-invalid")
    records = document["references"]
    if not isinstance(records, list) or len(records) > MAX_REFERENCES:
        raise LibraryError("storage-invalid")
    identifiers, paths = set(), set()
    for record in records:
        if not isinstance(record, dict) or record.keys() != _RECORD_FIELDS:
            raise LibraryError("storage-invalid")
        identifier = uuid_text(record["id"])
        uuid_text(record["revision"])
        path = validate_reference_path(record["path"])
        validate_reference_sha256(record["sha256"])
        if identifier in identifiers or path in paths:
            raise LibraryError("storage-invalid")
        identifiers.add(identifier)
        paths.add(path)
    return records


def _check(binding, requests):
    return check_references(binding["source_root"], requests, expected_identity=binding["source_identity"])


def _observed_files(binding, requests):
    report = _check(binding, requests)
    if report["status"] != "available":
        raise LibraryError("storage-verification")
    return report["files"]


def _reference_request(record):
    return {"path": record["path"], "sha256": record["sha256"]}


def _lookup(records, identifier):
    if identifier not in records:
        raise LibraryError("storage-project")
    return records[identifier]


def track_reference(workspace, binding, path):
    """Record a checked digest once. The caller holds the exclusive library lock."""
    name = validate_reference_path(path).as_posix()
    records = list_references(workspace)
    existing = next((record for record in records if record["path"] == name), None)
    if existing is not None:
        return existing
    if len(records) == MAX_REFERENCES:
        raise LibraryError("storage-invalid")
    observed, = _observed_files(binding, [{"path": name}])
    if observed["status"] != "available":
        raise LibraryError("storage-verification")
    record = {"id": str(uuid4()), "revision": str(uuid4()), "path": name, "sha256": observed["sha256"]}
    publish_metadata(workspace / REFERENCES_FILE, {"references": [*records, record]})
    return record


def check_tracked_references(workspace, binding, reference_ids):
    """Reconcile selected locators without changing their recorded fingerprints."""
    if not isinstance(reference_ids, list) or not 1 <= len(reference_ids) <= MAX_FILE_CHECKS:
        raise LibraryError("storage-invalid")
    identifiers = [uuid_text(identifier) for identifier in reference_ids]
    if len(set(identifiers)) != len(identifiers):
        raise LibraryError("storage-invalid")
    records = {record["id"]: record for record in list_references(workspace)}
    selected = [_lookup(records, identifier) for identifier in identifiers]
    report = _check(binding, [_reference_request(record) for record in selected])
    if report["status"] == "available":
        report["files"] = [
            {**row, "reference_id": record["id"], "recorded_sha256": record["sha256"]}
            for row, record in zip(report["files"], selected, strict=True)
        ]
    return report


def repair_reference(workspace, binding, reference_id, expected_reference_revision, path):
    """Change a missing locator's path while preserving its ID and recorded digest."""
    identifier = uuid_text(reference_id)
    expected = uuid_text(expected_reference_revision)
    name = validate_reference_path(path).as_posix()
    records = {record["id"]: record for record in list_references(workspace)}
    saved = _lookup(records, identifier)
    if expected != saved["revision"]:
        raise LibraryError("storage-stale")
    if name == saved["path"]:
        if source_status(binding["source_root"], binding["source_identity"]) != "available":
            raise LibraryError("storage-verification")
        return saved
    if any(record["path"] == name for record in records.values()):
        raise LibraryError("storage-destination")

    # Verify the candidate first so the old name is observed closer to publication.
    candidate, previous = _observed_files(binding, [
        {"path": name, "sha256": saved["sha256"]}, _reference_request(saved),
    ])
    if candidate["status"] != "unchanged":
        raise LibraryError("storage-verification")
    if previous["status"] != "missing":
        if previous["status"] in {"unchanged", "changed", "too_large"}:
            raise LibraryError("storage-stale")
        raise LibraryError("storage-verification")
    updated = {**saved, "path": name, "revision": str(uuid4())}
    records[identifier] = updated
    publish_metadata(workspace / REFERENCES_FILE, {"references": list(records.values())})
    return updated
