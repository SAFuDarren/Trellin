"""Compile bounded, data-only submissions against the reviewed Lean goal."""

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from uuid import UUID

from .lean_runner import AXIOMS, EXPECTED_THEOREM, artifact_ref, digest
from .serialization import fingerprint


AUDIT_SOURCE = "RMFlow/CandidateAudit.lean"
AUDIT_SHA256 = "48512b1e849b94bbc0c549b426bb1d55386059770ee7fb121fc06557b797917c"
CHECKER = {"name": "rm-lean-candidate-audit", "version": "1"}
SUBMISSION_THEOREM = "RMFlow.Submission.proof"
TARGET_TYPES = {
    "goal": "RMFlow.Expected.realGoal",
    "upper_bound": "∀ flow : Edge → ℝ, Feasible flow → value flow ≤ 23",
    "attainment": "∃ flow : Edge → ℝ, Feasible flow ∧ value flow = 23",
}
EDGES = ("sa", "sc", "ac", "ca", "ab", "bc", "cd", "db", "bt", "dt")
VERTICES = frozenset("sabcdt")
MAX_NODES = 128


def _binding_id(value):
    if not isinstance(value, str):
        raise ValueError("A proof reference needs an approved basis version UUID.")
    try:
        expected = str(UUID(value))
    except ValueError:
        raise ValueError("A proof reference needs an approved basis version UUID.") from None
    if value != expected:
        raise ValueError("A proof reference needs a canonical basis version UUID.")
    return expected


def _auditor(project):
    source = (project.path / AUDIT_SOURCE).read_bytes()
    if digest(source) != AUDIT_SHA256:
        raise ValueError("Candidate auditor source differs from its reviewed version.")
    return source


def candidate_environment(project):
    """Record the exact checked compiler and auditor alongside the reviewed project."""
    source = _auditor(project)
    inputs = {
        "reviewed_project_sha256": project.environment["sha256"],
        "candidate_audit_sha256": digest(source),
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "compiler_sha256": digest(Path(project.lean).read_bytes()),
    }
    return {"toolchain": project.environment["toolchain"], "sha256": fingerprint(inputs), "inputs": inputs}


