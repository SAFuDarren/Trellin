"""Kernel-check a scoped lemma using one reviewed statement's frozen hypotheses."""

from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import UUID

from . import basis, general_lean as checker, lean_core_reuse, lean_mathlib, lean_native_runtime, lean_workflow
from .lean_runner import artifact_ref
from .lean_parameters import Hypotheses
from .serialization import fingerprint


_FINITE_OPERATORS = frozenset({
    "nat_literal", "bool_literal", "bool_predicate_literal", "bool_relation_literal", "decide_false",
})
_LOGIC_OPERATORS = {
    "or_elim": ("Or.elim", 3),
    "and_left": ("And.left", 1),
    "and_right": ("And.right", 1),
    "eq_symm": ("Eq.symm", 1),
    "eq_trans": ("Eq.trans", 2),
}
_SCOPED_OPERATORS = _FINITE_OPERATORS.union(_LOGIC_OPERATORS)


def uses_scoped_operator(proof):
    if isinstance(proof, dict):
        return (len(proof) == 1 and next(iter(proof)) in _SCOPED_OPERATORS
                or any(uses_scoped_operator(value) for value in proof.values()))
    return isinstance(proof, list) and any(uses_scoped_operator(value) for value in proof)


def _version_id(value):
    text = str(value)
    try:
        canonical = str(UUID(text))
    except ValueError:
        raise ValueError("A local lemma needs an exact version UUID.") from None
    if text != canonical:
        raise ValueError("A local lemma needs an exact version UUID.")
    return canonical


def _version_ids(values):
    if not isinstance(values, (tuple, list)) or len(values) > checker.MAX_NODES:
        raise ValueError("The checked lemma has an invalid dependency list.")
    identifiers = {_version_id(value) for value in values}
    if len(identifiers) != len(values):
        raise ValueError("The checked lemma repeats a dependency version.")
    return identifiers


def _proposition(text):
    return checker._proposition({"lean": {"proposition": text}})


def _earlier_rows(rows, statement_sha256, basis_ids):
    if not isinstance(rows, (tuple, list)) or len(rows) > checker.MAX_REPLAY_THEOREMS:
        raise ValueError("Too many prior local lemmas for a single proof.")
    earlier = {}
    for row in rows:
        required = {"id", "proposition", "submission", "statement_sha256", "basis_version_ids", "local_lemma_ids"}
        if not isinstance(row, dict) or required - row.keys() or row["statement_sha256"] != statement_sha256:
            raise ValueError("A prior lemma belongs to a different reviewed statement.")
        identifier = _version_id(row["id"])
        foundations, dependencies = _version_ids(row["basis_version_ids"]), _version_ids(row["local_lemma_ids"])
        if identifier in earlier or identifier in basis_ids:
            raise ValueError("A local lemma version conflicts with another proof input.")
        if not foundations <= basis_ids or not dependencies <= earlier.keys():
            raise ValueError("An earlier lemma cites an unavailable or later proof input.")
        earlier[identifier] = {**row, "proposition": _proposition(row["proposition"]),
                               "basis_version_ids": foundations, "local_lemma_ids": dependencies}
    return earlier


