"""Enter a theorem in the workbench using the ordinary human review path."""

import re
from uuid import UUID

from research_multiplier.protocol.serialization import fingerprint, json_text
from research_multiplier.workflows import claim_references, intake


class TheoremError(ValueError):
    """Fixed workbench error codes that do not quote research content."""


_LIBRARY_PREFIX = "workbench-theorem:"
_LIBRARY_ROWS = """
SELECT id, version_sha256, body, admitted, needs_recheck
FROM rm.formal_item_status
WHERE origin = 'human' AND body ->> 'kind' = 'theorem'
  AND starts_with(logical_key, 'workbench-theorem:')
  AND body #>> '{source,kind}' = 'human_entry'
  AND jsonb_typeof(body -> 'title') = 'string'
  AND jsonb_typeof(body #> '{formal,lean,proposition}') = 'string'
"""
_INVALID = "project.theorem.invalid"
_CONFIRMATION = "project.theorem.confirmation"
_MISSING = "project.theorem.missing"


def _text(value, maximum, *, required=True, error=_INVALID):
    if (not isinstance(value, str) or len(value) > maximum or "\x00" in value
            or (required and not value.strip())):
        raise TheoremError(error)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise TheoremError(error) from None
    return value


def _reviewer(document):
    return (_text(document.get("actor"), 200, error=_CONFIRMATION),
            _text(document.get("reason"), 2000, error=_CONFIRMATION))


def _receipt(record):
    if record["needs_recheck"]:
        raise TheoremError(_CONFIRMATION)
    return {
        "kind": "library",
        "status": "confirmed" if record["admitted"] else "draft",
        "id": str(record["id"]),
        "sha256": record["version_sha256"],
    }


def recent(store):
    """Read recent pending entries with the exact text needed for review."""
    rows = store.query("human", _LIBRARY_ROWS + """
        AND NOT admitted AND NOT needs_recheck
        ORDER BY created_at DESC, id DESC LIMIT 25
    """)
    return {"drafts": [
        {"id": str(row["id"]), "sha256": row["version_sha256"], "title": row["body"]["title"],
         "wording": row["body"]["natural"], "lean": row["body"]["formal"]["lean"]["proposition"],
         "source": row["body"]["source"].get("reference", "")}
        for row in rows
    ], **({"claim_codes": True} if claim_references.available(store) else {})}


def _library(store, title, wording, formal, source):
    body = {
        "kind": "theorem",
        "title": title,
        "natural": wording,
        "formal": formal,
        "source": {"kind": "human_entry", "reference": source},
    }
    key = _LIBRARY_PREFIX + fingerprint(body)
    rows = store.query("human", "SELECT * FROM rm.propose_formal_item(%s,%s)", key, store.json(body))
    recorded = store.query("human", _LIBRARY_ROWS + " AND id = %s", rows[0]["id"])[0]
    return _receipt(recorded)


def _goal(store, title, wording, formal, source, actor, reason, *, code=None):
    target = {"title": title, "domain": "lean-core", "sensitivity": "restricted",
              "statement": wording, "formal": formal}
    raw = json_text(target).encode("utf-8")
    if len(raw) > intake.MAX_TARGET_BYTES:
        raise TheoremError(_INVALID)
    preview = intake.preview_target(store, raw)
    checksum = preview["target_sha256"]
    # The target digest fences duplicate submits from different workbench windows.
    store.query("human", "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", checksum)
    frozen = intake.freeze_target(store, raw, checksum, actor, reason)
    result_code = {}
    if frozen["claim_id"] is None:
        # The form confirms a closed proposition. Any premises are part of the
        # submitted proposition itself. It does not select library items.
        notes = {"title": title, "domain": "lean-core", "sensitivity": "restricted",
                 "objective": wording, "items": [], "notes": source}
        draft = intake.draft(store, json_text(notes).encode("utf-8"), target_id=frozen["id"])
        intake.confirm(store, draft["id"], draft["intake_sha256"], {}, actor, reason)
        frozen = intake.target(store, frozen["id"])
        if code is not None:
            result_code = claim_references.set_code(store, frozen["claim_id"], code)
    elif code is not None:
        recorded = claim_references.snapshot(store, frozen["claim_id"])
        if not recorded:
            raise claim_references.ClaimCodeError("claim_code.upgrade_required")
        if recorded["code"] != code:
            raise claim_references.ClaimCodeError("claim_code.conflict")
        result_code = {"code": code}
    return {
        "kind": "goal",
        "status": "open",
        "id": str(frozen["claim_id"]),
        "claim_id": str(frozen["claim_id"]),
        "packet_id": str(frozen["packet_id"]),
        "target_id": str(frozen["id"]),
        **result_code,
    }


def add(store, document):
    """Save a library draft or freeze a human-reviewed goal with no added basis."""
    if not isinstance(document, dict):
        raise TheoremError(_INVALID)
    kind = document.get("kind")
    fields = {"kind", "title", "wording", "lean", "domain", "source"}
    if kind == "goal":
        fields |= {"confirmed", "actor", "reason", "code"}
    if (kind not in ("library", "goal") or set(document) - fields
            or document.get("domain", "lean-core") != "lean-core"):
        raise TheoremError(_INVALID)
    title = _text(document.get("title"), 300)
    wording = _text(document.get("wording"), 4000)
    proposition = _text(document.get("lean"), 16000)
    source = _text(document.get("source", ""), 2000, required=False)
    formal = {"lean": {"proposition": proposition}}
    if kind == "library":
        return _library(store, title, wording, formal, source)
    if document.get("confirmed") is not True:
        raise TheoremError(_CONFIRMATION)
    actor, reason = _reviewer(document)
    code = claim_references.normalize_code(document["code"]) if "code" in document else None
    return _goal(store, title, wording, formal, source, actor, reason, code=code)


def confirm(store, document):
    """Apply human confirmation to the exact recorded manual library version."""
    if not isinstance(document, dict) or set(document) != {"id", "sha256", "actor", "reason"}:
        raise TheoremError(_INVALID)
    try:
        identifier = UUID(_text(document["id"], 36))
    except ValueError:
        raise TheoremError(_INVALID) from None
    checksum = document["sha256"]
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise TheoremError(_INVALID)
    actor, reason = _reviewer(document)
    rows = store.query("human", _LIBRARY_ROWS + " AND id = %s", identifier)
    if not rows:
        raise TheoremError(_MISSING)
    if rows[0]["needs_recheck"] or rows[0]["version_sha256"] != checksum:
        raise TheoremError(_CONFIRMATION)
    store.query("human", "SELECT rm.approve_formal_item(%s,%s,%s,%s) AS id",
                identifier, checksum, actor, reason)
    return {"kind": "library", "status": "confirmed", "id": str(identifier), "sha256": checksum}