class _Compiler:
    def __init__(self, approved_bindings):
        self.available = {}
        for binding in approved_bindings:
            if not isinstance(binding, dict):
                raise ValueError("Invalid approved basis binding.")
            identifier = _binding_id(binding.get("version_id"))
            if identifier in self.available:
                raise ValueError("Duplicate approved basis version.")
            self.available[identifier] = binding
        self.chunks = []
        self.references = {}
        self.active = set()
        self.nodes = 0

    def statement(self, submission):
        if (not isinstance(submission, dict) or set(submission) != {"format_version", "target", "proof"}
                or type(submission["format_version"]) is not int or submission["format_version"] != 1
                or not isinstance(submission["target"], str) or submission["target"] not in TARGET_TYPES):
            raise ValueError("Expected a version 1 submission for goal, upper_bound, or attainment.")
        return submission["target"]

    def proof(self, form, target, suffix):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ValueError("Submission has too many proof nodes.")
        if not isinstance(form, dict) or len(form) != 1:
            raise ValueError("Each proof node must be exactly one of and, ref, cut or flow.")
        op, value = next(iter(form.items()))
        if op == "ref":
            return self.reference(value, target)
        if op == "and" and target == "goal":
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("The goal requires an upper-bound proof and an attainment proof.")
            upper = self.proof(value[0], "upper_bound", suffix + "_upper")
            attainment = self.proof(value[1], "attainment", suffix + "_attainment")
            return f"⟨{upper}, {attainment}⟩"
        if op == "cut" and target == "upper_bound":
            return self.cut(value, suffix)
        if op == "flow" and target == "attainment":
            return self.flow(value, suffix)
        raise ValueError("Proof node does not match the selected target.")

    def reference(self, value, target):
        identifier = _binding_id(value)
        if identifier in self.active:
            raise ValueError("Approved proof references must be acyclic.")
        binding = self.available.get(identifier)
        if binding is None:
            raise ValueError(f"Proof references an unavailable basis version: {identifier}.")
        submission = binding.get("submission")
        if self.statement(submission) != target:
            raise ValueError("Approved basis theorem has a different proposition.")
        theorem = "basis_" + identifier.replace("-", "")
        if identifier not in self.references:
            expected = binding.get("sha256")
            if expected is not None and fingerprint(submission) != expected:
                raise ValueError("Approved basis content differs from its recorded hash.")
            self.active.add(identifier)
            term = self.proof(submission["proof"], target, theorem)
            self.active.remove(identifier)
            self.chunks.append(f"theorem {theorem} : {TARGET_TYPES[target]} := {term}")
            self.references[identifier] = {
                "version_id": identifier, "declaration": f"RMFlow.Submission.{theorem}",
                "submission_sha256": fingerprint(submission),
            }
        return theorem

    def cut(self, side, suffix):
        if (not isinstance(side, list) or not all(isinstance(vertex, str) and vertex in VERTICES for vertex in side)
                or len(set(side)) != len(side) or "s" not in side or "t" in side):
            raise ValueError("A cut needs distinct approved vertices with s inside and t outside.")
        name = f"cut_{suffix}"
        side = set(side)
        lines = [f"theorem {name} : {TARGET_TYPES['upper_bound']} := by",
                 "  intro flow feasible"]
        facts = []
        for vertex in sorted(side - {"s"}):
            fact = f"conserve_{vertex}"
            lines += [f"  have {fact} := feasible.conservation .{vertex} (by decide) (by decide)",
                      f"  simp [inflow, outflow, edges, endpoints] at {fact}"]
            facts.append(fact)
        for edge in EDGES:
            tail, head = edge
            if tail in side and head not in side:
                fact = f"upper_{edge}"
                lines += [f"  have {fact} := (feasible.bounds .{edge}).2",
                          f"  norm_num [capacity] at {fact}"]
                facts.append(fact)
            elif tail not in side and head in side:
                fact = f"lower_{edge}"
                lines.append(f"  have {fact} := (feasible.bounds .{edge}).1")
                facts.append(fact)
        lines += ["  simp [value, source, inflow, outflow, edges, endpoints]",
                  f"  linarith only [{', '.join(facts)}]"]
        self.chunks.append("\n".join(lines))
        return name

    def flow(self, amounts, suffix):
        if (not isinstance(amounts, dict) or set(amounts) != set(EDGES)
                or any(type(amount) is not int or not 0 <= amount <= 99 for amount in amounts.values())):
            raise ValueError("A flow witness needs bounded integer amounts for all ten approved edges.")
        witness = f"flow_{suffix}"
        theorem = f"attain_{suffix}"
        definition = [f"def {witness} : Edge → ℝ"]
        definition.extend(f"  | .{edge} => {amounts[edge]}" for edge in EDGES)
        self.chunks.append("\n".join(definition))
        self.chunks.append(f"""theorem {theorem} : {TARGET_TYPES['attainment']} := by
  refine ⟨{witness}, ?_, ?_⟩
  · constructor
    · intro edge
      cases edge <;> norm_num [{witness}, capacity]
    · intro vertex interiorSource interiorSink
      cases vertex <;> simp [source, sink, inflow, outflow, edges, endpoints, {witness}] at *
      all_goals norm_num
  · simp [value, source, inflow, outflow, edges, endpoints, {witness}]
    norm_num""")
        return theorem

    def generate(self, submission):
        target = self.statement(submission)
        term = self.proof(submission["proof"], target, "candidate")
        return "\n\n".join([
            "import RMFlow.Expected\nimport Mathlib.Tactic.Linarith\nimport Mathlib.Tactic.NormNum",
            "namespace RMFlow.Submission\n\nopen RMFlow.Expected\n\nset_option maxHeartbeats 200000",
            *self.chunks,
            f"theorem proof : {TARGET_TYPES[target]} := {term}",
            "end RMFlow.Submission\n",
        ])


