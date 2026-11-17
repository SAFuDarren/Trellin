"""Replay admitted native theorems inside structured Lean proofs."""

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
from uuid import UUID

from . import basis, general_lean as checker, lake_scope, lean_core_reuse, lean_source, local_lemma_kernel, paths
from .artifacts import Artifacts
from .native_reuse import MAX_CAPSULE_BYTES
from .serialization import fingerprint


_SUPPORT = ("RMProofData.lean", "NativeReplay.lean", "RMComposition.lean")


class NativeCompositionBlocked(ValueError):
    """A proof needed a compatible definition or a stronger deduction procedure."""

    def __init__(self, kind, details=(), *, attempt_id=None):
        super().__init__("Lean could not complete this proof with the confirmed inputs.")
        self.kind = kind
        self.details = list(details)
        self.report = {"status": kind, "details": self.details}
        if attempt_id is not None:
            self.report["attempt_id"] = attempt_id


@dataclass(frozen=True)
class SelectedRuntime:
    runtime: object
    observation: dict | None
    anchor_environment: dict

    @property
    def rebound(self):
        return self.runtime.environment != self.anchor_environment

    def assert_current(self):
        self.runtime.assert_current()


def select_runtime(store, statement, *, lean=None, mathlib_project=None, checked_runtime=None):
    expected = statement["body"]["environment"]
    if isinstance(checked_runtime, SelectedRuntime):
        if checked_runtime.anchor_environment != expected:
            raise ValueError("The composed proof belongs to a different confirmed Lean environment.")
        return checked_runtime
    if expected.get("external") is None:
        checked = checked_runtime or local_lemma_kernel.verify_environment(
            statement, lean=lean, mathlib_project=mathlib_project)
        checked.require_environment(expected)
        return SelectedRuntime(checked, None, expected)
    checked = checked_runtime or lake_scope.open_runtime(statement, lean=lean, mathlib_project=mathlib_project)
    lean_source._check_internal_modules(checked)
    reserved = {Path(name).with_suffix(".olean") for name in _SUPPORT}
    if reserved.intersection(checked.mathlib.backend.artifacts):
        raise ValueError("The reviewed Lean project uses a reserved proof composition module.")
    _, _, trusted, _, _ = lean_source._trusted_scope(store, statement)
    approved = lean_source._accepted_declarations(store, statement["packet_id"])
    observation, changes = lake_scope.validate(store, statement, checked, trusted, approved)
    if changes:
        raise NativeCompositionBlocked("lake_scope_review_required", changes)
    return SelectedRuntime(checked, observation, expected)


def _support():
    return {name: files("research_multiplier").joinpath("lean_support", name).read_bytes() for name in _SUPPORT}


def _revision(support):
    return fingerprint({
        "runner": sha256(Path(__file__).read_bytes()).hexdigest(),
        "local_compiler": sha256(Path(local_lemma_kernel.__file__).read_bytes()).hexdigest(),
        "basis": sha256(Path(lean_source.__file__).read_bytes()).hexdigest(),
        "lake_scope": sha256(Path(lake_scope.__file__).read_bytes()).hexdigest(),
        **{name: sha256(value).hexdigest() for name, value in support.items()},
    })


def _references(proof):
    from .local_lemmas import _references as visit

    return set(visit(proof))


def imports_for(store, submission, earlier):
    """Read active native exports used by the proof or any chosen local ancestor."""
    references = _references(submission.get("proof"))
    for row in earlier:
        references.update(_references(row["submission"].get("proof")))
    identifiers = []
    for reference in references:
        try:
            if str(UUID(reference)) == reference:
                identifiers.append(reference)
        except ValueError:
            continue
    if not identifiers:
        return {}
    return {str(row["id"]): row["body"] for row in store.query("verifier", """
        SELECT id, body FROM rm.current_lean_core_theorems
        WHERE id = ANY(%s::uuid[]) AND body ->> 'format' = 'lean-native-proof'
    """, sorted(identifiers))}


def selected_for(runtime):
    return runtime.runtime if isinstance(runtime, SelectedRuntime) else runtime


def requires_rebound(runtime):
    return isinstance(runtime, SelectedRuntime) and runtime.rebound


@dataclass
class Composition:
    compiler: object
    term: str
    metadata: dict
    approved: dict
    support: dict
    source_files: dict

    @property
    def basis_ids(self):
        refs = self.compiler.references.keys() - self.compiler.earlier.keys()
        return sorted(refs | {item["version_id"] for item in self.metadata["imports"]}
                      | {version for version, _ in self.approved.values()})

    @property
    def local_ids(self):
        return sorted(self.compiler.references.keys() & self.compiler.earlier.keys())


def prepare(store, statement, hypotheses, submission, earlier, native, *, admit=False):
    environment = statement["body"]["environment"]
    manifest = environment.get("external")
    library = lean_core_reuse.VerifiedCoreTheorems(store, statement["packet_id"], statement["claim_id"], manifest)
    approved = lean_source._accepted_declarations(store, statement["packet_id"])
    bound = {version: library.admit(version) for version in sorted(native)}
    items = basis.versions(store, "verifier", statement["packet_id"])
    compiler, term = local_lemma_kernel.compile_in_scope(
        items, hypotheses, earlier, statement["statement_sha256"], submission, library, admit=admit, native=bound)
    imports = []
    sources = {}
    archive = Artifacts(paths.object_store(), workspace=False)
    for version in sorted(bound):
        if _alias(version) not in compiler.expansion.declarations:
            raise ValueError("A native theorem input disappeared from the generated proof.")
        artifact = bound[version]["source"]["replay"]
        try:
            data = archive.read(artifact)
        except FileNotFoundError:
            raise ValueError("The original native replay artifact is unavailable in this workspace.") from None
        if len(data) > MAX_CAPSULE_BYTES:
            raise ValueError("The original native replay artifact exceeds its size limit.")
        imports.append({"version_id": version, "artifact": artifact})
        sources[f"native-{version}.json"] = data
    if sum(map(len, sources.values())) > 24 * 1024 * 1024:
        raise ValueError("The combined native archives exceed the proof replay budget.")
    support = _support()
    metadata = {"imports": imports, "verifier_revision": _revision(support),
                "approved_declarations": lake_scope._mapping(approved)}
    return Composition(compiler, term, metadata, approved, support, sources)


