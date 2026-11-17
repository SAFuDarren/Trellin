"""Recheck a current local theorem under another confirmed Lean statement."""

from __future__ import annotations

import json

from .. import composed_lean, general_lean
from . import lean_proof_search, lean_search_components
from .. import local_lemmas
from .lean_blueprint import _under_scope
from .lean_decomposition import match_key
from research_multiplier.storage.postgres import DatabaseOperationError

MAX_REPLAYS = 3


def _parameters(hypotheses):
    # Legacy scope stores a plain list. The parameterized scope retains list behavior.
    return getattr(hypotheses, "parameters", ())


def _argument_count(hypotheses):
    return getattr(hypotheses, "argument_count", len(hypotheses))


def _premise_arguments(source, context, known):
    destination = context["antecedents"]
    source_parameters = _parameters(source)
    destination_parameters = _parameters(destination)
    # A typed header containing dependent variables is retained in full.
    # Destination proof validation remains necessary even when the headers match.
    if source_parameters and source_parameters != destination_parameters:
        return None
    source_offset = _argument_count(source) - len(source)
    destination_offset = _argument_count(destination) - len(destination)
    available = {match_key(hypothesis): {"use": destination_offset + index}
                 for index, hypothesis in enumerate(destination)}
    arguments = [{"use": index} for index in range(source_offset)]
    for hypothesis in source:
        proof = available.get(match_key(hypothesis))
        if proof is None:
            proposal = lean_proof_search.candidate(hypothesis, context, known)
            if proposal is None:
                proposal = lean_proof_search.telescope_candidate(
                    hypothesis, context, known, require_quantified_fact=True,
                )
            if proposal is None:
                return None
            # The replay already introduces the exact destination scope. Keep
            # any additional binders belonging to the premise proof itself.
            proof = _open_scope(proposal, _argument_count(destination))
            if proof is None:
                return None
        arguments.append(proof)
    return arguments


def _move_binders(node, replace_use, introduced=0):
    """Preserve binder scopes while moving proof terms between telescopes."""
    if isinstance(node, list):
        return [_move_binders(item, replace_use, introduced) for item in node]
    if not isinstance(node, dict):
        return node
    if set(node) == {"use"}:
        return replace_use(node["use"], introduced)
    if set(node) == {"intro"}:
        count = node["intro"]["count"]
        return {"intro": {"count": count, "body": _move_binders(
            node["intro"]["body"], replace_use, introduced + count)}}
    return {key: _move_binders(value, replace_use, introduced) for key, value in node.items()}


def _open_scope(proof, remaining):
    while remaining:
        intro = proof.get("intro")
        if not intro:
            return None
        count, proof = intro["count"], intro["body"]
        if count > remaining:
            proof = {"intro": {"count": count - remaining, "body": proof}}
        remaining -= min(remaining, count)
    return proof


def _under_new_scope(proof, source, destination, arguments):
    source_count = _argument_count(source)
    proof = _open_scope(proof, source_count)
    if proof is None:
        return None
    destination_count = _argument_count(destination)

    def substitute(index, introduced):
        if index >= source_count:
            return {"use": index + destination_count - source_count}
        return _move_binders(arguments[index], lambda use, _: {
            "use": use if use < destination_count else use + introduced,
        })

    return _under_scope(_move_binders(proof, substitute), destination_count)


def _source_graph(store, artifacts, statement_id, root, exact_basis):
    rows = store.query("verifier", "SELECT * FROM rm.lean_working_lemma_graph(%s,%s)",
                       statement_id, root["lemma_id"])
    by_id = {str(row["lemma_id"]): row for row in rows}
    root_id = str(root["lemma_id"])
    if root_id not in by_id or by_id[root_id]["submission"] != root["submission"]:
        raise ValueError("The source working theorem needs recheck or its dependency graph is unavailable.")

    ordered = {}
    visiting = set()

    def visit(identifier):
        if identifier in ordered:
            return
        if identifier in visiting or len(visiting) >= general_lean.MAX_REPLAY_DEPTH + 1:
            raise ValueError("The source working theorem has a cyclic or over-deep proof graph.")
        row = by_id.get(identifier)
        if row is None:
            raise ValueError("The source working theorem has a missing proof dependency.")
        if not set(row["basis_version_ids"]) <= exact_basis:
            raise ValueError("A source proof dependency is outside the destination's approved basis.")
        visiting.add(identifier)
        for dependency in row["local_lemma_ids"]:
            visit(dependency)
        archived = artifacts.read_json(row["submission"])
        if (not isinstance(archived, dict) or set(archived) != {"format_version", "proof"}
                or type(archived["format_version"]) is not int or archived["format_version"] != 1
                or not isinstance(archived["proof"], dict)):
            raise ValueError("A source working theorem has an invalid structured proof artifact.")
        references = set(local_lemmas._references(archived["proof"]))
        if references & by_id.keys() != set(row["local_lemma_ids"]):
            raise ValueError("The source working theorem differs from its recorded proof dependencies.")
        ordered[identifier] = {**row, "proof": archived["proof"]}
        visiting.remove(identifier)

    visit(root_id)
    return ordered


