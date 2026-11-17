"""Audit a locally supplied Lean theorem against a reviewed research statement."""

from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path

from . import basis, general_lean, lake_scope, lean_core_reuse, lean_workflow, local_lemma_kernel, paths, runtime
from . import lean_parameters
from .artifacts import Artifacts, write_json
from .lean_runner import artifact_ref
from .postgres import Store
from .serialization import fingerprint


MAX_SOURCE_BYTES = 256 * 1024
MAX_COMPILED_BYTES = 64 * 1024 * 1024
MAX_APPROVED_DECLARATIONS = 128
SUBMISSION_MODULE = "RMSubmitted"
INTERNAL_MODULES = frozenset({"RMExpected.olean", "RMApproved.olean", "RMProofData.olean", f"{SUBMISSION_MODULE}.olean"})


def _accepted_declarations(store, packet_id):
    by_name = {}
    for row in basis.versions(store, "verifier", packet_id):
        item = row["body"]
        if item.get("kind") != "theorem":
            continue
        formal = item.get("formal")
        lean_formal = formal.get("lean") if isinstance(formal, dict) else None
        name = lean_formal.get("declaration") if isinstance(lean_formal, dict) else None
        if name is None:
            continue
        if not isinstance(name, str) or not general_lean.CORE_NAME.fullmatch(name):
            raise ValueError("A reviewed Lean theorem needs its exact qualified declaration name.")
        if name in by_name:
            raise ValueError("The reviewed packet binds the same Lean declaration to multiple theorem versions.")
        identifier = str(row["id"])
        by_name[name] = (identifier, general_lean._proposition(item["formal"]))
    if len(by_name) > MAX_APPROVED_DECLARATIONS:
        raise ValueError("This source check can use up to 128 approved Lean declarations in one packet.")
    return by_name


def _approval_sources(approved):
    sources = {}
    code = ["import RMExpected", "set_option autoImplicit false", "set_option maxHeartbeats 200000", ""]
    keys = []
    for name, (identifier, proposition) in sorted(approved.items()):
        key = "RMApproved.basis_" + identifier.replace("-", "")
        sources[key + ".txt"] = proposition.encode("utf-8")
        code.append(f"def {key} : Prop := (\n{proposition}\n)")
        keys.append(f"{identifier}\t{name}\t{key}\n")
    sources["RMApproved.lean"] = ("\n".join(code) + "\n").encode("utf-8")
    sources["approved.tsv"] = "".join(keys).encode("utf-8")
    return sources


def _source_file(file):
    if Path(file).suffix != ".lean":
        raise ValueError("Select an ordinary .lean theorem file.")
    with Path(file).open("rb") as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError("The Lean source must be nonempty and at most 256 KiB.")
    try:
        data.decode("utf-8")
    except UnicodeError:
        raise ValueError("Save the Lean source as UTF-8 text.") from None
    return data


def _check_internal_modules(checked):
    if checked.mathlib and any(Path(name) in checked.mathlib.backend.artifacts for name in INTERNAL_MODULES):
        raise ValueError("The reviewed Lean project uses a module name reserved for the proof audit.")


def _trusted_scope(store, statement):
    frozen = store.frozen(statement["claim_id"])
    contract = json.loads(frozen["contract_bytes"])
    goal, hypotheses = general_lean._target(contract)
    expected = statement["body"]
    names = expected["environment"].get("external", {}).get("modules", [])
    trusted = general_lean._type_source(goal, hypotheses, names)
    if expected["theorem_type"] != goal or artifact_ref(trusted.encode("utf-8")) != expected["trusted_source"]:
        raise ValueError("The Lean source check must match the original reviewed statement.")
    return frozen, contract, trusted, goal, hypotheses


