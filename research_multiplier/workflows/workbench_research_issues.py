"""Prepare a prospective Work issue from an authorized research question."""

import re
from uuid import UUID

from . import proof_graph, workbench_proof_drafts


MAX_REQUEST_BYTES = 65_536
_FIELDS = {"title", "deliverable", "acceptance_criteria", "required_capabilities"}
_CAPABILITY = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")


class ResearchIssuePreviewError(ValueError):
    """A stable preview failure without private input in the message."""


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


def _from_note(store, source):
    if not isinstance(source, dict) or source.keys() != {"claim_id", "node_id", "draft_revision"}:
        raise ResearchIssuePreviewError("research_issue.invalid")
    claim_id, revision = _uuid(source["claim_id"]), _uuid(source["draft_revision"])
    node_id = source["node_id"]
    if _text(node_id, 128) != node_id:
        raise ResearchIssuePreviewError("research_issue.invalid")
    try:
        saved = workbench_proof_drafts.read(store, claim_id, node_id)["draft"]
    except workbench_proof_drafts.ProofDraftError as error:
        code = "source_unavailable" if str(error) == "proof_draft_unavailable" else "not_found"
        raise ResearchIssuePreviewError(f"research_issue.{code}") from None
    if saved is None:
        raise ResearchIssuePreviewError("research_issue.not_found")
    if str(saved["revision"]) != str(revision):
        raise ResearchIssuePreviewError("research_issue.stale")
    return _text(saved["text"], 6000)


def preview(store, document):
    if not store.local_user():
        raise ResearchIssuePreviewError("research_issue.identity_required")
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
        source = document["proof_note"]
        draft["question"] = _from_note(store, source)
        draft.update(claim_id=source["claim_id"], proof_note=source)
    else:
        draft["question"] = _text(document["question"], 6000)
        if "claim_id" in document:
            claim_id = _uuid(document["claim_id"])
            try:
                proof_graph.snapshot(store, claim_id, references=False)
            except proof_graph.ClaimGraphNotFound:
                raise ResearchIssuePreviewError("research_issue.not_found") from None
            draft["claim_id"] = str(claim_id)
    return {"draft": draft}