class _LocalTerm(checker._Term):
    def __init__(self, items, hypotheses, earlier, *, available=None, native=None, **options):
        super().__init__(items, **options)
        self.hypotheses = hypotheses if isinstance(hypotheses, Hypotheses) else Hypotheses(hypotheses)
        self.earlier = earlier
        self.available = (items, earlier) if available is None else available
        self.native = {} if native is None else native

    def term(self, node, depth):
        if isinstance(node, dict) and len(node) == 1:
            operator, value = next(iter(node.items()))
            if operator in _FINITE_OPERATORS:
                return self._finite_term(operator, value, depth)
            if operator in _LOGIC_OPERATORS:
                declaration, arity = _LOGIC_OPERATORS[operator]
                arguments = [value] if arity == 1 else value
                if not isinstance(arguments, list) or len(arguments) != arity:
                    raise ValueError(f"Unsupported or invalid proof operator: {operator}.")
                self._count_scoped_term(depth)
                terms = [self.term(argument, depth) for argument in arguments]
                return f"(_root_.{declaration} {' '.join(terms)})"
        term = super().term(node, depth)
        if isinstance(node, dict) and set(node) == {"ref"} and node["ref"] in self.native:
            arguments = [f"x_{index}" for index in range(min(depth, self.hypotheses.argument_count))]
            if arguments:
                return "(" + " ".join([term, *arguments]) + ")"
        return term

    def _count_scoped_term(self, depth):
        self.nodes += 1
        self.expansion.nodes += 1
        if self.nodes > checker.MAX_NODES or depth > checker.MAX_NODES or self.expansion.nodes > checker.MAX_REPLAY_NODES:
            raise ValueError("The Lean proof term is too large.")

    def _finite_term(self, operator, value, depth):
        self._count_scoped_term(depth)
        if operator == "nat_literal" and type(value) is int and 0 <= value <= 16:
            return f"({value} : _root_.Nat)"
        if operator == "bool_literal" and type(value) is bool:
            return "_root_.Bool.true" if value else "_root_.Bool.false"
        if operator == "bool_predicate_literal" and self._bool_table(value, 2):
            return f"(fun (a : _root_.Bool) => ({self._boolean_branch('a', value)}) = _root_.Bool.true)"
        if operator == "bool_relation_literal" and self._bool_table(value, 4):
            left = self._boolean_branch("b", value[:2])
            right = self._boolean_branch("b", value[2:])
            return f"(fun (a b : _root_.Bool) => (match a with | false => ({left}) | true => ({right})) = _root_.Bool.true)"
        if operator == "decide_false":
            return f"(_root_.of_decide_eq_false rfl {self.term(value, depth)})"
        raise ValueError(f"Unsupported or invalid proof operator: {operator}.")

    @staticmethod
    def _bool_table(value, arity):
        return isinstance(value, list) and len(value) == arity and all(type(item) is bool for item in value)

    @staticmethod
    def _boolean_branch(variable, bits):
        false_arm, true_arm = ("_root_.Bool.true" if bit else "_root_.Bool.false" for bit in bits)
        return f"match {variable} with | false => {false_arm} | true => {true_arm}"

    def reference(self, item_id):
        if item_id in self.native:
            from .composed_lean import _alias, lean_reference

            if item_id not in self.items:
                raise ValueError("The native theorem is outside this proof's admitted dependencies.")
            name = _alias(item_id)
            self.references[item_id] = name
            if name not in self.expansion.declarations:
                self.expansion.theorems.add(item_id)
                if len(self.expansion.theorems) > checker.MAX_REPLAY_THEOREMS:
                    raise ValueError("The proof cites too many archived theorems.")
                self.expansion.declarations[name] = lean_reference(item_id, self.native[item_id], self.hypotheses)
            return name
        row = self.earlier.get(item_id)
        if row is None:
            return super().reference(item_id)
        name = "basis_local_" + item_id.replace("-", "")
        self.references[item_id] = name
        expansion = self.expansion
        if name in expansion.declarations:
            return name
        if item_id in expansion.active or len(expansion.active) >= checker.MAX_REPLAY_DEPTH:
            raise ValueError("The local lemma replay exceeds the depth limit or contains a cycle.")
        expansion.theorems.add(item_id)
        if len(expansion.theorems) > checker.MAX_REPLAY_THEOREMS:
            raise ValueError("The proof cites too many archived theorems.")
        all_basis, all_earlier = self.available
        permitted_basis = [item for item in all_basis if item["id"] in row["basis_version_ids"]]
        permitted_lemmas = {key: all_earlier[key] for key in row["local_lemma_ids"]}
        expansion.active.add(item_id)
        try:
            term = _LocalTerm(permitted_basis, self.hypotheses, permitted_lemmas, library=self.library,
                              prefix=f"basis_nested_{item_id.replace('-', '')}_", expansion=expansion,
                              available=self.available, native=self.native)
            proof = term.generate(row["submission"])
            basis_refs = row["basis_version_ids"]
            if "reference_basis_version_ids" in row:
                basis_refs = _version_ids(row["reference_basis_version_ids"])
                if not basis_refs <= row["basis_version_ids"]:
                    raise ValueError("The prior lemma cites an unrecorded basis reference.")
            if set(term.references) != basis_refs | row["local_lemma_ids"]:
                raise ValueError("The prior lemma's structured proof differs from its recorded dependencies.")
        finally:
            expansion.active.remove(item_id)
        expansion.declarations[name] = checker._Declaration(item_id, row["proposition"], proof, tuple(self.hypotheses),
                                                            self.hypotheses.parameters)
        return name


