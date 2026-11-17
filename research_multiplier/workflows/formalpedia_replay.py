"""Re-derive selected public statements under an existing confirmed Lean scope."""

from hashlib import sha256
from importlib.resources import files

from research_multiplier.proof.lean_blueprint import LABEL
from research_multiplier.proof.lean_decomposition import conditional_route_premises
from research_multiplier.proof.lean_proof_search import canonical
from research_multiplier.sources.formalpedia import FormalpediaSnapshot, replay_identity
from research_multiplier.sources.formalpedia_lean import (
    MAX_PROPOSITION_CHARS, exported_solution_type, exported_theorem,
)
from research_multiplier.storage.postgres import Store
from . import formalpedia_review
from .lean_verification import LocalLeanVerification, bind_plan_proof


def exported_proposition(text, name):
    """Return the plain source proposition for callers inspecting an export."""
    return exported_theorem(text, name).proposition


def _plan(document):
    if (not isinstance(document, dict) or not {"format_version", "nodes"} <= document.keys()
            <= {"format_version", "nodes", "goal_proof"}
            or type(document["format_version"]) is not int or document["format_version"] != 1
            or not isinstance(document["nodes"], list) or not 1 <= len(document["nodes"]) <= 16):
        raise ValueError("A Formalpedia replay plan needs 1 to 16 ordered nodes in a version 1 document.")
    names = set()
    theorems = set()

    def earlier(name):
        if name not in names:
            raise ValueError(f"Put Formalpedia node {name} before any proof that uses it.")

    for node in document["nodes"]:
        if (not isinstance(node, dict) or not {"name", "theorem"} <= node.keys()
                <= {"name", "theorem", "solution_id", "local_proposition", "proof"}
                or not isinstance(node["name"], str) or not LABEL.fullmatch(node["name"])
                or node["name"] in names or not isinstance(node["theorem"], str)
                or not node["theorem"].strip() or node["theorem"] in theorems
                or ("solution_id" in node and not isinstance(node["solution_id"], str))
                or ("local_proposition" in node and (not isinstance(node["local_proposition"], str)
                    or not node["local_proposition"].strip()
                    or len(node["local_proposition"]) > MAX_PROPOSITION_CHARS))
                or ("proof" in node and not isinstance(node["proof"], dict))):
            raise ValueError("Replay nodes need distinct names and theorems with optional exact solution and proof.")
        bind_plan_proof(node.get("proof"), earlier)
        names.add(node["name"])
        theorems.add(node["theorem"])
    if "goal_proof" in document:
        if not isinstance(document["goal_proof"], dict):
            raise ValueError("The optional goal proof must be a structured proof.")
        bind_plan_proof(document["goal_proof"], earlier)
    return document["nodes"], document.get("goal_proof")


def _comparison_declaration(label, theorem):
    namespace = theorem.namespace
    definition = f"def {label} : Prop := (\n{theorem.proposition}\n)"
    if namespace:
        definition = f"namespace {namespace}\n{definition}\nend {namespace}"
    reference = f"_root_.{namespace}.{label}" if namespace else f"_root_.{label}"
    return definition, reference


def _check_types(verification, selected):
    """Compare the exported stub, proof annotation, and local port in one runtime."""
    modules = verification.modules
    support = files("research_multiplier").joinpath("lean_support", "FormalpediaTypeInput.lean").read_bytes()
    imports = "".join(f"import {module}\n" for module in ("Lean", *modules))
    sources = {"FormalpediaTypeInput.lean": support}
    code = [imports, "set_option autoImplicit false", "set_option maxHeartbeats 200000"]
    upstream, destination = [], []
    for index, (stub, solution, local) in enumerate(selected):
        for declaration in (stub, solution):
            unknown = [module for module in declaration.imports if module not in modules and not (
                module in {"Lean", "Init", "Std", "Mathlib"}
                or module.startswith(("Lean.", "Init.", "Std.", "Mathlib.", "Theorems.Thm_"))
            )]
            if unknown:
                raise ValueError(f"The exported source imports {unknown[0]} outside the reviewed local context.")
        for tag, term, group in (("stub", stub.proposition, upstream), ("solution", solution.proposition, upstream),
                                 ("local", local, destination)):
            filename = f"type_{tag}_{index}.txt"
            group.append(filename)
            sources[filename] = term.encode("utf-8")
        stub_code, stub_ref = _comparison_declaration(f"rm_formalpedia_stub_{index}", stub)
        proof_code, proof_ref = _comparison_declaration(f"rm_formalpedia_proof_{index}", solution)
        local_name = f"rm_formalpedia_local_{index}"
        code.extend((stub_code, proof_code, f"def {local_name} : Prop := (\n{local}\n)",
                     f"example : {stub_ref} = {proof_ref} := rfl",
                     f"example : {stub_ref} = _root_.{local_name} := rfl"))
    sources["RMFormalpediaTypes.lean"] = "\n".join(code).encode("utf-8")
    validate_local = ["validate-imports", ",".join(modules)] if modules else ["validate"]
    status, diagnostic = verification.validate_types(sources, [
        ["--run", "FormalpediaTypeInput.lean", ",".join(modules), *upstream],
        ["--run", "TheoryAudit.lean", *validate_local, *destination],
        ["RMFormalpediaTypes.lean"],
    ])
    if status:
        raise ValueError("The local port did not match the exported proposition and proof type "
                         "in the selected Lean environment. " + diagnostic[-1200:])
    return sha256(support).hexdigest()