def _audit(checked, expected_source, approved, names, declaration, module, data, support, *, scope_terms=()):
    sources = {
        "TheoryAudit.lean": checked.audit,
        "SourceAudit.lean": support["SourceAudit.lean"],
        "RMProofData.lean": support["RMProofData.lean"],
        "RMExpected.lean": expected_source.encode("utf-8"),
        **_approval_sources(approved),
    }
    sources.update({f"scope-{index}.txt": term.encode("utf-8") for index, term in enumerate(scope_terms)})
    facts = [name for name in sources if name.endswith(".txt")]
    commands = []
    if facts:
        validate = ["validate-imports", ",".join(names)] if names else ["validate"]
        commands.append(["--run", "TheoryAudit.lean", *validate, *facts])
    commands.extend([
        ["-o", "RMProofData.olean", "RMProofData.lean"],
        ["-o", "RMExpected.olean", "RMExpected.lean"],
        ["-o", "RMApproved.olean", "RMApproved.lean"],
    ])
    if data is not None:
        sources["SourceInput.lean"] = support["SourceInput.lean"]
        sources["RMSubmitted.lean"] = data
        commands.extend([
            ["--run", "SourceInput.lean", "RMSubmitted.lean", ",".join(names), declaration],
            ["-o", "RMSubmitted.olean", "RMSubmitted.lean"],
        ])
    commands.append(["--run", "SourceAudit.lean", "audit", module, declaration, ",".join(names),
                     "source" if data is not None else "compiled"])
    checker = checked.mathlib or checked.native
    code, raw = checker.run(sources, commands)
    report = general_lean._lean_report(raw, "Lean could not check the supplied declaration.")
    if (report.get("compiled") is not True or report.get("accepted") is not (code == 0)
            or report.get("source_declaration") != declaration or report.get("source_module") != module):
        raise ValueError("The native Lean audit returned inconsistent declaration evidence.")
    return report


def _support():
    return {name: files("research_multiplier").joinpath("lean_support", name).read_bytes()
            for name in ("SourceInput.lean", "SourceAudit.lean", "RMProofData.lean")}


def _revision(support, observation):
    revisions = {"driver": sha256(Path(__file__).read_bytes()).hexdigest(),
                 **{name: sha256(code).hexdigest() for name, code in support.items()}}
    if observation is not None:
        revisions.update(lake_driver=sha256(Path(lake_scope.__file__).read_bytes()).hexdigest(),
                         lake_snapshot=observation["scope_revision"])
    return fingerprint(revisions)


