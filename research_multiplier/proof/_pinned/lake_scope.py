"""Separate the meaning of a reviewed Lean scope from the bytes of a Lake build."""

from contextlib import contextmanager
from hashlib import sha256
from importlib.resources import files
import os
from pathlib import Path
import platform
from shutil import copyfile
from tempfile import TemporaryDirectory
from time import monotonic

from . import general_lean, lean_mathlib, lean_workflow, local_lemma_kernel, paths
from .artifacts import Artifacts
from .postgres import Store
from .serialization import fingerprint


def _support():
    return files("research_multiplier").joinpath("lean_support", "ScopeSnapshot.lean").read_bytes()


def _revision(helper):
    return fingerprint({"format": 1, "lean": sha256(helper).hexdigest()})


def _runtime(executable, backend, expected=None):
    audit, environment = general_lean._environment(executable, backend, expected=expected)
    return local_lemma_kernel._CheckedRuntime(
        executable, local_lemma_kernel._PlanMathlib(backend), None, audit, environment)


def open_runtime(statement, *, lean=None, mathlib_project=None):
    expected = statement["body"]["environment"]
    modules = lean_mathlib.validate_manifest(expected["external"])["modules"]
    executable, backend = general_lean._runtime(lean, mathlib_project, modules)
    return _runtime(executable, backend, expected)


def _foundation(checked):
    backend = checked.mathlib.backend
    helper = files("research_multiplier").joinpath("lean_support", "ImportClosure.lean").read_bytes()
    core = lean_mathlib._closure(checked.executable, (), [backend.core], helper, monotonic() + 30)
    return fingerprint({
        "platform": [platform.system(), platform.machine()],
        "toolchain": checked.environment["toolchain"],
        "compiler": backend.compiler_digest,
        "runtime_libraries": {path.name: digest for path, digest in backend.runtime_digests.items()},
        "core_modules": {path.as_posix(): backend.artifacts[path][1] for path in core},
    })


def _snapshot_document(checked, trusted, approved, helper):
    from .lean_source import _approval_sources, _check_internal_modules

    _check_internal_modules(checked)
    names = checked.mathlib.manifest["modules"] if checked.mathlib else ()
    generated = _approval_sources(approved)
    facts = [name for name in generated if name.endswith(".txt")]
    validate = ["validate-imports", ",".join(names)] if names else ["validate"]
    commands = [["--run", "TheoryAudit.lean", *validate, *facts]] if facts else []
    commands.extend([
        ["-o", "RMExpected.olean", "RMExpected.lean"],
        ["-o", "RMApproved.olean", "RMApproved.lean"],
        ["--run", "ScopeSnapshot.lean"],
    ])
    runtime = checked.mathlib or checked.native
    code, raw = runtime.run({
        "TheoryAudit.lean": checked.audit,
        "ScopeSnapshot.lean": helper,
        "RMExpected.lean": trusted.encode("utf-8"),
        **generated,
    }, commands)
    if code:
        raise ValueError("Lean could not measure the reviewed scope. " + raw[-1300:])
    document = general_lean._lean_report(raw, "Lean did not return the semantic scope.")
    if document["format_version"] != 1:
        raise ValueError("Lean returned an unknown semantic scope format.")
    return document


def goal_identity(checked, trusted):
    """Fingerprint the exact scoped proposition with Lean's binder-independent encoding."""
    document = _snapshot_document(checked, trusted, {}, _support())
    goal = document["goal"]
    if goal["trusted"] is not True:
        raise ValueError("Lean did not certify the reviewed test proposition.")
    return fingerprint({"format_version": document["format_version"], "expression": goal["expression"],
                        "declarations": document["declarations"]})


def _snapshot(checked, trusted, approved, helper):
    document = _snapshot_document(checked, trusted, approved, helper)
    declarations = document["declarations"]

    def component(value):
        return fingerprint({
            **value,
            "declarations": {name: declarations[name] for name in value["declarations"]},
        })

    basis = [
        {"version_id": row["version_id"], "declaration": row["declaration"], "sha256": component(row)}
        for row in sorted(document["basis"], key=lambda row: row["declaration"])
    ]
    if [{"declaration": row["declaration"], "version_id": row["version_id"]} for row in basis] != _mapping(approved):
        raise ValueError("Lean did not measure every approved theorem version.")
    return {"goal": component(document["goal"]), "basis": basis, "foundation": _foundation(checked)}


def _mapping(approved):
    return [{"declaration": name, "version_id": row[0]} for name, row in sorted(approved.items())]


def _read(store, statement, revision, approved):
    rows = store.query("verifier", "SELECT * FROM rm.get_lean_lake_baseline(%s,%s,%s)",
                       statement["id"], revision, store.json(_mapping(approved)))
    return rows[0] if rows else None


def _record(store, statement, checked, trusted, approved, helper):
    checked.require_environment(statement["body"]["environment"])
    semantic = _snapshot(checked, trusted, approved, helper)
    return store.query("verifier", "SELECT * FROM rm.record_lean_lake_baseline(%s,%s,%s,%s,%s,%s)",
                       statement["id"], statement["statement_sha256"], checked.environment["sha256"],
                       _revision(helper), store.json(_mapping(approved)), store.json(semantic))[0]


def capture_draft(statement, *, lean=None, mathlib_project=None):
    """Measure Lake meaning while the build just shown for review is available."""
    if "external" not in statement["body"]["environment"]:
        return
    from .lean_source import _accepted_declarations, _trusted_scope

    with Store() as store:
        approved = _accepted_declarations(store, statement["packet_id"])
        helper = _support()
        if _read(store, statement, _revision(helper), approved) is None:
            checked = open_runtime(statement, lean=lean, mathlib_project=mathlib_project)
            _, _, trusted, _, _ = _trusted_scope(store, statement)
            _record(store, statement, checked, trusted, approved, helper)


