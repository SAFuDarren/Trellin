"""Load the confirmed Lean scope and apply model disclosure rules."""

import json

from research_multiplier import general_lean, lean_core_reuse
from research_multiplier.workflows import lean_workflow
from research_multiplier.storage import paths
from . import basis


def _native_theorems(store, statement, *, model_visible):
    archive = paths.object_store()
    rows = store.query("verifier", "SELECT * FROM rm.lean_search_native_theorems(%s,%s)",
                       statement["packet_id"], model_visible)
    for row in rows:
        item = row["body"]
        checksum = item["source"]["replay"]["sha256"]
        if (archive / checksum).is_file():
            yield {"id": str(row["id"]), "proposition": general_lean._proposition(item["formal"]),
                   "antecedents": lean_core_reuse.antecedents(item), "application": "native"}


def _frozen_scope(store, statement_id, *, allow_restricted, private):
    statement = lean_workflow._statement(store, "verifier", statement_id)
    if not statement["check_ready"] or statement["body"].get("checker") != general_lean.CHECKER:
        raise ValueError("Confirm the current frozen Lean Core statement before searching.")
    frozen = store.frozen(statement["claim_id"])
    contract = json.loads(frozen["contract_bytes"])
    public = contract["sensitivity"] == "public"
    if private and public:
        raise ValueError("Use --confirm-public for a public Lean search scope.")
    if not public and not private and not allow_restricted:
        raise ValueError("Lean model search requires a public scope. Use a structured proof for a restricted scope.")
    if frozen["status"] != "open":
        raise ValueError("Lean model search requires an open research claim.")
    return statement, frozen, contract


def _context(store, statement, contract, *, model_visible):
    consequent, antecedents = general_lean._target(contract)
    external = statement["body"]["environment"].get("external")
    theorems = []
    for row in basis.versions(store, "verifier", statement["packet_id"]):
        item = row["body"]
        lean = (item.get("formal") or {}).get("lean")
        if item.get("kind") != "theorem" or not isinstance(lean, dict):
            continue
        if item.get("format") == "lean-core-proof":
            if (item["source"]["claim_id"] == str(statement["claim_id"])
                    or item["source"]["environment"].get("external") != external):
                continue
            conditions = lean_core_reuse.antecedents(item)
        elif lean.get("declaration"):
            conditions = []
        else:
            continue
        theorems.append({"id": row["id"], "proposition": general_lean._proposition(item["formal"]),
                         "antecedents": conditions})
    native = _native_theorems(store, statement, model_visible=model_visible)
    theorems.extend(sorted(native, key=lambda item: item["id"]))
    parameters = getattr(antecedents, "parameters", ())
    return {
        "consequent": consequent, "antecedents": antecedents, "approved_theorems": theorems,
        "approved_modules": external["modules"] if external else [],
        **({"parameters": list(parameters)} if parameters else {}),
    }


def authorize_model_dispatch(store, statement, *, private):
    """Enforce the database disclosure rules before dispatching a model job."""
    condition = ("SELECT rm.require_private_model_packet(%s)" if private
                 else "SELECT rm.require_public_model_packet(%s)")
    store.query("verifier", condition, statement["packet_id"])


def load_local_search_scope(store, statement_id, *, allow_restricted=False, private=False):
    """Load the frozen scope for program work without contacting a model."""
    statement, frozen, contract = _frozen_scope(store, statement_id, allow_restricted=allow_restricted,
                                                 private=private)
    context = _context(store, statement, contract, model_visible=not private and not allow_restricted)
    return statement, frozen, context


def load_model_search_scope(store, statement_id, *, private=False):
    """Check disclosure permission and return the exact current model context."""
    statement, frozen, contract = _frozen_scope(store, statement_id, allow_restricted=False, private=private)
    authorize_model_dispatch(store, statement, private=private)
    context = _context(store, statement, contract, model_visible=not private)
    return statement, frozen, context