def _selection(node, selected, statement):
    identity = replay_identity(selected, statement)
    stub = exported_theorem(statement["text"], identity["theorem"])
    solution = exported_solution_type(selected["body"])
    return identity, stub, solution, node.get("local_proposition", stub.proposition)


def _provenance(selected, statement, proposition, checker, verification):
    entry, solution = selected["entry"], selected["solution"]
    artifacts = verification.artifacts
    return {
        "kind": "local_rederivation", "license": entry["license"],
        "upstream_lean": entry["toolchain"], "upstream_mathlib": entry["mathlib_rev"],
        "upstream_statement": {key: entry.get(key) for key in ("id", "author", "source", "url", "statement_path")},
        "upstream_solution": {key: solution.get(key) for key in ("id", "author", "url", "path")},
        "exported_statement": artifacts.put_bytes(statement["text"].encode("utf-8")),
        "exported_proof_body": artifacts.put_bytes(selected["body"].encode("utf-8")),
        "exported_solution_file_sha256": selected["file_sha256"],
        "index_entry_sha256": entry["index_entry_sha256"],
        "local_proposition": proposition,
        "type_comparison_checker_sha256": checker,
        "type_comparison_environment_sha256": verification.statement["body"]["environment"]["sha256"],
    }


def replay(statement_id, snapshot_path, environment, document, *, lean=None, mathlib_project=None,
           output=None, finish=False, retry=False):
    """Check a local source plan without model requests or changes to the basis."""
    nodes, requested_goal = _plan(document)
    with Store() as store:
        formalpedia_review.require_enabled(store)
        snapshot = FormalpediaSnapshot(snapshot_path)
        inputs = [(node["theorem"], node.get("solution_id")) for node in nodes]
        files = snapshot.replay_sources(environment, inputs)
        selections = [_selection(node, *sources) for node, sources in zip(nodes, files)]
        verification = LocalLeanVerification(store, statement_id, lean=lean, mathlib_project=mathlib_project,
                                             retry=retry)
        goal = verification.goal
        goal_indexes = [index for index, item in enumerate(selections) if canonical(item[-1]) == canonical(goal)]
        if goal_indexes and (goal_indexes != [len(nodes) - 1] or requested_goal is not None):
            raise ValueError("Put the frozen goal last and supply its proof only once.")
        checker = _check_types(verification, [item[1:] for item in selections])
        names = {}
        result = {"status": "verified", "statement_id": str(statement_id), "lemmas": []}
        direct_goal = None

        def resolve_node(name):
            return {"ref": names[name]}

        for index, (node, (selected, original), (identity, _, _, proposition)) in enumerate(
            zip(nodes, files, selections),
        ):
            proof = node.get("proof") or verification.suggest_proof(proposition)
            if proof is None:
                return {**result, "status": "port_needed", "accepted": False, "candidate": identity,
                        "proposition": proposition}
            provenance = _provenance(selected, original, proposition, checker, verification)
            if goal_indexes and index == goal_indexes[0]:
                direct_goal = (bind_plan_proof(proof, resolve_node), identity, provenance)
                break
            row = verification.check_lemma(
                {"name": node["name"], "proposition": proposition, "proof": proof}, names,
                on_receipt=lambda row: formalpedia_review.accept(store, statement_id, row["id"], identity, provenance),
            )
            names[node["name"]] = str(row["id"])
            result["lemmas"].append({"name": node["name"], "candidate": identity, "provenance": provenance, **row})

        if not (finish or requested_goal is not None or direct_goal is not None):
            return result
        if direct_goal is not None:
            proof, identity, provenance = direct_goal
        else:
            proof = (bind_plan_proof(requested_goal, resolve_node) if requested_goal is not None
                     else verification.suggest_proof(goal))
        if proof is None:
            blockers = []
            for row in result["lemmas"]:
                gaps = [{"kind": "premise", "proposition": premise}
                        for premise in conditional_route_premises(row["proposition"], goal)
                        if verification.suggest_proof(premise) is None]
                if gaps:
                    item = formalpedia_review.record(store, statement_id, row["candidate"], gaps)
                    if item is not None:
                        blockers.append({"id": item, "candidate": row["candidate"], "obligations": gaps})
            return {**result, "status": "needs_basis_review" if blockers else "open_goal",
                    "accepted": False, "blockers": blockers}

    def record_goal(receipt_store, report):
        formalpedia_review.accept_goal(receipt_store, statement_id, report["evidence_id"], identity, provenance)

    report = verification.check_goal(proof, output, on_receipt=record_goal if direct_goal else None)
    if report.get("accepted") and direct_goal:
        result["goal_source"] = {"candidate": identity, "provenance": provenance}
    return {**result, "status": "goal_verified" if report.get("accepted") else "goal_rejected",
            "accepted": report.get("accepted"), "goal": report}
