"""Read and edit durable workbench references without changing frozen claims."""

import re
from uuid import UUID

from research_multiplier.storage.postgres import DatabaseOperationError


class ClaimCodeError(ValueError):
    """Fixed error codes for research references."""


_CODE = re.compile(r"[A-Z][A-Z0-9]{1,11}", re.ASCII)
_REFERENCE = re.compile(r"[A-Z][A-Z0-9]{1,11}(?:-[0-9]{1,10})?", re.ASCII)


def _normalized(value, pattern):
    if not isinstance(value, str) or not value.isascii():
        raise ClaimCodeError("claim_code.invalid")
    code = value.strip().upper()
    if not pattern.fullmatch(code):
        raise ClaimCodeError("claim_code.invalid")
    return code


def available(store):
    return store.query("human", """
        SELECT to_regprocedure('rm.claim_reference_snapshot(uuid)') IS NOT NULL AS installed
    """)[0]["installed"]


def normalize_code(value):
    return _normalized(value, _CODE)


def snapshot(store, claim_id):
    if not available(store):
        return None
    rows = store.query("human", "SELECT * FROM rm.claim_reference_snapshot(%s)", claim_id)
    return rows[0] if rows else None


def _require_available(store):
    if not available(store):
        raise ClaimCodeError("claim_code.upgrade_required")


def set_code(store, claim_id, value):
    code = normalize_code(value)
    _require_available(store)
    try:
        rows = store.query("human", "SELECT rm.set_claim_code(%s,%s) AS code", claim_id, code)
    except DatabaseOperationError as error:
        if error.state == "23505":
            raise ClaimCodeError("claim_code.exists") from None
        if error.state == "P0002":
            raise ClaimCodeError("claim_code.missing") from None
        raise
    return {"code": rows[0]["code"]}


def rename(store, document):
    if not isinstance(document, dict) or document.keys() != {"claim_id", "code"}:
        raise ClaimCodeError("claim_code.invalid")
    try:
        claim_id = UUID(document["claim_id"])
    except (AttributeError, TypeError, ValueError):
        raise ClaimCodeError("claim_code.invalid") from None
    return set_code(store, claim_id, document["code"])


def resolve(store, reference):
    code = _normalized(reference, _REFERENCE)
    _require_available(store)
    rows = store.query("human", "SELECT * FROM rm.resolve_claim_code(%s)", code)
    if not rows:
        raise ClaimCodeError("claim_code.missing")
    return {"claim_id": str(rows[0]["claim_id"]), "node_id": rows[0]["node_key"]}