def _archived_modules(store, statement, checked):
    rows = store.query("verifier", """
        SELECT DISTINCT body #>> '{lean_source,module}' AS module,
               body -> 'submission' AS artifact
        FROM rm.lean_check_status WHERE statement_id = %s
          AND body #>> '{lean_source,mode}' = 'module'
          AND coalesce(body #>> '{lake_validation,runtime_sha256}', body ->> 'environment_sha256') = %s
    """, statement["id"], statement["body"]["environment"]["sha256"])
    backend = checked.mathlib.backend
    names = backend.manifest["modules"]
    archived = {}
    for row in rows:
        if row["module"] in names:
            path = Path(*row["module"].split(".")).with_suffix(".olean")
            if path in backend.artifacts and row["artifact"]["sha256"] != backend.artifacts[path][1]:
                archived[path] = row["artifact"]
    return archived


@contextmanager
def _previous_runtime(store, statement, checked):
    """Recover an upgraded statement only if archived modules reproduce its exact old build digest."""
    archived = _archived_modules(store, statement, checked)
    if not archived:
        yield None
        return
    plan = checked.mathlib
    backend = plan.backend
    expected_project = statement["body"]["environment"]["external"]["project_sha256"]
    reconstructed = fingerprint({
        "platform": [platform.system(), platform.machine()],
        "toolchain": plan.metadata[backend.project / "lean-toolchain"],
        "lake_manifest": plan.metadata[backend.project / "lake-manifest.json"],
        "compiler": backend.compiler_digest,
        "import_reader": sha256(files("research_multiplier").joinpath("lean_support", "ImportClosure.lean").read_bytes()).hexdigest(),
        "compiled_imports": {path.as_posix(): archived[path]["sha256"] if path in archived else digest
                             for path, (_, digest) in backend.artifacts.items()},
        "runtime_libraries": {path.name: digest for path, digest in backend.runtime_digests.items()},
    })
    if reconstructed != expected_project:
        yield None
        return
    with restore_archived_runtime(checked, archived) as restored:
        yield restored


@contextmanager
def restore_archived_runtime(checked, archived):
    """Restore pinned modules in a disposable project; the caller compares the exact build digest."""
    plan = checked.mathlib
    backend = plan.backend
    if archived.keys() - backend.artifacts.keys():
        yield None
        return
    artifacts = Artifacts(paths.object_store(), workspace=False)
    try:
        restored = {path: artifacts.read(ref) for path, ref in archived.items()}
    except (FileNotFoundError, ValueError):
        yield None
        return
    with TemporaryDirectory(prefix="rm-reviewed-lake-") as directory:
        root = Path(directory)
        for path in plan.metadata:
            copyfile(path, root / path.name)
        library = root / ".lake" / "build" / "lib" / "lean"
        for relative, (original, _) in backend.artifacts.items():
            if original.is_relative_to(backend.core):
                continue
            destination = library / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative in restored:
                destination.write_bytes(restored[relative])
            else:
                try:
                    os.link(original, destination)
                except OSError:
                    copyfile(original, destination)
        previous = lean_mathlib.LocalMathlib(root, backend.manifest["modules"], executable=checked.executable)
        yield _runtime(checked.executable, previous, checked.environment)


def _changes(before, after):
    changes = []
    if before["foundation"] != after["foundation"]:
        changes.append({"kind": "lean_foundation"})
    if before["goal"] != after["goal"]:
        changes.append({"kind": "frozen_goal_or_premises"})
    for original, measured in zip(before["basis"], after["basis"]):
        if original != measured:
            changes.append({"kind": "approved_theorem", "version_id": measured["version_id"],
                            "declaration": measured["declaration"]})
    return changes


def _observe_scope(store, statement, checked, changes):
    store.query("verifier", "SELECT rm.record_lean_scope_observation(%s,%s,%s)",
                statement["id"], checked.environment["sha256"], store.json(changes))


def validate(store, statement, checked, trusted, approved):
    """Return the observation binding or a focused reason for human review."""
    helper = _support()
    revision = _revision(helper)
    expected = statement["body"]["environment"]
    exact = checked.environment == expected
    baseline = _read(store, statement, revision, approved)
    if baseline is None:
        if exact:
            baseline = _record(store, statement, checked, trusted, approved, helper)
        else:
            with _previous_runtime(store, statement, checked) as previous:
                if previous is None:
                    return None, [{"kind": "reviewed_lake_build_unavailable"}]
                baseline = _record(store, statement, previous, trusted, approved, helper)
    observation = {
        "baseline_id": str(baseline["id"]),
        "scope_revision": revision,
        "snapshot_sha256": baseline["snapshot_sha256"],
        "runtime_sha256": checked.environment["sha256"],
    }
    if exact:
        _observe_scope(store, statement, checked, [])
        return observation, []
    rows = store.query("verifier", """
        SELECT 1 FROM rm.lean_check_status WHERE statement_id = %s
          AND body -> 'lake_validation' = %s LIMIT 1
    """, statement["id"], store.json(observation))
    if rows:
        _observe_scope(store, statement, checked, [])
        return observation, []
    current = _snapshot(checked, trusted, approved, helper)
    changes = _changes(baseline["snapshot"], current)
    _observe_scope(store, statement, checked, changes)
    return (None, changes) if changes else (observation, [])