def _compile(project, source, audit_source, target, timeout):
    deadline = monotonic() + timeout

    def invoke(command, root, env, *, allow_rejected=False):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ValueError("The Lean candidate exceeded its time limit.")
        return project._run(command, root, env=env, allow_rejected=allow_rejected, timeout=remaining)

    with TemporaryDirectory(prefix="rm-lean-candidate-") as directory:
        root = Path(directory)
        module = root / "RMFlow"
        module.mkdir()
        env = {
            "LEAN_PATH": os.pathsep.join([str(root), *project.dependencies]),
            "LEAN_SYSROOT": str(Path(project.lean).parent.parent),
            "PATH": os.pathsep.join([str(Path(project.lean).parent), os.defpath]),
            "LANG": "C.UTF-8",
        }
        if os.name == "nt" and "SYSTEMROOT" in os.environ:
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        for name, contents in (("Expected", project.expected_source), ("Submission", source.encode("utf-8"))):
            path = module / f"{name}.lean"
            path.write_bytes(contents)
            result = invoke([project.lean, "-o", str(path.with_suffix(".olean")), str(path)],
                            root, env, allow_rejected=(name == "Submission"))
            if result.returncode:
                return {"compiled": False, "kernel_rechecked": False, "scope_matches": False, "accepted": False,
                        "expected_declaration": EXPECTED_THEOREM, "candidate_declaration": SUBMISSION_THEOREM,
                        "target": target, "dependencies": {},
                        "failures": ["Lean did not prove the candidate.",
                                     (result.stdout + "\n" + result.stderr).replace(str(root), "<candidate>")[-1800:].strip()]}
        audit = root / AUDIT_SOURCE
        audit.write_bytes(audit_source)
        result = invoke([project.lean, "--run", str(audit), target], root, env, allow_rejected=True)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise ValueError("The pinned candidate auditor did not return a kernel report.") from None
    if (not isinstance(report, dict) or report.get("target") != target
            or report.get("expected_declaration") != EXPECTED_THEOREM
            or report.get("candidate_declaration") != SUBMISSION_THEOREM or report.get("compiled") is not True
            or report.get("accepted") is not (result.returncode == 0)):
        raise ValueError("The kernel report does not match the requested candidate.")
    return report


def validate_submission(submission, approved_bindings=()):
    """Check the candidate's grammar and references without invoking Lean."""
    _Compiler(approved_bindings).generate(submission)


def check_submission(project, submission, approved_bindings=(), *, timeout=120):
    """Generate and independently check Lean code from a constrained proof request."""
    if type(timeout) not in (int, float) or not 0 < timeout <= 180:
        raise ValueError("The Lean candidate needs a positive time limit of at most 180 seconds.")
    generator = _Compiler(approved_bindings)
    source = generator.generate(submission)
    bundle = {
        "format_version": 1,
        "submission": deepcopy(submission),
        "files": {"RMFlow/Submission.lean": source},
    }
    report = _compile(project, source, _auditor(project), submission["target"], timeout)
    dependencies = report.get("dependencies", {})
    used = []
    if report["accepted"]:
        actual = dependencies.get("project_declarations")
        axioms = dependencies.get("axioms")
        if (not isinstance(actual, list) or not all(isinstance(name, str) for name in actual)
                or not isinstance(axioms, list) or not all(isinstance(name, str) for name in axioms)
                or set(axioms) - AXIOMS or report.get("kernel_rechecked") is not True
                or report.get("scope_matches") is not True
                or any(dependencies.get(field) != [] for field in ("unauthorized_axioms", "unapproved", "unsafe"))):
            raise ValueError("The candidate auditor verdict contradicts the verification policy.")
        used = [binding for binding in generator.references.values() if binding["declaration"] in actual]
        used.sort(key=lambda item: item["version_id"])
        expected = {binding["declaration"] for binding in used}
        actual_references = {name for name in actual if name.startswith("RMFlow.Submission.basis_")}
        if expected != actual_references:
            raise ValueError("Kernel proof contains an unmapped basis declaration.")
    bundle["basis_bindings"] = used
    return {
        "bundle": bundle,
        "report": {**report, "checker": CHECKER, "basis_version_ids": [item["version_id"] for item in used],
                   "basis_bindings": used, "source": artifact_ref(source.encode("utf-8"))},
    }