def check_source(statement_id, *, declaration, file=None, module=None, lean=None, mathlib_project=None,
                 output=None, retry=False):
    from . import native_reuse

    if (file is None) == (module is None):
        raise ValueError("Select either a .lean source file or a declaration's compiled --module.")
    if (not isinstance(declaration, str) or not 0 < len(declaration) <= 512
            or declaration.strip() != declaration or not declaration.isprintable()):
        raise ValueError("Supply the theorem's exact qualified Lean declaration name.")
    data = _source_file(file) if file is not None else None
    with Store() as store:
        statement = lean_workflow._statement(store, "verifier", statement_id)
        expected = statement["body"]
        if not statement["check_ready"] or expected.get("checker") != general_lean.CHECKER:
            raise ValueError("Confirm the current Lean statement and checking environment.")
        names = expected["environment"].get("external", {}).get("modules", [])
        if module is not None and module not in names:
            raise ValueError("Include the compiled theorem's module in the reviewed Lean imports before checking it.")
        frozen, contract, trusted, goal, hypotheses = _trusted_scope(store, statement)
        approved = _accepted_declarations(store, statement["packet_id"])
        observation = None
        if names:
            checked = lake_scope.open_runtime(statement, lean=lean, mathlib_project=mathlib_project)
            _check_internal_modules(checked)
            observation, changes = lake_scope.validate(store, statement, checked, trusted, approved)
            if changes:
                return {"status": "lake_scope_review_required", "accepted": False,
                        "statement_id": str(statement_id), "changes": changes,
                        "reviewed_environment_sha256": expected["environment"]["sha256"],
                        "observed_environment_sha256": checked.environment["sha256"]}
        else:
            checked = local_lemma_kernel.verify_environment(statement, lean=lean, mathlib_project=mathlib_project)

        support = _support()
        revision = _revision(support, observation)
        directory = paths.validate_project_write(Path(output or paths.runs() / "lean") / str(statement_id) / "source")
        artifacts = Artifacts(directory / "artifacts")
        if data is None:
            compiled = Path(*module.split(".")).with_suffix(".olean")
            candidate_path, candidate_sha = checked.mathlib.backend.artifacts[compiled]
            with candidate_path.open("rb") as stream:
                candidate_bytes = stream.read(MAX_COMPILED_BYTES + 1)
            if len(candidate_bytes) > MAX_COMPILED_BYTES:
                raise ValueError("Select a compiled theorem module of at most 64 MiB.")
            if sha256(candidate_bytes).hexdigest() != candidate_sha:
                raise ValueError("The compiled theorem module differs from its reviewed bytes.")
        else:
            candidate_bytes = data
            candidate_sha = sha256(data).hexdigest()
        candidate = artifacts.put_bytes(candidate_bytes)
        bound = {
            "mode": "file" if data is not None else "module",
            "module": module or SUBMISSION_MODULE, "declaration": declaration, "verifier_revision": revision,
            "candidate_sha256": candidate_sha,
            "approved_declarations": [{"declaration": name, "version_id": row[0]}
                                      for name, row in sorted(approved.items())],
        }
        environment = expected["environment"]
        subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": frozen["contract_sha256"]}
        contract_ref = artifacts.put(frozen["contract_bytes"], raw=True)
        payload = {
            "target": "goal", "lean_statement_id": str(statement_id), "statement_sha256": statement["statement_sha256"],
            "checker": general_lean.CHECKER, "checker_revision": environment["inputs"]["driver"],
            "verification_environment_sha256": environment["sha256"],
            "submission": candidate,
            "basis_version_ids": sorted(row[0] for row in approved.values()), "lean_source": bound,
        }
        if observation is not None:
            payload["lake_validation"] = observation
        verification_ran = False

        def execute():
            nonlocal verification_ran
            verification_ran = True
            scope_terms = general_lean._scoped_terms(goal, hypotheses) if hypotheses.parameters else ()
            report = _audit(checked, trusted, approved, names, declaration, bound["module"], data, support,
                            scope_terms=scope_terms)
            if observation is not None:
                report["lake_validation"] = observation
            dependencies = report["dependencies"]
            observed = dependencies.pop("leading_binder_occurrence")
            dependencies["premise_usage"] = lean_parameters.premise_usage(contract, hypotheses, observed)
            if not report["accepted"]:
                return "no_result", {"report": report, "failure_reason":
                                     "The native Lean audit rejected the declaration."}, None
            native_reuse.capture(artifacts, report)
            proof_archive = artifacts.put({"statement": trusted, "lean_source": bound, "source": candidate})
            return "exact_proof", {"report": report, "candidate": proof_archive}, None

        attempt = runtime.execute(
            store, artifacts, subject, contract_ref, "verify", execute, payload=payload, model_calls=0,
            operation_key="lean-source:" + fingerprint(payload), seconds=120, retry=retry,
        )
        if not verification_ran:
            checked.assert_current()
        result = attempt["result"]
        if attempt["return"]["finding"] != "exact_proof":
            if "report" not in result:
                raise ValueError(result["failure_reason"])
            return {**result["report"], "status": "rejected", "lean_source": bound, "attempt_id": attempt["id"]}
        report = result["report"]
        lean_core_reuse.archive_candidate(artifacts, result["candidate"])
        body = {
            **report, "theorem": general_lean.THEOREM, "candidate_theorem": general_lean.CANDIDATE,
            "target": "goal", "statement_type": goal, "trusted_source_sha256": expected["trusted_source"]["sha256"],
            "environment_sha256": environment["sha256"],
            "verification_environment_sha256": environment["sha256"], "checker": general_lean.CHECKER,
            "checker_revision": payload["checker_revision"], "candidate": result["candidate"],
            "submission": payload["submission"], "report": attempt["artifact"], "verification_attempt_id": attempt["id"],
            "lean_source": bound,
        }
        def issue():
            receipt = store.query("verifier", "SELECT id FROM rm.record_verified_lean(%s,%s,%s)",
                                  statement["id"], statement["statement_sha256"], store.json(body))[0]
            if report.get("native_capsule"):
                native_reuse.register(store, receipt["id"], body, report["native_capsule"], revision)
            return str(receipt["id"])

        evidence_id = store.transaction("verifier", issue)
        document = {**report, "status": "awaiting_evidence_review", "lean_source": bound,
                    "evidence_id": evidence_id, "attempt_id": attempt["id"]}
        report_path = directory / str(attempt["id"]) / "report.json"
        report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(report_path, document)
        return document