def _alias(version):
    return "basis_native_" + version.replace("-", "")


def lean_reference(version, item, hypotheses):
    """A declaration that can replay the native theorem from all destination hypotheses."""
    consequent = checker._proposition(item.get("formal"))
    source_count = len(lean_core_reuse.antecedents(item))
    value = f'(by rm_native "native-{version}.json" {source_count} {hypotheses.argument_count})'
    return checker._Declaration(version, consequent, value, tuple(hypotheses), hypotheses.parameters)


def audit(selected, composition, goal, hypotheses, source):
    """Elaborate the restricted proof DSL and independently check the closed kernel term."""
    checked = selected.runtime
    modules = checked.mathlib.manifest["modules"] if checked.mathlib is not None else ()
    declarations = composition.compiler.expansion.declarations
    output = ["import RMExpected", "import RMApproved", "import RMComposition",
              "set_option autoImplicit false", "set_option maxHeartbeats 500000",
              "namespace RMTheory.Proof"]
    output.extend(declaration.render(name) for name, declaration in declarations.items())
    output.extend([f"theorem checked : {checker.THEOREM} := {composition.term}", "end RMTheory.Proof", ""])
    generated = "\n".join(output)
    if len(source.encode()) + len(generated.encode()) > checker.MAX_REPLAY_SOURCE_BYTES:
        raise ValueError("The checked Lean source exceeds the replay budget.")
    terms = checker._scoped_terms(goal, hypotheses, declarations)
    sources = {**composition.source_files, **composition.support, **lean_source._approval_sources(composition.approved),
               "Audit.lean": checked.audit, "RMExpected.lean": source.encode(), "RMCandidate.lean": generated.encode()}
    sources.update({f"term{index}.txt": term.encode() for index, term in enumerate(terms)})
    validate = ["validate-imports", ",".join(modules)] if modules else ["validate"]
    commands = [["--run", "Audit.lean", *validate, *[name for name in sources if name.endswith(".txt")]]]
    commands.extend([["-o", Path(name).with_suffix(".olean").name, name]
                     for name in (*_SUPPORT, "RMExpected.lean", "RMApproved.lean", "RMCandidate.lean")])
    commands.append(["--run", "NativeReplay.lean", "composition-audit", ",".join(modules)])
    code, raw = (checked.mathlib or checked.native).run(sources, commands)
    token = "RM_COMPOSITION_BLOCKED "
    for line in raw.splitlines():
        if token in line:
            try:
                problem = json.loads(line.split(token, 1)[1])
            except (ValueError, IndexError):
                continue
            raise NativeCompositionBlocked(problem["kind"], problem["details"])
    report = checker._lean_report(raw, "Lean could not verify the composed proof.")
    dep = report["dependencies"]
    if (code or report.get("accepted") is not True or report.get("kernel_rechecked") is not True
            or report.get("scope_matches") is not True or set(dep["axioms"]) - checker.AXIOMS
            or any(dep[key] for key in ("unauthorized_axioms", "unsafe", "unapproved"))):
        raise ValueError("The independent Lean kernel or dependency policy rejected the composed proof.")

    names = {f"RMTheory.Proof.{name}": declaration.version_id for name, declaration in declarations.items()}
    observed = set(dep["basis_aliases"])
    if observed - names.keys():
        raise ValueError("The composed proof uses an unregistered alias.")
    native = {item["version_id"] for item in composition.metadata["imports"]
              if f"RMTheory.Proof.{_alias(item['version_id'])}" in observed}
    actual_named = set(report["basis_version_ids"])
    if not actual_named <= {version for version, _ in composition.approved.values()}:
        raise ValueError("The composed proof uses an unregistered approved theorem.")
    direct = {identifier for identifier, alias in composition.compiler.references.items()
              if f"RMTheory.Proof.{alias}" in observed}
    locals = set(composition.compiler.earlier)
    expanded_local = {names[name] for name in observed} & locals
    dep["native_version_ids"] = sorted(native)
    dep["direct_basis_version_ids"] = sorted(set(report.pop("direct_basis_version_ids")) | (direct - locals))
    dep["references"] = sorted(set(dep["references"]) | observed)
    for key, value in {
        "basis_version_ids": sorted((direct - locals) | actual_named | native),
        "expanded_basis_version_ids": sorted((direct - locals) | actual_named | native),
        "local_lemma_ids": sorted(direct & locals),
        "expanded_local_lemma_ids": sorted(expanded_local),
        "reference_basis_version_ids": sorted(direct - locals),
        "lean_composition": composition.metadata,
    }.items():
        report[key] = value
    if selected.observation is not None:
        report["lake_validation"] = selected.observation
    return report, generated


def blocked_result(error):
    return {"failure_reason": str(error), "failure_code": error.kind, "proof_block": error.report}


def raise_failed(attempt):
    result = attempt["result"]
    blocked = result.get("proof_block")
    if blocked:
        raise NativeCompositionBlocked(blocked["status"], blocked["details"], attempt_id=attempt["id"])
    raise ValueError(result["failure_reason"])
