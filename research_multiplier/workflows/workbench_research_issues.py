"""Preview and explicitly offer author-private research Work in the bound workspace."""

import re
from uuid import UUID

from research_multiplier.storage.postgres import DatabaseOperationError

from . import proof_graph, workbench_proof_drafts


MAX_REQUEST_BYTES = 65_536
_FIELDS = {"title", "deliverable", "acceptance_criteria", "required_capabilities"}
_CAPABILITY = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


class ResearchIssuePreviewError(ValueError):
    """A stable Work request failure without private input in the message."""


def _text(value, limit):
    if not isinstance(value, str) or not 1 <= len(value) <= limit or not value.strip() or "\0" in value:
        raise ResearchIssuePreviewError("research_issue.invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ResearchIssuePreviewError("research_issue.invalid") from None
    return value.strip()


def _uuid(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        identifier = UUID(value)
        if str(identifier) != value:
            raise ValueError
        return identifier
    except ValueError:
        raise ResearchIssuePreviewError("research_issue.invalid") from None


def _capabilities(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 16
            or any(not isinstance(item, str) or len(item) > 64 or not _CAPABILITY.fullmatch(item)
                   for item in value)
            or len(set(value)) != len(value)):
        raise ResearchIssuePreviewError("research_issue.invalid")
    return sorted(value)


def _note_reference(source):
    if not isinstance(source, dict) or source.keys() != {"claim_id", "node_id", "draft_revision"}:
        raise ResearchIssuePreviewError("research_issue.invalid")
    _uuid(source["claim_id"])
    _uuid(source["draft_revision"])
    node_id = source["node_id"]
    if _text(node_id, 128) != node_id:
        raise ResearchIssuePreviewError("research_issue.invalid")
    return source


def _from_note(store, source):
    try:
        saved = workbench_proof_drafts.read(store, UUID(source["claim_id"]), source["node_id"])["draft"]
    except workbench_proof_drafts.ProofDraftError as error:
        code = "source_unavailable" if str(error) == "proof_draft_unavailable" else "not_found"
        raise ResearchIssuePreviewError(f"research_issue.{code}") from None
    if saved is None:
        raise ResearchIssuePreviewError("research_issue.not_found")
    if str(saved["revision"]) != source["draft_revision"]:
        raise ResearchIssuePreviewError("research_issue.stale")
    return _text(saved["text"], 6000)


def _direction(document):
    if not isinstance(document, dict):
        raise ResearchIssuePreviewError("research_issue.invalid")
    source_fields = document.keys() - _FIELDS
    if not _FIELDS <= document.keys() or source_fields not in (
        {"question"}, {"question", "claim_id"}, {"proof_note"},
    ):
        raise ResearchIssuePreviewError("research_issue.invalid")
    draft = {
        "title": _text(document["title"], 160),
        "deliverable": _text(document["deliverable"], 2000),
        "acceptance_criteria": _text(document["acceptance_criteria"], 4000),
        "required_capabilities": _capabilities(document["required_capabilities"]),
        "claim_id": None,
        "proof_note": None,
    }
    if "proof_note" in document:
        source = _note_reference(document["proof_note"])
        draft.update(claim_id=source["claim_id"], proof_note=source)
    else:
        draft["question"] = _text(document["question"], 6000)
        if "claim_id" in document:
            claim_id = _uuid(document["claim_id"])
            draft["claim_id"] = str(claim_id)
    return draft


def _resolve_direction(store, draft):
    if draft["proof_note"] is not None:
        return {**draft, "question": _from_note(store, draft["proof_note"])}
    if draft["claim_id"] is not None:
        try:
            proof_graph.snapshot(store, UUID(draft["claim_id"]), references=False)
        except proof_graph.ClaimGraphNotFound:
            raise ResearchIssuePreviewError("research_issue.not_found") from None
    return draft


def _require_author(store):
    if not store.local_user():
        raise ResearchIssuePreviewError("research_issue.identity_required")


def preview(store, document):
    _require_author(store)
    return {"draft": _resolve_direction(store, _direction(document))}


_SHA256 = re.compile(r"[0-9a-f]{64}")


def installed(store):
    return store.query("human", """
        SELECT to_regprocedure('rm.offer_research_issue(uuid,uuid,jsonb,jsonb)') IS NOT NULL AS installed
    """)[0]["installed"]


def _require_storage(store):
    _require_author(store)
    if not installed(store):
        raise ResearchIssuePreviewError("research_issue.storage_unavailable")


def _requested_inputs(value, workspace_id):
    if not isinstance(value, list) or len(value) > 16:
        raise ResearchIssuePreviewError("research_issue.invalid")
    pins = {}
    for item in value:
        if not isinstance(item, dict) or item.keys() != {"reference_id", "sha256"}:
            raise ResearchIssuePreviewError("research_issue.invalid")
        identifier = str(_uuid(item["reference_id"]))
        digest = item["sha256"]
        if identifier in pins or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ResearchIssuePreviewError("research_issue.invalid")
        pins[identifier] = {"source_id": str(workspace_id), "reference_id": identifier, "sha256": digest}
    return [pins[key] for key in sorted(pins)]


def _observed_files(workspace_id, observations):
    if (not observations or observations.get("source_id") != str(workspace_id)
            or not observations.get("revision") or observations.get("status") != "available"):
        return {}
    return {row["reference_id"]: row for row in observations["files"]}


def _input_status(pin, workspace_id, files):
    row = files.get(pin["reference_id"])
    if pin["source_id"] != str(workspace_id) or row is None:
        return "source_unavailable"
    if row["status"] == "missing":
        return "needs_recheck"
    digest = row.get("sha256")
    if (row["status"] not in {"unchanged", "changed", "available"}
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
        return "source_unavailable"
    return "ready" if digest == pin["sha256"] else "needs_recheck"


def _receipt(row):
    return {**row, "id": str(row["id"]), "created_at": row["created_at"].isoformat()}


def _read_offers(store, offer_id=None):
    return store.query("human", "SELECT * FROM rm.read_research_offers(%s,%s)", store.workspace_id, offer_id)


def offer(store, document, resolve_inputs):
    """Record an explicit offer in the caller's human transaction, without starting work."""
    _require_storage(store)
    if not isinstance(document, dict) or document.keys() not in (
        {"offer_id", "direction"}, {"offer_id", "direction", "inputs"},
    ):
        raise ResearchIssuePreviewError("research_issue.invalid")
    offer_id = _uuid(document["offer_id"])
    direction = _direction(document["direction"])
    pins = _requested_inputs(document.get("inputs", []), store.workspace_id)

    # A later retry can outlive an edit, a disconnected source, or a discarded note.
    # Serialize before deciding whether any live input needs to be resolved.
    store.query("human", "SELECT pg_advisory_xact_lock(hashtextextended(%s, 9085))", str(offer_id))
    existing = _read_offers(store, offer_id)
    if existing:
        row, = existing
        original = dict(row["direction"])
        if original["proof_note"] is not None:
            original.pop("question")
        if original != direction or row["inputs"] != pins:
            raise ResearchIssuePreviewError("research_issue.conflict")
        return {"issue": _receipt(row)}

    draft = _resolve_direction(store, direction)
    if pins:
        report = resolve_inputs([pin["reference_id"] for pin in pins])
        files = _observed_files(store.workspace_id, report)
        for pin in pins:
            status = _input_status(pin, store.workspace_id, files)
            if status == "needs_recheck":
                raise ResearchIssuePreviewError("research_issue.input_changed")
            if status == "source_unavailable":
                raise ResearchIssuePreviewError("research_issue.input_unavailable")
    try:
        store.query("human", "SELECT rm.offer_research_issue(%s,%s,%s,%s)",
                    store.workspace_id, offer_id, store.json(draft), store.json(pins))
    except DatabaseOperationError as error:
        codes = {"23505": "conflict", "23514": "stale", "P0002": "not_found"}
        if error.state not in codes:
            raise
        raise ResearchIssuePreviewError(f"research_issue.{codes[error.state]}") from None
    row, = _read_offers(store, offer_id)
    return {"issue": _receipt(row)}


def list_offers(store, observations):
    """Compare immutable issue inputs to a trusted observation of the current binding."""
    _require_storage(store)
    files = _observed_files(store.workspace_id, observations)
    issues = []
    for row in _read_offers(store):
        states = {_input_status(pin, store.workspace_id, files) for pin in row["inputs"]}
        status = next((state for state in ("needs_recheck", "source_unavailable") if state in states), "ready")
        issues.append({**_receipt(row), "input_status": status})
    return {"issues": issues}