def compile_in_scope(items, hypotheses, earlier, statement_sha256, submission, library, *, admit=False, native=None):
    """Generate a term that can replay only the supplied earlier rows in this scope."""
    rows = _earlier_rows(earlier, statement_sha256, {row["id"] for row in items})
    term = _LocalTerm(items, hypotheses, rows, library=library, admit=admit, native=native)
    return term, term.generate(submission)


def _prepare(store, statement_id, proposition, submission, earlier):
    statement = lean_workflow._statement(store, "verifier", statement_id)
    expected = statement["body"]
    if not statement["check_ready"] or expected.get("checker") != checker.CHECKER:
        raise ValueError("Confirm the current Lean statement before deriving a scoped lemma.")
    frozen = json.loads(store.frozen(statement["claim_id"])["contract_bytes"])
    original, hypotheses = checker._target(frozen)
    environment = expected["environment"]
    manifest = environment.get("external")
    modules = lean_mathlib.validate_manifest(manifest)["modules"] if manifest is not None else ()
    anchor_source = artifact_ref(checker._type_source(original, hypotheses, modules).encode("utf-8"))
    if original != expected["theorem_type"] or anchor_source != expected["trusted_source"]:
        raise ValueError("The lemma scope differs from the reviewed statement.")

    library = lean_core_reuse.VerifiedCoreTheorems(store, statement["packet_id"], statement["claim_id"], manifest)
    items = basis.versions(store, "verifier", statement["packet_id"])
    proposition = _proposition(proposition)
    if proposition.strip() == original.strip():
        raise ValueError("Use the complete-goal verifier to prove the original result.")
    term, proof = compile_in_scope(items, hypotheses, earlier, statement["statement_sha256"], submission, library)
    earlier = term.earlier
    source = checker._type_source(proposition, hypotheses, modules)
    dependencies = set(term.references)
    binding = {
        "statement_sha256": statement["statement_sha256"],
        "trusted_source_sha256": artifact_ref(source.encode("utf-8"))["sha256"],
        "checker_revision": environment["inputs"]["driver"], "environment_sha256": environment["sha256"],
        "kernel_revision": sha256(Path(__file__).read_bytes()).hexdigest(),
        "basis_version_ids": sorted(dependencies - earlier.keys()),
        "local_lemma_ids": sorted(dependencies & earlier.keys()),
    }
    return binding, environment, modules, hypotheses, term, proof, source, earlier


def prepare(store, statement_id, proposition, submission, earlier=()):
    """Read the approved inputs and bind the exact generated statement and proof references."""
    binding, *_ = _prepare(store, statement_id, proposition, submission, earlier)
    return binding


class _PlanMathlib:
    """Keep a reviewed import inventory for this plan and recheck its inputs on use."""

    def __init__(self, backend):
        self.backend = backend
        self.manifest = backend.manifest
        toolchain = backend.project / "lean-toolchain"
        lock = backend.project / "lake-manifest.json"
        self.metadata = {path: lean_mathlib._sha256(path) for path in (toolchain, lock)}
        helper = files("research_multiplier").joinpath("lean_support", "ImportClosure.lean").read_bytes()
        # Bind the two component hashes to the already reviewed v1 project digest.
        # The installed Mathlib driver and its manifest format remain unchanged.
        if fingerprint({
            "platform": [platform.system(), platform.machine()],
            "toolchain": self.metadata[toolchain], "lake_manifest": self.metadata[lock],
            "compiler": backend.compiler_digest, "import_reader": sha256(helper).hexdigest(),
            "compiled_imports": {path.as_posix(): digest for path, (_, digest) in backend.artifacts.items()},
            "runtime_libraries": {path.name: digest for path, digest in backend.runtime_digests.items()},
        }) != self.manifest["project_sha256"]:
            raise ValueError("The confirmed Mathlib project files changed during runtime selection.")

    def _check_layout(self):
        backend = self.backend
        _check_files(self.metadata, "A confirmed Mathlib project file changed during the plan.")
        core = (backend.executable.parent.parent / "lib" / "lean").resolve()
        if core != backend.core:
            raise ValueError("The pinned Lean toolchain library path changed during the plan.")
        roots = [core, *lean_mathlib._build_roots(backend.project)]
        namespaces = {}
        for relative, (path, _) in backend.artifacts.items():
            # Lean 4.19 resolves the first search root containing the top-level package.
            namespace = relative.parts[0] if len(relative.parts) > 1 else relative.stem
            if namespace not in namespaces:
                namespaces[namespace] = next(
                    (root for root in roots
                     if (root / namespace).is_dir() or (root / (namespace + ".olean")).exists()),
                    None,
                )
            root = namespaces[namespace]
            if root is None or (root / relative).resolve() != path:
                raise ValueError("A pinned Mathlib import path changed during the plan.")

    def assert_current(self):
        self._check_layout()
        backend = self.backend
        _check_files({backend.executable: backend.compiler_digest, **backend.runtime_digests},
                     "The pinned Lean executable or runtime libraries changed during the plan.")
        _check_files(dict(backend.artifacts.values()), "A pinned compiled Lean module changed during the plan.")

    def run(self, sources, commands):
        self._check_layout()
        code, report = self.backend.run(sources, commands)
        if code == 0:
            self.assert_current()
        return code, report


