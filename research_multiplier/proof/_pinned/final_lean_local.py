"""Check scoped proof replay and refutations of complete frozen Lean goals."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

from . import basis, composed_lean, general_lean as lean_checker, lean_core_reuse, lean_mathlib, lean_workflow
from . import lean_parameters, local_lemma_kernel, local_lemmas, native_reuse, paths, runtime
from .artifacts import Artifacts, write_json
from .lean_runner import artifact_ref
from .postgres import Store
from .serialization import fingerprint


class ReceiptCommitError(ValueError):
    """The proof passed verification but its receipt could not be published."""


class FinalProofRejected(ValueError):
    """The exact candidate could not be verified in its checked runtime."""

    def __init__(self, reason, attempt_id):
        super().__init__(reason)
        self.attempt_id = attempt_id


def _revision():
    return fingerprint({
        "final_driver": sha256(Path(__file__).read_bytes()).hexdigest(),
        "scoped_kernel": sha256(Path(local_lemma_kernel.__file__).read_bytes()).hexdigest(),
        "scoped_archive": sha256(Path(local_lemmas.__file__).read_bytes()).hexdigest(),
    })


def check(statement_id, submission, output, lean=None, *, retry=False, model=False, theorems=(),
          mathlib_project=None, refute=False, checked_runtime=None, on_receipt=None):
    """Verify a structured refutation or replay explicitly cited local lemmas."""
    if on_receipt is not None and (model or checked_runtime is None):
        raise ValueError("Atomic receipt metadata requires a checked runtime and an explicit proof.")
    if not model:
        proof = submission.get("proof") if isinstance(submission, dict) else None
        try:
            references = set(local_lemmas._references(proof))
            scoped = proof == {"decide": True} or local_lemma_kernel.uses_scoped_operator(proof)
        except RecursionError as error:
            raise ValueError("The Lean proof term is too deeply nested.") from error
        if refute or references or checked_runtime is not None or scoped:
            with Store() as store:
                known = ({str(row["id"]): row for row in local_lemmas._rows(store, statement_id)}
                         if references else {})
                earlier = ()
                if references & known.keys():
                    archive = Artifacts(paths.object_store(), workspace=False)
                    earlier = local_lemmas._earlier(submission, known, archive)
                native = composed_lean.imports_for(store, submission, earlier) if references else {}
                if refute or earlier or native or checked_runtime is not None or scoped:
                    return _check_frozen(store, statement_id, submission, output, earlier, lean=lean, retry=retry,
                                         theorems=theorems, mathlib_project=mathlib_project, refute=refute,
                                         checked_runtime=checked_runtime, on_receipt=on_receipt, native=native)
    return lean_checker.check(statement_id, submission, output, lean, retry=retry, model=model, theorems=theorems,
                              mathlib_project=mathlib_project, refute=refute)


def _check_frozen(store, statement_id, submission, output, earlier, *, lean, retry, theorems, mathlib_project, refute,
                  checked_runtime, on_receipt, native):
    statement = lean_workflow._statement(store, "verifier", statement_id)
    expected = statement["body"]
    if not statement["check_ready"] or expected.get("checker") != lean_checker.CHECKER:
        raise ValueError("Confirm the current Lean Core statement and checking environment.")
    manifest = expected["environment"].get("external")
    modules = lean_mathlib.validate_manifest(manifest)["modules"] if manifest is not None else ()
    checked = composed_lean.selected_for(checked_runtime)
    frozen = store.frozen(statement["claim_id"])
    frozen_contract = json.loads(frozen["contract_bytes"])
    goal, hypotheses = lean_checker._target(frozen_contract)
    source = lean_checker._type_source(goal, hypotheses, modules)
    if expected["theorem_type"] != goal or artifact_ref(source.encode("utf-8")) != expected["trusted_source"]:
        raise ValueError("The Lean proposition differs from its reviewed source.")
    checked_goal, checked_hypotheses, checked_source = goal, hypotheses, source
    if refute:
        checked_goal = f"_root_.Not (\n{lean_checker._implication(goal, hypotheses)}\n)"
        checked_hypotheses = ()
        checked_source = lean_checker._type_source(checked_goal, checked_hypotheses, modules)
    target = "refutation" if refute else "goal"

    library = lean_core_reuse.VerifiedCoreTheorems(store, statement["packet_id"], statement["claim_id"], manifest)
    for theorem in dict.fromkeys(theorems):
        library.admit(theorem)
    composition = None
    if native or composed_lean.requires_rebound(checked_runtime):
        selected = composed_lean.select_runtime(store, statement, lean=lean, mathlib_project=mathlib_project,
                                                checked_runtime=checked_runtime)
        composition = composed_lean.prepare(store, statement, hypotheses, submission, earlier, native, admit=True)
        compiler, term = composition.compiler, composition.term
    else:
        selected = (checked.require_environment(expected["environment"]) if checked is not None
                    else local_lemma_kernel._checked_runtime(expected["environment"], modules, lean, mathlib_project))
        items = basis.versions(store, "verifier", statement["packet_id"])
        compiler, term = local_lemma_kernel.compile_in_scope(
            items, hypotheses, earlier, statement["statement_sha256"], submission, library, admit=True)
    exact_decision = term == "(by decide)"
    if exact_decision:
        # Typeclass search does not unfold the reviewed definition at its normal
        # transparency. Supply the identical proposition as the decision target.
        proposition = lean_checker._implication(checked_goal, checked_hypotheses)
        term = f"(show (\n{proposition}\n) from (by decide))"
        if composition is not None:
            composition = replace(composition, term=term)
    local_ids = compiler.earlier.keys()
    requested = set(compiler.references)
    requested_local = requested & local_ids
    directory = Path(output) / str(statement["id"]) / "core"
    artifacts = Artifacts(directory / "artifacts")
    subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": statement["contract_sha256"]}
    contract = artifacts.put(frozen["contract_bytes"], raw=True)
    environment = expected["environment"]
    payload = {
        "lean_statement_id": str(statement["id"]), "statement_sha256": statement["statement_sha256"],
        "target": target, "submission": artifacts.put(submission), "checker": lean_checker.CHECKER,
        "checker_revision": environment["inputs"]["driver"], "verification_environment_sha256": environment["sha256"],
        "basis_version_ids": sorted(requested - local_ids),
    }
    if requested_local:
        payload.update(local_checker_revision=_revision(), local_lemma_ids=sorted(requested_local))
    if composition is not None:
        payload.update(lean_composition=composition.metadata, basis_version_ids=composition.basis_ids)
        if selected.observation is not None:
            payload["lake_validation"] = selected.observation
    if refute:
        payload["refutation_source_sha256"] = sha256(checked_source.encode("utf-8")).hexdigest()

    verification_ran = False

    def execute():
        nonlocal verification_ran
        verification_ran = True
        if composition is None:
            report, generated = lean_checker._check(
                selected.executable, selected.audit, checked_goal, checked_hypotheses, checked_source, term,
                compiler.expansion.declarations, compiler.references.values(),
                mathlib=selected.mathlib, native=selected.native)
        else:
            try:
                report, generated = composed_lean.audit(
                    selected, composition, checked_goal, checked_hypotheses, checked_source)
            except composed_lean.NativeCompositionBlocked as error:
                return "no_result", composed_lean.blocked_result(error), None
            if not refute:
                native_reuse.capture(artifacts, report)
            else:
                report.pop("native_proof", None)
            if not requested_local:
                report.pop("local_lemma_ids")
                report.pop("expanded_local_lemma_ids")
        if not refute and "elaborated_theorem_type" in expected and (
                report["expected_type"] != expected["elaborated_theorem_type"]):
            raise ValueError("The elaborated Lean proposition differs from the reviewed type.")
        dependencies = report["dependencies"]
        if requested_local and composition is None:
            direct_constants = set(dependencies["direct_constants"])
            actual_direct = set(report["basis_version_ids"])
            actual_expanded = set(report["expanded_basis_version_ids"])
            report.update(
                basis_version_ids=sorted(actual_direct - local_ids),
                expanded_basis_version_ids=sorted(actual_expanded - local_ids),
                local_lemma_ids=sorted(identifier for identifier in actual_direct & local_ids
                                       if f"RMTheory.Proof.{compiler.references[identifier]}" in direct_constants),
                expanded_local_lemma_ids=sorted(actual_expanded & local_ids),
            )
        observed = dependencies.pop("leading_binder_occurrence")
        if refute:
            dependencies.update(premise_usage=[], implication_binder_occurs_in_proof=observed[0] if observed else None)
        else:
            dependencies["premise_usage"] = lean_parameters.premise_usage(frozen_contract, hypotheses, observed)
        candidate = artifacts.put({"statement": checked_source, "proof": generated, "submission": submission})
        return "exact_proof", {"report": report, "candidate": candidate}, None

    # Keep the exact anchor and evidence schema unchanged. Retry the corrected
    # generated term even if the old alias-based decision attempt is archived.
    operation = {**payload, "typed_decision": 1} if exact_decision else payload
    attempt = runtime.execute(store, artifacts, subject, contract, "verify", execute,
                              payload=payload, seconds=120, operation_key="lean-core:" + fingerprint(operation),
                              model_calls=0, retry=retry)
    if attempt["return"]["finding"] != "exact_proof":
        result = attempt["result"]
        if result.get("proof_block"):
            composed_lean.raise_failed(attempt)
        raise FinalProofRejected(result["failure_reason"], attempt["id"])
    if checked_runtime is not None and not verification_ran:
        checked_runtime.assert_current()
    result = attempt["result"]
    report = result["report"]
    lean_core_reuse.archive_candidate(artifacts, result["candidate"])
    body = {
        **report, "theorem": lean_checker.THEOREM, "candidate_theorem": lean_checker.CANDIDATE,
        "target": target, "statement_type": goal, "trusted_source_sha256": expected["trusted_source"]["sha256"],
        "environment_sha256": environment["sha256"], "verification_environment_sha256": environment["sha256"],
        "checker_revision": payload["checker_revision"],
        "checker": lean_checker.CHECKER, "candidate": result["candidate"], "submission": payload["submission"],
        "report": attempt["artifact"], "verification_attempt_id": attempt["id"],
    }
    if requested_local:
        body["local_checker_revision"] = payload["local_checker_revision"]
    if refute:
        body.update(refutation_source_sha256=payload["refutation_source_sha256"],
                    refutation_mode="logical_negation", witness=None)
    record = ("SELECT id FROM rm.record_verified_lean_refutation(%s,%s,%s)" if refute
              else "SELECT id FROM rm.record_verified_lean(%s,%s,%s)")
    def issue():
        receipt = store.query("verifier", record, statement["id"], statement["statement_sha256"], store.json(body))[0]
        if composition is not None and "native_capsule" in report:
            native_reuse.register(store, receipt["id"], body, report["native_capsule"],
                                  composition.metadata["verifier_revision"])
        authors = (store.query("verifier", """
            SELECT attempt_id FROM rm.lean_core_author_provenance WHERE evidence_id = %s ORDER BY attempt_id
        """, receipt["id"]) if not requested_local and not refute else [])
        document = {**report, "evidence_id": str(receipt["id"]), "attempt_id": attempt["id"],
                    "author_attempt_ids": [str(row["attempt_id"]) for row in authors]}
        if requested_local:
            document["local_checker_revision"] = payload["local_checker_revision"]
        if refute:
            document.update(evidence_kind="counterexample", refutation_mode="logical_negation", witness=None)
        if on_receipt is not None:
            on_receipt(store, document)
        return document

    if on_receipt is None and composition is None:
        output_document = issue()
    else:
        try:
            output_document = store.transaction("verifier", issue)
        except ValueError as error:
            raise ReceiptCommitError(f"Could not commit the Lean receipt and its provenance: {error}") from None
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_json(directory / ("refutation.json" if refute else "report.json"), output_document)
    return output_document
