"""Prepare and check dependency graphs of reviewed Lean Core obligations."""

import re
from uuid import UUID

from ..general_lean import CHECKER
from research_multiplier.storage.postgres import Store

LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
MAX_BLUEPRINT_NODES = 32


def _under_scope(proof, premise_count):
    for start in range(0, premise_count, 24):
        proof = {"intro": {"count": min(24, premise_count - start), "body": proof}}
    return proof


def _proof(value, resolve, depth=0):
    if depth >= 128:
        raise ValueError("The blueprint proof exceeds the nesting limit.")
    if isinstance(value, list):
        return [_proof(child, resolve, depth + 1) for child in value]
    if isinstance(value, dict):
        if "node_ref" in value:
            key = value["node_ref"]
            if set(value) != {"node_ref"} or not isinstance(key, str) or not LABEL.fullmatch(key):
                raise ValueError("A blueprint reference needs one exact node name.")
            return resolve(key)
        return {key: _proof(child, resolve, depth + 1) for key, child in value.items()}
    return value


def _graph(document):
    if (not isinstance(document, dict) or set(document) != {"format_version", "nodes"}
            or type(document["format_version"]) is not int or document["format_version"] != 1
            or not isinstance(document["nodes"], list) or not 1 <= len(document["nodes"]) <= MAX_BLUEPRINT_NODES):
        raise ValueError("A blueprint needs up to 32 nodes in a version 1 document.")
    nodes = {}
    statements = set()
    for node in document["nodes"]:
        if (not isinstance(node, dict) or not {"name", "statement_id"} <= set(node) <= {"name", "statement_id", "proof"}
                or not isinstance(node["name"], str) or not LABEL.fullmatch(node["name"])
                or ("proof" in node and not isinstance(node["proof"], dict))):
            raise ValueError("Each node needs a name and Lean statement ID, with optional structured proof.")
        try:
            statement_id = str(UUID(node["statement_id"]))
        except (TypeError, ValueError, AttributeError):
            raise ValueError("Every blueprint node needs an exact Lean statement UUID.") from None
        name = node["name"]
        if name in nodes or statement_id in statements:
            raise ValueError("Blueprint nodes need distinct names and statement IDs.")
        depends_on = set()

        def require(reference):
            depends_on.add(reference)
            return None

        _proof(node.get("proof"), require)
        nodes[name] = {**node, "statement_id": statement_id, "requires": sorted(depends_on)}
        statements.add(statement_id)

    ordered = {}
    visiting = set()

    def visit(name):
        if name in ordered:
            return
        if name in visiting:
            raise ValueError("The Lean proof blueprint contains a cycle.")
        if name not in nodes:
            raise ValueError(f"The blueprint references a missing node: {name}.")
        visiting.add(name)
        node = nodes[name]
        for predecessor in node["requires"]:
            visit(predecessor)
        visiting.remove(name)
        ordered[name] = node

    for name in nodes:
        visit(name)
    return ordered


def _states(store, ids):
    return {str(row["id"]): row for row in store.query("verifier", """
        SELECT s.id, s.body -> 'checker' AS checker, s.approved, s.current, s.check_ready,
               jsonb_array_length(coalesce(s.body #> '{scope,lean_hypotheses}', '[]'::jsonb))
                 + jsonb_array_length(coalesce(s.body #> '{scope,lean_parameters}', '[]'::jsonb)) AS premise_count,
               receipt.id AS evidence_id, receipt.needs_recheck, receipt.review_status,
               receipt.theorem_version_id, receipt.theorem_format, receipt.promoted
        FROM rm.lean_statement_status s
        JOIN rm.claims claim ON claim.id = s.claim_id
        LEFT JOIN LATERAL (
            SELECT e.id, e.needs_recheck, e.review_status, th.id AS theorem_version_id,
                   th.body ->> 'format' AS theorem_format,
                   (claim.status = 'proved' AND e.id = claim.decisive_evidence_id) AS promoted
            FROM rm.lean_check_status checked
            JOIN rm.evidence_foundation_status e ON e.lean_check_id = checked.id
            LEFT JOIN rm.current_lean_core_theorems th ON th.evidence_id = e.id
                AND th.body ->> 'format' IN ('lean-core-proof', 'lean-native-proof')
            WHERE checked.statement_id = s.id AND e.kind = 'lean'
              AND e.verdict = 'verified' AND e.diff_status = 'exact'
            ORDER BY (th.id IS NULL), promoted DESC, e.needs_recheck, (e.review_status = 'returned'),
                     e.issued_at DESC, e.id
            LIMIT 1
        ) receipt ON true
        WHERE s.id = ANY(%s::uuid[])
    """, ids)}


