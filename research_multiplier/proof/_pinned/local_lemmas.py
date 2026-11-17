"""Admit and reuse exact intermediate proofs inside one reviewed research scope."""

from . import composed_lean, general_lean, lean_source, lean_workflow, local_lemma_kernel, paths, runtime
from .artifacts import Artifacts
from .lean_blueprint import LABEL, _proof
from .lean_runner import artifact_ref
from .postgres import Store
from .serialization import fingerprint

CHECKER = {"name": "rm-lean-local-lemma", "version": "1"}
MAX_PLAN_LEMMAS = 16


def _plan(document):
    if (not isinstance(document, dict)
            or not {"format_version", "nodes"} <= document.keys() <= {"format_version", "nodes", "goal_proof"}
            or ("goal_proof" in document and not isinstance(document["goal_proof"], dict))
            or type(document["format_version"]) is not int or document["format_version"] != 1
            or not isinstance(document["nodes"], list) or not 1 <= len(document["nodes"]) <= MAX_PLAN_LEMMAS):
        raise ValueError("A scoped lemma plan needs 1 to 16 nodes in a version 1 document.")
    names = set()

    def require(reference):
        if reference not in names:
            raise ValueError(f"The lemma plan must put {reference} before any proof that uses it.")

    for node in document["nodes"]:
        if (not isinstance(node, dict) or set(node) != {"name", "proposition", "proof"}
                or not isinstance(node["name"], str) or not LABEL.fullmatch(node["name"])
                or node["name"] in names or not isinstance(node["proposition"], str)
                or not node["proposition"].strip() or len(node["proposition"]) > 16000
                or not isinstance(node["proof"], dict)):
            raise ValueError("Each scoped lemma needs a distinct name, exact proposition and structured proof.")

        _proof(node["proof"], require)
        names.add(node["name"])
    goal_proof = document.get("goal_proof")
    if goal_proof is not None:
        _proof(goal_proof, require)
    return document["nodes"], goal_proof


def _rows(store, statement_id):
    return store.query("verifier", """
        SELECT * FROM rm.local_lean_lemma_status(%s) ORDER BY recorded_at, id
    """, statement_id)


def list_lemmas(statement_id):
    with Store() as store:
        lean_workflow._statement(store, "verifier", statement_id)
        return _rows(store, statement_id)


def _references(proof):
    if isinstance(proof, dict):
        if set(proof) == {"ref"} and isinstance(proof["ref"], str):
            yield proof["ref"]
        for value in proof.values():
            yield from _references(value)
    elif isinstance(proof, list):
        for value in proof:
            yield from _references(value)


def _earlier(submission, known, artifacts):
    selected = {}

    def collect(identifier):
        if identifier in selected or identifier not in known:
            return
        row = known[identifier]
        if row["status"] != "verified":
            raise ValueError("The referenced local theorem needs recheck.")
        body = row["body"]
        for prerequisite in body["local_lemma_ids"]:
            collect(prerequisite)
        selected[identifier] = {
            "id": identifier, "proposition": row["proposition"],
            "statement_sha256": body["anchor_sha256"], "basis_version_ids": body["basis_version_ids"],
            "local_lemma_ids": body["local_lemma_ids"], "submission": artifacts.read_json(body["submission"]),
        }
        if "reference_basis_version_ids" in body:
            selected[identifier]["reference_basis_version_ids"] = body["reference_basis_version_ids"]

    for identifier in _references(submission["proof"]):
        collect(identifier)
    return tuple(selected.values())


