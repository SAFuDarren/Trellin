# SPDX-License-Identifier: Apache-2.0
"""Check confirmed native intake against its source and keep a portable, immutable project input."""

from uuid import uuid4

from research_multiplier.protocol.serialization import fingerprint
from research_multiplier.storage.managed_workspaces import LibraryError, publish_metadata, read_metadata, uuid_text

from . import desktop_intake, desktop_intake_draft as proposal, desktop_pdf, desktop_source, intake


def digest(document):
    try:
        return fingerprint(document)
    except (TypeError, ValueError, RecursionError):
        raise LibraryError("storage-invalid") from None


def _fields(document, *keys):
    if not isinstance(document, dict) or document.keys() != set(keys):
        raise LibraryError("storage-invalid")


def references(draft, mode):
    _fields(draft, "title", "question", "sources", "items", "links")
    if not isinstance(draft["sources"], list):
        raise LibraryError("storage-invalid")
    pins = []
    for source in draft["sources"]:
        if not isinstance(source, dict) or "path" not in source or "sha256" not in source:
            raise LibraryError("storage-invalid")
        pins.append({"path": source["path"], "sha256": source["sha256"]})
    selected = desktop_source.validate_references(pins)
    if mode == "paper" and (len(selected) != 1 or selected[0][0].suffix.casefold() != ".pdf"):
        raise LibraryError("storage-invalid")
    if mode not in ("paper", "folder"):
        raise LibraryError("storage-invalid")
    return pins


def verify(root, identity, draft, pins):
    files = desktop_intake.preview(str(root), identity, pins)
    sources, passages = proposal._sources(files)
    if digest(sources) != digest(draft["sources"]):
        raise LibraryError("intake-changed")
    # Translate the editable evidence back to the proposal's passage identifiers.
    # The same validator then checks item IDs, kinds, wording, and dependency endpoints.
    origins = {digest(origin): key for key, (_, origin) in passages.items()}
    try:
        items = []
        for row in proposal._rows(draft["items"], 15):
            _fields(row, "id", "kind", "label", "source")
            source = row["source"]
            if not isinstance(source, dict) or "quote" not in source:
                raise LibraryError("storage-invalid")
            quote = proposal._text(source["quote"], 4000)
            origin = {key: value for key, value in source.items() if key != "quote"}
            passage = origins.get(digest(origin))
            if passage is None or quote not in passages[passage][0]:
                raise LibraryError("intake-changed")
            items.append({"id": row["id"], "kind": row["kind"], "label": row["label"],
                          "passage": passage, "quote": quote})
        links = []
        for link in proposal._rows(draft["links"], 30):
            _fields(link, "from", "to", "kind")
            if link["kind"] != "suggested_dependency":
                raise LibraryError("storage-invalid")
            links.append({"from": link["from"], "to": link["to"]})
        proposal._proposal({"title": draft["title"], "question": draft["question"], "items": items, "links": links},
                           sources, passages)
    except LibraryError as error:
        if error.code == "intake-model-output":
            raise LibraryError("storage-invalid") from None
        raise


def settle(root, identity, pins):
    """Check the same bytes and directory identity again after potentially slow provisioning."""
    with desktop_intake._selected_root(root, expected=identity) as (descriptor, _, existing):
        if existing is not None:
            raise LibraryError("workspace-occupied")
        observed = []
        remaining = desktop_pdf.MAX_RAW_BYTES + intake.MAX_MODEL_NOTES_BYTES
        try:
            for path, expected in desktop_source.validate_references(pins):
                with desktop_source._regular_file(descriptor, path) as (file, info):
                    if info.st_size > remaining or desktop_source._hash_file(file, info.st_size) != expected:
                        raise LibraryError("intake-changed")
                    remaining -= info.st_size
                    observed.append((path, info))
            for path, info in observed:
                desktop_source._verify_file(descriptor, path, info)
        except (OSError, desktop_source._ObservationError):
            raise LibraryError("intake-changed") from None


def reserve(file, request_hash, mode, root, identity):
    receipt = {"request_sha256": request_hash, "slot": str(uuid4()), "mode": mode,
               "source_root": str(root), "source_identity": identity}
    publish_metadata(file, receipt)
    return receipt["slot"]


def reserved_slot(file, request_hash, mode, root, identity):
    try:
        receipt = read_metadata(file)
    except FileNotFoundError:
        return None
    _fields(receipt, "format_version", "request_sha256", "slot", "mode", "source_root", "source_identity")
    if receipt["request_sha256"] != request_hash:
        raise LibraryError("storage-stale")
    if (receipt["mode"], receipt["source_root"], receipt["source_identity"]) != (mode, str(root), identity):
        raise LibraryError("storage-verification")
    return uuid_text(receipt["slot"])


def stored(workspace):
    inputs = workspace / "inputs"
    if inputs.is_symlink() or inputs.exists() and not inputs.is_dir():
        raise LibraryError("storage-verification")
    try:
        document = read_metadata(inputs / "project-intake.json")
    except FileNotFoundError:
        return None
    _fields(document, "format_version", "draft", "sha256")
    if not isinstance(document["draft"], dict) or document["sha256"] != digest(document["draft"]):
        raise LibraryError("storage-verification")
    return document["draft"]


def publish(workspace, draft):
    # Under the exclusive library lock, stored() has checked both the destination
    # and its parent. The input is committed before the marker or the registry.
    inputs = workspace / "inputs"
    inputs.mkdir(mode=0o700, exist_ok=True)
    publish_metadata(inputs / "project-intake.json", {"draft": draft, "sha256": digest(draft)})
