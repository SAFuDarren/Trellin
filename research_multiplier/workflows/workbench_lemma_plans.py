"""Check scoped lemma plans from the local workbench using the existing Lean verifier."""

import re
from uuid import UUID

from research_multiplier import general_lean
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows import lean_verification


MAX_REQUEST_BYTES = 64_000
_MAX_PROOF_DEPTH = 32


class LemmaPlanError(ValueError):
    """A workbench message key safe to return to the browser."""


def _bounded_proof(proof):
    if not isinstance(proof, dict):
        return False
    pending = [(proof, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > _MAX_PROOF_DEPTH:
            return False
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    return True


def verify(store, document):
    if not isinstance(document, dict) or document.keys() != {"claim_id", "statement_id", "statement_sha256", "plan"}:
        raise LemmaPlanError("lemmaPlan.invalid")
    try:
        claim_id = UUID(document["claim_id"])
        statement_id = UUID(document["statement_id"])
    except (AttributeError, TypeError, ValueError):
        raise LemmaPlanError("lemmaPlan.invalid") from None
    checksum = document["statement_sha256"]
    plan = document["plan"]
    if (not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or not isinstance(plan, dict) or plan.keys() != {"format_version", "nodes"}
            or type(plan["format_version"]) is not int or plan["format_version"] != 1
            or not isinstance(plan["nodes"], list) or not 1 <= len(plan["nodes"]) <= 16
            or any(not isinstance(node, dict) or node.keys() != {"name", "proposition", "proof"}
                   or not _bounded_proof(node["proof"]) for node in plan["nodes"])):
        raise LemmaPlanError("lemmaPlan.invalid")
    try:
        identity = store.local_user()
    except DatabaseOperationError:
        raise
    except ValueError:
        raise LemmaPlanError("lemmaPlan.identity") from None
    if identity is None:
        raise LemmaPlanError("lemmaPlan.identity")
    scope = store.query("human", """
        SELECT id FROM rm.lean_statement_status
        WHERE id = %s AND claim_id = %s AND statement_sha256 = %s AND check_ready
          AND body ->> 'theorem' = %s
    """, statement_id, claim_id, checksum, general_lean.THEOREM)
    if not scope:
        raise LemmaPlanError("lemmaPlan.stale")
    try:
        result = lean_verification.derive_plan(statement_id, plan, retry=True)
    except DatabaseOperationError:
        raise
    except ValueError:
        raise LemmaPlanError("lemmaPlan.checkFailed") from None
    return {"verified": len(result["lemmas"])}