def _derive(store, artifacts, statement, subject, contract, known, node, names, *, checked_runtime, retry,
            on_receipt=None, replay_origin=None):
    def resolve(name):
        return {"ref": names[name]}

    submission = {"format_version": 1, "proof": _proof(node["proof"], resolve)}
    earlier = _earlier(submission, known, artifacts)
    proposition = node["proposition"]
    native = composed_lean.imports_for(store, submission, earlier)
    composition = None
    if native or composed_lean.requires_rebound(checked_runtime):
        selected = composed_lean.select_runtime(store, statement, checked_runtime=checked_runtime)
        expected = statement["body"]["environment"]
        _, _, _, original, hypotheses = lean_source._trusted_scope(store, statement)
        proposition = general_lean._proposition({"lean": {"proposition": proposition}})
        if proposition.strip() == original.strip():
            raise ValueError("Use the complete-goal verifier to prove the original result.")
        modules = expected.get("external", {}).get("modules", [])
        source = general_lean._type_source(proposition, hypotheses, modules)
        composition = composed_lean.prepare(store, statement, hypotheses, submission, earlier, native)
        binding = {
            "statement_sha256": statement["statement_sha256"],
            "trusted_source_sha256": artifact_ref(source.encode("utf-8"))["sha256"],
            "checker_revision": expected["inputs"]["driver"], "environment_sha256": expected["sha256"],
            "kernel_revision": composition.metadata["verifier_revision"],
            "basis_version_ids": composition.basis_ids, "local_lemma_ids": composition.local_ids,
        }
    else:
        binding = local_lemma_kernel.prepare(store, statement["id"], proposition, submission, earlier)
    payload = {
        "target": "local_lemma", "lean_statement_id": str(statement["id"]),
        "anchor_sha256": binding["statement_sha256"], "proposition": proposition,
        "statement_sha256": binding["trusted_source_sha256"],
        "trusted_source_sha256": statement["body"]["trusted_source"]["sha256"],
        "verification_environment_sha256": binding["environment_sha256"],
        "reviewed_driver_sha256": binding["checker_revision"], "checker_revision": binding["kernel_revision"],
        "basis_version_ids": binding["basis_version_ids"], "local_lemma_ids": binding["local_lemma_ids"],
        "submission": artifacts.put(submission), "checker": CHECKER,
    }
    if replay_origin is not None:
        payload["working_lemma_replay"] = replay_origin
    if composition is not None:
        payload["lean_composition"] = composition.metadata
        if selected.observation is not None:
            payload["lake_validation"] = selected.observation

    verification_ran = False

    def check():
        nonlocal verification_ran
        verification_ran = True
        if composition is not None:
            try:
                report, proof = composed_lean.audit(selected, composition, proposition, hypotheses, source)
            except composed_lean.NativeCompositionBlocked as error:
                return "no_result", composed_lean.blocked_result(error), None
            if report["local_lemma_ids"] != binding["local_lemma_ids"]:
                raise ValueError("Lean omitted a required local theorem reference.")
            report.pop("native_proof", None)
            return "exact_proof", {
                "report": report,
                "candidate": artifacts.put({"statement": source, "proof": proof, "submission": submission}),
            }, None
        result = local_lemma_kernel.check(store, statement["id"], proposition, submission, earlier,
                                          checked_runtime=composed_lean.selected_for(checked_runtime))
        if any(result[key] != value for key, value in binding.items()):
            raise ValueError("The local theorem's prepared inputs changed during verification.")
        return "exact_proof", {
            "report": result["report"], "candidate": artifacts.put(result["candidate"]),
        }, None

    attempt = runtime.execute(store, artifacts, subject, contract, "verify", check, payload=payload,
                              operation_key="lean-local:" + fingerprint(payload), model_calls=0, retry=retry)
    if attempt["return"]["finding"] != "exact_proof":
        composed_lean.raise_failed(attempt)
    if not verification_ran:
        checked_runtime.assert_current()
    result = attempt["result"]
    report = {
        **result["report"], "statement_sha256": payload["statement_sha256"],
        "candidate": result["candidate"], "report": attempt["artifact"],
    }
    if replay_origin is not None:
        report["working_lemma_replay"] = replay_origin

    def issue():
        row = store.query("verifier", "SELECT * FROM rm.record_local_lean_lemma(%s,%s)",
                          attempt["id"], store.json(report))[0]
        if on_receipt is not None:
            on_receipt(row)
        return row

    saved = store.transaction("verifier", issue) if on_receipt is not None else issue()
    return {**saved, "status": "verified"}


def derive(statement_id, document, *, lean=None, mathlib_project=None, retry=False, output=None):
    nodes, goal_proof = _plan(document)
    with Store() as store:
        statement = lean_workflow._statement(store, "verifier", statement_id)
        if not statement["check_ready"] or statement["body"].get("theorem") != "RMTheory.expected":
            raise ValueError("Confirm the current Lean Core statement before deriving local theorems.")
        proof_plan = {"proof": [*(node["proof"] for node in nodes), goal_proof]}
        known = {str(row["id"]): row for row in _rows(store, statement_id)}
        archive = Artifacts(paths.object_store(), workspace=False)
        earlier = _earlier(proof_plan, known, archive)
        if composed_lean.imports_for(store, proof_plan, earlier):
            checked_runtime = composed_lean.select_runtime(store, statement, lean=lean, mathlib_project=mathlib_project)
        else:
            checked_runtime = local_lemma_kernel.verify_environment(statement, lean=lean, mathlib_project=mathlib_project)
        artifacts = Artifacts(paths.object_store(), workspace=False)
        subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": statement["contract_sha256"]}
        contract = artifacts.put(store.frozen(statement["claim_id"])["contract_bytes"], raw=True)
        names = {}
        proof_records = []
        for node in nodes:
            try:
                row = _derive(store, artifacts, statement, subject, contract, known, node, names,
                              checked_runtime=checked_runtime, retry=retry)
            except ValueError as error:
                raise ValueError(f"Local theorem {node['name']}: {error}") from None
            identifier = str(row["id"])
            names[node["name"]] = identifier
            known[identifier] = row
            proof_records.append({"name": node["name"], **row})
    result = {"statement_id": statement_id, "lemmas": proof_records}
    if goal_proof is not None:
        from .final_lean_local import check

        submission = {"format_version": 1, "proof": _proof(goal_proof, lambda name: {"ref": names[name]})}
        result["goal"] = check(statement_id, submission, output or paths.runs() / "lean", lean,
                               mathlib_project=mathlib_project, retry=retry, checked_runtime=checked_runtime)
    return result