def _status(node, state, reports):
    if state is None or state["checker"] != CHECKER:
        raise ValueError(f"Blueprint node {node['name']} must reference a Lean Core statement.")
    if not state["current"] or state["needs_recheck"]:
        return "needs_recheck"
    if not state["approved"]:
        return "awaiting_statement_review"
    if not state["check_ready"]:
        return "waiting_for_conditions"
    if state["theorem_version_id"]:
        return "available"
    if state["promoted"]:
        return "proved_without_export"
    if state["evidence_id"] and state["review_status"] != "returned":
        return "awaiting_promotion" if state["review_status"] == "approved" else "awaiting_evidence_review"
    dependencies = {reports[predecessor]["status"] for predecessor in node["requires"]}
    if dependencies & {"proved_without_export", "blocked_by_unexported_theorem"}:
        return "blocked_by_unexported_theorem"
    if dependencies - {"available"}:
        return "waiting_for_theorems"
    return "ready" if "proof" in node else "awaiting_proof"


def blueprint(document, output, lean=None, *, work=False, limit=1, retry=False, mathlib_project=None):
    from ..composed_lean import NativeCompositionBlocked, select_runtime
    from ..final_lean_local import check
    from research_multiplier.workflows.lean_workflow import _statement

    nodes = _graph(document)
    if type(limit) is not int or not 1 <= limit <= MAX_BLUEPRINT_NODES:
        raise ValueError("Choose a blueprint work limit between 1 and 32.")
    with Store() as store:
        states = _states(store, [node["statement_id"] for node in nodes.values()])
    reports = {}
    started = 0
    for name, node in nodes.items():
        state = states.get(node["statement_id"])
        status = _status(node, state, reports)
        report = {key: node[key] for key in ("name", "statement_id", "requires")}
        report.update(status=status, evidence_id=state["evidence_id"], theorem_version_id=state["theorem_version_id"])
        if state["theorem_format"] == "lean-native-proof":
            report["theorem_format"] = state["theorem_format"]
        reports[name] = report
        if work and status == "ready" and started < limit:
            started += 1
            proof = _proof(node["proof"], lambda ref: {"ref": str(reports[ref]["theorem_version_id"])})
            if (set(node["proof"]) == {"node_ref"} and state["premise_count"]
                    and reports[node["proof"]["node_ref"]].get("theorem_format") == "lean-native-proof"):
                proof = _under_scope(proof, state["premise_count"])
            try:
                with Store() as store:
                    statement = _statement(store, "verifier", node["statement_id"])
                    selected = select_runtime(store, statement, lean=lean, mathlib_project=mathlib_project)
                checked = check(node["statement_id"], {"format_version": 1, "proof": proof}, output, lean, retry=retry,
                                mathlib_project=mathlib_project, checked_runtime=selected)
            except NativeCompositionBlocked as error:
                status = ("lake_scope_review_required" if error.kind == "lake_scope_review_required"
                          else "proof_search_blocked")
                report.update(status=status, detail=str(error), obligations=error.details)
            except (OSError, ValueError) as error:
                report.update(status="proof_failed", detail=str(error))
            else:
                report.update(status="awaiting_evidence_review", evidence_id=checked["evidence_id"])
    return {"nodes": list(reports.values())}