def _check_files(expected, message):
    for path, digest in expected.items():
        if not path.is_file() or lean_mathlib._sha256(path) != digest:
            raise ValueError(message)


@dataclass(frozen=True)
class _CheckedRuntime:
    executable: Path
    mathlib: lean_mathlib.LocalMathlib | _PlanMathlib | None
    native: lean_native_runtime.NativeLean | None
    audit: bytes
    environment: dict

    def require_environment(self, expected):
        if self.environment != expected:
            raise ValueError("The Lean checking environment differs from the reviewed statement.")
        return self

    def assert_current(self):
        if isinstance(self.mathlib, _PlanMathlib):
            self.mathlib.assert_current()
        elif self.native is not None:
            with TemporaryDirectory(prefix="rm-lean-plan-") as directory:
                self.native.assert_current(Path(directory), monotonic() + 30)
        else:
            raise ValueError("An active Mathlib plan is required to recheck these cached inputs.")


def _checked_runtime(expected, modules, lean, mathlib_project, *, plan=False):
    executable, mathlib = checker._runtime(lean, mathlib_project if modules else None, modules)
    native = None if mathlib else lean_native_runtime.NativeLean(executable)
    audit, environment = checker._environment(executable, mathlib, native=native, expected=expected)
    if plan and mathlib is not None:
        mathlib = _PlanMathlib(mathlib)
    return _CheckedRuntime(executable, mathlib, native, audit, environment).require_environment(expected)


def verify_environment(statement, *, lean=None, mathlib_project=None):
    """Check the selected runtime even when all requested lemma results are cached."""
    expected = statement["body"]["environment"]
    manifest = expected.get("external")
    modules = lean_mathlib.validate_manifest(manifest)["modules"] if manifest is not None else ()
    return _checked_runtime(expected, modules, lean, mathlib_project, plan=True)


def check(store, statement_id, proposition, submission, earlier=(), *, lean=None, mathlib_project=None,
          checked_runtime=None):
    """Check the exact local lemma, replaying prior checked lemmas in the same scope."""
    binding, expected, modules, hypotheses, term, proof, source, earlier = _prepare(
        store, statement_id, proposition, submission, earlier)
    runtime = (checked_runtime.require_environment(expected) if checked_runtime is not None
               else _checked_runtime(expected, modules, lean, mathlib_project))
    report, generated = checker._check(runtime.executable, runtime.audit, proposition, hypotheses, source, proof,
                                       term.expansion.declarations, term.references.values(),
                                       mathlib=runtime.mathlib, native=runtime.native)
    directly_used = set(report["basis_version_ids"])
    expanded = set(report["expanded_basis_version_ids"])
    if directly_used != set(term.references):
        raise ValueError("A scoped lemma must record exactly the bases referenced by its checked proof.")
    report.update(basis_version_ids=binding["basis_version_ids"], local_lemma_ids=binding["local_lemma_ids"],
                  expanded_basis_version_ids=sorted(expanded - earlier.keys()),
                  expanded_local_lemma_ids=sorted(expanded & earlier.keys()))
    return {
        **binding, "report": report,
        "candidate": {"statement": source, "proof": generated, "submission": submission},
    }