def _rebind_local_references(node, mapped, scope_count):
    if isinstance(node, list):
        return [_rebind_local_references(item, mapped, scope_count) for item in node]
    if not isinstance(node, dict):
        return node
    if set(node) == {"ref"} and node["ref"] in mapped:
        term = {"ref": mapped[node["ref"]]}
        for start in range(0, scope_count, 23):
            term = {"apply": [term, *({"use": index} for index in range(start, min(start + 23, scope_count)))]}
        return term
    return {key: _rebind_local_references(value, mapped, scope_count) for key, value in node.items()}


def seed(store, artifacts, statement, frozen, context, routes, *, checked_runtime, lean, mathlib_project,
         continue_work, retry):
    """Recheck prior working proofs in the destination before reserving a model call."""
    if not lean_search_components.enabled(lean_search_components.WORKING_REUSE):
        return [], checked_runtime
    pending = {match_key(item["proposition"]): item["proposition"]
               for route in routes if route["status"] == "awaiting_obligations"
               for item in route["obligations"] if not item["lemma_id"]}
    if not pending:
        return [], checked_runtime
    _, hypotheses = general_lean._target(json.loads(frozen["contract_bytes"]))
    exact_basis = {item["id"] for item in context["approved_theorems"] if item.get("application") != "native"}
    candidates = store.query("verifier", "SELECT * FROM rm.lean_working_lemma_candidates(%s,%s)",
                             statement["id"], list(pending.values()))
    candidates = [row for row in candidates if match_key(row["proposition"]) in pending
                  and set(row["basis_version_ids"]) <= exact_basis]
    if not candidates:
        return [], checked_runtime

    selected = checked_runtime
    scope_count = _argument_count(hypotheses)
    known = {str(row["id"]): row for row in local_lemmas._rows(store, statement["id"])}
    existing = {}
    for identifier, entry in known.items():
        source = entry["body"].get("working_lemma_replay")
        if source and entry["status"] == "verified":
            existing[source["lemma_id"], source["submission"]["sha256"], entry["proposition"]] = identifier
    subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": frozen["contract_sha256"]}
    contract = None
    results = []

    for row in candidates:
        if continue_work is not None and not continue_work():
            break
        key = match_key(row["proposition"])
        if key not in pending:
            continue
        proposition = pending[key]
        root_id = str(row["lemma_id"])
        result = {"source_lemma_id": root_id, "proposition": proposition}
        _, original_scope = general_lean._target(row["contract"])
        arguments = _premise_arguments(original_scope, context, known)
        if arguments is None:
            continue
        mapped = {}
        try:
            graph = _source_graph(store, artifacts, statement["id"], row, exact_basis)
            root_proof = _under_new_scope(graph[root_id]["proof"], original_scope, hypotheses, arguments)
            if root_proof is None:
                raise ValueError("The source working proof does not expose its premise arguments.")
            if contract is None:
                contract = artifacts.put(frozen["contract_bytes"], raw=True)
            selected = selected or composed_lean.select_runtime(
                store, statement, lean=lean, mathlib_project=mathlib_project
            )
            for identifier, source in graph.items():
                if continue_work is not None and not continue_work():
                    return results, selected
                if identifier == root_id:
                    target, proof = proposition, root_proof
                else:
                    # Preserve the source's whole function type. A parent may
                    # partially apply the lemma or supply different arguments.
                    target = general_lean._implication(source["proposition"], original_scope)
                    lifted = _move_binders(source["proof"], lambda use, _: {"use": use + scope_count})
                    proof = _under_scope(lifted, scope_count)
                replay_key = identifier, source["submission"]["sha256"], target
                if replay_key not in existing:
                    checked = local_lemmas._derive(store, artifacts, statement, subject, contract, known, {
                        "name": "ReplayedObligation" if identifier == root_id else "ReplayedPrerequisite",
                        "proposition": target,
                        "proof": _rebind_local_references(proof, mapped, scope_count),
                    }, {}, checked_runtime=selected, retry=retry, replay_origin={
                        "lemma_id": identifier, "submission": source["submission"],
                    })
                    destination_id = str(checked["id"])
                    known[destination_id] = checked
                    existing[replay_key] = destination_id
                mapped[identifier] = existing[replay_key]
        except composed_lean.NativeCompositionBlocked as error:
            if error.kind == "lake_scope_review_required":
                raise
            result.update(status="proof_search_blocked", obligations=error.details)
        except (OSError, ValueError) as error:
            if isinstance(error, DatabaseOperationError) and error.retryable:
                raise
            result.update(status="replay_unavailable", reason=str(error)[:500])
        else:
            pending.pop(key)
            result.update(status="lemma_verified", lemma_id=mapped[root_id])
            if len(mapped) > 1:
                result["replayed_dependencies"] = [
                    {"source_lemma_id": identifier, "lemma_id": destination_id}
                    for identifier, destination_id in mapped.items() if identifier != root_id
                ]
        results.append(result)
        if len(results) >= MAX_REPLAYS:
            break
    return results, selected
