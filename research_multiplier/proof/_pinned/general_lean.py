"""Independently check structured proof terms for reviewed Lean Core goals."""

import hashlib
from dataclasses import dataclass
from importlib.resources import files
import json
import os
from pathlib import Path
import re
from shutil import which
from tempfile import TemporaryDirectory

from . import basis, formalization, lean_core_reuse, lean_mathlib, lean_native_runtime, lean_workflow, runtime
from . import lean_parameters
from .artifacts import Artifacts, write_json
from .lean_runner import AXIOMS, TOOLCHAIN, LeanProject, artifact_ref
from .model_adapters import ModelConfig, complete_structured
from .model_errors import ModelOutputError
from .postgres import Store
from .serialization import fingerprint, json_text

DOMAIN = "lean-core"
THEOREM = "RMTheory.expected"
CANDIDATE = "RMTheory.Proof.checked"
CHECKER = {"name": "rm-lean-core-audit", "version": "1"}
MAX_NODES = 128
MAX_TYPE_CHARS = 16000
MAX_REPLAY_NODES = 768
MAX_REPLAY_THEOREMS = 24
MAX_REPLAY_DEPTH = 8
MAX_REPLAY_SOURCE_BYTES = 512 * 1024
CORE_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9']*(?:\.[A-Za-z_][A-Za-z_0-9']*)*\Z")
AUTHOR_PROMPT = (
    "Return a single JSON object with format_version 1 and proof. Prove exactly the supplied "
    "Lean Core consequent under the supplied closed antecedents. Supply no Lean code and no "
    "additional hypotheses. A proof node has one operator. intro: {count:integer,body:node} "
    "introduces variables, use: zero-based index uses an introduced variable, apply: [fn,arg,...] "
    "applies terms, and: [left,right] introduces a conjunction, left/right: node introduce a "
    "disjunction, ref: approved theorem UUID uses a listed theorem, and rfl, decide, trivial: true "
    "are fixed proof operations. A theorem's listed antecedents must be supplied as proof arguments "
    "in their recorded order. "
    "If a proof is unavailable, return {failure_reason: string}."
)


def _proposition(formal):
    if not isinstance(formal, dict) or not isinstance(formal.get("lean"), dict):
        raise ValueError("Every Lean goal and hypothesis needs a reviewed formal.lean.proposition.")
    text = formal["lean"].get("proposition")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TYPE_CHARS:
        raise ValueError("A Lean proposition must be nonempty text within the size limit.")
    return text


def _target(frozen):
    skeleton = frozen.get("formal_skeleton", {})
    if frozen["domain"] != DOMAIN or skeleton.get("format") != "rm-goal-v1":
        raise ValueError("Choose a frozen lean-core research goal before preparing a proof.")
    target = skeleton.get("target")
    goal = _proposition(target)
    shared = lean_parameters.parameters(target)
    hypotheses = []
    for row in frozen["hypotheses"]:
        formal = row.get("formal")
        if lean_parameters.parameters(formal) != shared:
            raise ValueError("A frozen hypothesis must use the exact target's shared Lean parameters.")
        hypotheses.append(_proposition(formal))
    return goal, lean_parameters.Hypotheses(hypotheses, shared)


def _implication(goal, hypotheses):
    prefix = lean_parameters.prefix(getattr(hypotheses, "parameters", ()))
    return prefix + " → ".join(f"(\n{part}\n)" for part in [*hypotheses, goal])


def _type_source(goal, hypotheses, modules=()):
    proposition = _implication(goal, hypotheses)
    imports = "".join(f"import {module}\n" for module in modules)
    return (f"import Lean\n{imports}set_option autoImplicit false\nset_option maxHeartbeats 200000\n"
            f"def {THEOREM} : Prop := {proposition}\n")


@dataclass(frozen=True)
class _Declaration:
    version_id: str
    proposition: str
    value: str
    antecedents: tuple = ()
    parameters: tuple = ()

    def render(self, name):
        scope = lean_parameters.Hypotheses(self.antecedents, self.parameters)
        return f"theorem {name} : {_implication(self.proposition, scope)} := {self.value}"


class _Expansion:
    def __init__(self):
        self.nodes = 0
        self.active = set()
        self.theorems = set()
        self.declarations = {}


class _Term:
    def __init__(self, packet_items, *, library=None, admit=False, prefix="basis_", expansion=None):
        self.items = {row["id"]: row["body"] for row in packet_items}
        self.references = {}
        self.nodes = 0
        self.library = library
        self.admit = admit
        self.prefix = prefix
        self.expansion = expansion or _Expansion()

    def generate(self, candidate):
        if (not isinstance(candidate, dict) or set(candidate) != {"format_version", "proof"}
                or type(candidate["format_version"]) is not int or candidate["format_version"] != 1):
            raise ValueError("Expected a version 1 Lean proof term.")
        return self.term(candidate["proof"], 0)

    def term(self, node, depth):
        self.nodes += 1
        self.expansion.nodes += 1
        if self.nodes > MAX_NODES or depth > MAX_NODES or self.expansion.nodes > MAX_REPLAY_NODES:
            raise ValueError("The Lean proof term is too large.")
        if not isinstance(node, dict) or len(node) != 1:
            raise ValueError("A Lean proof term needs exactly one operator per node.")
        op, value = next(iter(node.items()))
        if op == "use" and type(value) is int and 0 <= value < depth:
            return f"x_{value}"
        if op == "intro" and isinstance(value, dict) and set(value) == {"count", "body"}:
            count = value["count"]
            if type(count) is int and 1 <= count <= 24:
                names = " ".join(f"x_{index}" for index in range(depth, depth + count))
                return f"(fun {names} => {self.term(value['body'], depth + count)})"
        if op == "apply" and isinstance(value, list) and 2 <= len(value) <= 24:
            return "(" + " ".join(self.term(term, depth) for term in value) + ")"
        if op == "and" and isinstance(value, list) and len(value) == 2:
            return "⟨" + ", ".join(self.term(term, depth) for term in value) + "⟩"
        if op == "left" or op == "right":
            constructor = "Or.inl" if op == "left" else "Or.inr"
            return f"({constructor} {self.term(value, depth)})"
        if op == "ref" and isinstance(value, str):
            return self.reference(value)
        if op in {"rfl", "decide", "trivial"} and value is True:
            return "(True.intro)" if op == "trivial" else f"(by {op})"
        raise ValueError(f"Unsupported or invalid proof operator: {op}.")

    def reference(self, item_id):
        item = self.items.get(item_id)
        if item is None and self.admit and self.library:
            item = self.library.admit(item_id)
            self.items[item_id] = item
        if not item or item.get("kind") != "theorem":
            raise ValueError("A proof reference must be a currently approved Lean theorem version.")
        name = self.prefix + item_id.replace("-", "")
        self.references[item_id] = name
        if name in self.expansion.declarations:
            return name
        formal = item.get("formal")
        proposition = _proposition(formal)
        if item.get("format") == "lean-core-proof":
            if self.library is not None:
                self.expand_export(item_id, item, name, proposition)
            return name
        declaration = formal["lean"].get("declaration")
        if not isinstance(declaration, str) or not CORE_NAME.fullmatch(declaration):
            raise ValueError("The approved theorem needs an exact Lean Core declaration name.")
        self.expansion.declarations[name] = _Declaration(item_id, proposition, f"_root_.{declaration}")
        return name

    def expand_export(self, item_id, item, name, proposition):
        expansion = self.expansion
        if item_id in expansion.active or len(expansion.active) >= MAX_REPLAY_DEPTH:
            raise ValueError("The Lean theorem dependencies exceed the replay depth or contain a cycle.")
        expansion.theorems.add(item_id)
        if len(expansion.theorems) > MAX_REPLAY_THEOREMS:
            raise ValueError("The Lean proof uses too many archived theorems.")
        shared = lean_parameters.parameters(item["formal"])
        antecedents = lean_parameters.Hypotheses(lean_core_reuse.antecedents(item), shared)
        manifest = self.library.manifest
        modules = manifest["modules"] if manifest is not None else ()
        submission, basis_items, pinned = self.library.document(item, _type_source(proposition, antecedents, modules))
        expansion.active.add(item_id)
        try:
            nested = _Term(basis_items, library=self.library, prefix=f"basis_nested_{item_id.replace('-', '')}_",
                           expansion=expansion)
            proof = nested.generate(submission)
            if set(nested.references) != pinned:
                raise ValueError("The archived submission does not have its recorded theorem dependencies.")
        finally:
            expansion.active.remove(item_id)
        expansion.declarations[name] = _Declaration(item_id, proposition, proof, tuple(antecedents), shared)


def _executable(option, project=None):
    executable = which(option or "lean")
    if executable is None:
        raise ValueError("Install the pinned Lean Core toolchain or supply --lean.")
    with TemporaryDirectory(prefix="rm-lean-core-toolchain-") as directory:
        root = Path(directory)
        env = {
            "PATH": os.pathsep.join([str(Path(executable).parent), os.defpath]),
            "ELAN_HOME": os.environ.get("ELAN_HOME") or str(Path.home() / ".elan"),
        }
        if os.name == "nt" and "SYSTEMROOT" in os.environ:
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        prefix = LeanProject._run([str(Path(executable).absolute()), "--print-prefix"], project or root,
                                  env=env, timeout=15).stdout.strip()
    name = "lean.exe" if os.name == "nt" else "lean"
    return Path(prefix) / "bin" / name


def _environment(executable, mathlib=None, *, native=None, expected=None):
    audit = files("research_multiplier").joinpath("lean_support", "TheoryAudit.lean").read_bytes()
    drivers = {
        "compiler_driver": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "theorem_library": hashlib.sha256(Path(lean_core_reuse.__file__).read_bytes()).hexdigest(),
    }
    if mathlib is not None:
        drivers["mathlib_runtime"] = hashlib.sha256(Path(lean_mathlib.__file__).read_bytes()).hexdigest()
    old_compiler = "69999a236a7f418b4409c19e755b56468f60a6c7c3c8f0c284c1d458f504099e"
    old_inputs = (expected or {}).get("inputs", {})
    if old_inputs.get("compiler_driver") == old_compiler:
        # Preserve the pre-parameter ABI when reopening an already reviewed legacy scope.
        # All runtime, audit and theorem-library pins are still measured below.
        drivers["compiler_driver"] = old_compiler
    else:
        drivers["parameter_scope"] = hashlib.sha256(Path(lean_parameters.__file__).read_bytes()).hexdigest()
    inputs = {
        "auditor": hashlib.sha256(audit).hexdigest(),
        "driver": fingerprint(drivers), **drivers,
    }
    environment = {"toolchain": TOOLCHAIN, "inputs": inputs}
    if mathlib is None:
        native = native or lean_native_runtime.NativeLean(executable)
        inputs["compiler"] = native.native["compiler"]
        inputs["native_runtime_policy"] = lean_native_runtime.POLICY
        inputs["native_runtime_sha256"] = native.digest
        environment["sha256"] = fingerprint(inputs)
    else:
        inputs["mathlib_policy"] = lean_mathlib.POLICY
        environment["external"] = mathlib.manifest
        environment["sha256"] = fingerprint(environment)
    return audit, environment


def _scoped_terms(goal, hypotheses, declarations=None):
    wrap = lean_parameters.scoped_term
    shared = getattr(hypotheses, "parameters", ())
    return list(dict.fromkeys([
        *(wrap(part, shared) for part in (goal, *hypotheses)),
        *(wrap(part, declaration.parameters) for declaration in (declarations or {}).values()
          for part in (*declaration.antecedents, declaration.proposition)),
    ]))


def _check(executable, audit, goal, hypotheses, source, term, declarations, direct_references=(), *, mathlib=None,
           native=None):
    proof = ["import RMExpected", "set_option autoImplicit false", "set_option maxHeartbeats 200000",
             "namespace RMTheory.Proof"]
    proof.extend(declaration.render(name) for name, declaration in declarations.items())
    proof.extend([f"theorem checked : {THEOREM} := {term}", "end RMTheory.Proof", ""])
    candidate = "\n".join(proof)
    if len(source.encode("utf-8")) + len(candidate.encode("utf-8")) > MAX_REPLAY_SOURCE_BYTES:
        raise ValueError("The checked Lean source exceeds the replay budget.")
    terms = _scoped_terms(goal, hypotheses, declarations)
    sources = {"Audit.lean": audit, "RMExpected.lean": source.encode("utf-8"),
               "RMCandidate.lean": candidate.encode("utf-8")}
    names = [f"term{index}.txt" for index, _ in enumerate(terms)]
    sources.update({name: text.encode("utf-8") for name, text in zip(names, terms)})
    validate = ["validate"]
    inspect = ["audit"]
    if mathlib is not None:
        modules = ",".join(mathlib.manifest["modules"])
        validate = ["validate-imports", modules]
        inspect = ["audit-imports", modules]
    commands = [["--run", "Audit.lean", *validate, *names],
                ["-o", "RMExpected.olean", "RMExpected.lean"], ["-o", "RMCandidate.olean", "RMCandidate.lean"],
                ["--run", "Audit.lean", *inspect]]
    checker = mathlib or native or lean_native_runtime.NativeLean(executable)
    code, raw = checker.run(sources, commands)
    report = _lean_report(raw, "Lean did not establish the reviewed proposition.")
    if not isinstance(report, dict) or report.get("accepted") is not (code == 0):
        raise ValueError("The independent Lean kernel report is inconsistent.")
    if not report["accepted"]:
        raise ValueError("The independent Lean kernel or dependency policy rejected the proof.")
    deps = report["dependencies"]
    if (not report["kernel_rechecked"] or not report["scope_matches"] or set(deps["axioms"]) - AXIOMS
            or any(deps[key] for key in ("unauthorized_axioms", "unsafe", "unapproved"))):
        raise ValueError("The independent Lean report violates the verification policy.")
    aliases = {f"RMTheory.Proof.{name}": declaration.version_id for name, declaration in declarations.items()}
    used = set(deps["references"])
    if used - aliases.keys():
        raise ValueError("The Lean proof uses an unregistered basis declaration.")
    direct = {f"RMTheory.Proof.{name}" for name in direct_references}
    report["basis_version_ids"] = sorted({aliases[name] for name in used & direct})
    report["expanded_basis_version_ids"] = sorted({aliases[name] for name in used})
    return report, candidate


def _lean_report(raw, message):
    try:
        result = json.loads(raw.splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise ValueError(f"{message} {raw[-1500:]}") from error
    if not isinstance(result, dict):
        raise ValueError(message)
    return result


def _describe_scope(runtime, audit, source, propositions, modules=()):
    names = [f"term{index}.txt" for index, _ in enumerate(propositions)]
    inputs = {"Audit.lean": audit, "RMExpected.lean": source.encode("utf-8")}
    inputs.update({name: proposition.encode("utf-8") for name, proposition in zip(names, propositions)})
    validate = ["validate-imports", ",".join(modules)] if modules else ["validate"]
    code, raw = runtime.run(inputs, [
        ["--run", "Audit.lean", *validate, *names],
        ["-o", "RMExpected.olean", "RMExpected.lean"], ["--run", "Audit.lean", "describe"],
    ])
    if code:
        raise ValueError("The local Lean runtime could not elaborate the frozen proposition. " + raw[-1500:])
    reviewed = _lean_report(raw, "Lean did not return the elaborated proposition.").get("expected_type")
    if not isinstance(reviewed, str) or not reviewed:
        raise ValueError("Lean returned an invalid elaborated proposition.")
    return reviewed


def _runtime(lean, project=None, modules=()):
    if not modules:
        if project is not None:
            raise ValueError("Name the Mathlib modules to review with --import.")
        return _executable(lean), None
    selected = project or os.environ.get("RM_LEAN_MATHLIB_PROJECT")
    if not selected:
        raise ValueError("Select the local Lean project with --mathlib-project or RM_LEAN_MATHLIB_PROJECT.")
    selected = Path(selected).expanduser().resolve()
    if not selected.is_dir():
        raise ValueError("The selected local Lean project directory does not exist.")
    executable = _executable(lean, selected)
    return executable, lean_mathlib.LocalMathlib(selected, list(modules), executable=executable)


def propose(packet_id, lean=None, *, mathlib_project=None, modules=()):
    executable, mathlib = _runtime(lean, mathlib_project, modules)
    audit, environment = _environment(executable, mathlib)
    with Store() as store:
        packet = formalization.read(store, "id", packet_id)
        if not packet or not packet["proof_ready"]:
            raise ValueError("Confirm the research packet before proposing its Lean statement.")
        frozen = store.frozen(packet["claim_id"])
        goal, hypotheses = _target(json.loads(frozen["contract_bytes"]))
        source = _type_source(goal, hypotheses, mathlib.manifest["modules"] if mathlib else ())
        body = {
            "theorem": THEOREM, "candidate_theorem": CANDIDATE, "checker": CHECKER,
            "theorem_type": goal, "trusted_source": artifact_ref(source.encode("utf-8")),
            "environment": environment, "scope": {
                "principal_packet_sha256": packet["packet_sha256"],
                "claim_contract_sha256": frozen["contract_sha256"],
                "lean_hypotheses": hypotheses,
            },
        }
        if hypotheses.parameters:
            body["scope"]["lean_parameters"] = list(hypotheses.parameters)
        if mathlib is not None or hypotheses.parameters:
            checker = mathlib or lean_native_runtime.NativeLean(executable)
            names = mathlib.manifest["modules"] if mathlib else ()
            body["elaborated_theorem_type"] = _describe_scope(checker, audit, source, _scoped_terms(goal, hypotheses),
                                                             names)
        return store.query("human", "SELECT * FROM rm.propose_lean_core_statement(%s,%s)",
                           packet_id, store.json(body))[0]


def _author(store, artifacts, statement, subject, contract, packet, items, goal, hypotheses, retry):
    manifest = statement["body"]["environment"].get("external")
    applicable = []
    for row in items:
        item = row["body"]
        lean = (item.get("formal") or {}).get("lean")
        if item.get("kind") != "theorem" or not isinstance(lean, dict):
            continue
        if item.get("format") == "lean-core-proof":
            if (item["source"]["claim_id"] == subject["claim_id"]
                    or item["source"]["environment"].get("external") != manifest):
                continue
            applicable.append({"version_id": row["id"], "proposition": _proposition(item["formal"]),
                               "antecedents": lean_core_reuse.antecedents(item)})
        elif lean.get("declaration"):
            applicable.append({"version_id": row["id"], "proposition": _proposition(item["formal"]),
                               "declaration": lean["declaration"]})
    request = {"consequent": goal, "antecedents": hypotheses, "approved_theorems": applicable}
    prompt = AUTHOR_PROMPT
    if hypotheses.parameters:
        request["parameters"] = list(hypotheses.parameters)
        prompt += " Introduce the shared parameters first, then the reviewed hypotheses in order."
    if manifest is not None:
        request["approved_modules"] = manifest["modules"]
    if len(json_text(request).encode("utf-8")) > 48000:
        raise ValueError("The approved context exceeds the Lean author budget.")
    config = ModelConfig.from_env("LEAN_AUTHOR")
    input_ref = artifacts.put(request)
    key = "lean-core-author-v2:" + fingerprint({
        "statement": statement["statement_sha256"], "request": input_ref["sha256"], "prompt": prompt,
        "provider": config.provider, "family": config.family, "model": config.model, "endpoint": config.base_url,
        "factory": config.adapter_factory, "binary": config.binary, "tokens": config.max_output_tokens,
        "effort": config.reasoning_effort, "timeout": config.timeout_seconds,
    })

    def propose():
        result = complete_structured(config, "lean_author", prompt, request, 2400, 90)
        reason = result.output.get("failure_reason")
        if set(result.output) == {"failure_reason"} and isinstance(reason, str) and reason.strip():
            return "no_result", {"failure_reason": reason[:1500]}, result
        try:
            _Term(items).generate(result.output)
        except ValueError as error:
            raise ModelOutputError(str(error), result) from None
        return "exact_proof", result.output, result

    attempt = runtime.execute(store, artifacts, subject, contract, "prove", propose, seconds=120,
                              payload={"formalization_id": str(packet["id"]),
                                       "formalization_sha256": packet["packet_sha256"], "lean_author": True,
                                       "author_statement_id": str(statement["id"]),
                                       "author_statement_sha256": statement["statement_sha256"],
                                       "input": input_ref},
                              model_calls=1, operation_key=key, retry=retry)
    if attempt["return"]["finding"] != "exact_proof":
        raise ValueError(attempt["result"]["failure_reason"])
    return attempt


def check(statement_id, submission, output, lean=None, *, retry=False, model=False, theorems=(),
          mathlib_project=None, refute=False):
    with Store() as store:
        statement = lean_workflow._statement(store, "verifier", statement_id)
        expected = statement["body"]
        if not statement["check_ready"] or expected.get("checker") != CHECKER:
            raise ValueError("Confirm the current Lean Core statement and checking environment.")
        frozen = store.frozen(statement["claim_id"])
        frozen_contract = json.loads(frozen["contract_bytes"])
        if model and frozen_contract["sensitivity"] != "public":
            raise ValueError("Restricted Lean goals require a locally supplied structured proof.")
        if model and refute:
            raise ValueError("Supply a structured proof of the exact negation to check a refutation.")
        manifest = expected["environment"].get("external")
        modules = ()
        if manifest is not None:
            try:
                modules = lean_mathlib.validate_manifest(manifest)["modules"]
            except ValueError as error:
                raise ValueError("Draft and confirm this statement for the local Lean project.") from error
        executable, mathlib = _runtime(lean, mathlib_project if modules else None, modules)
        native = None if mathlib else lean_native_runtime.NativeLean(executable)
        audit, environment = _environment(executable, mathlib, native=native, expected=expected["environment"])
        if environment != expected["environment"]:
            raise ValueError("Confirm the current Lean Core statement and checking environment.")
        goal, hypotheses = _target(frozen_contract)
        source = _type_source(goal, hypotheses, mathlib.manifest["modules"] if mathlib else ())
        if expected["theorem_type"] != goal or artifact_ref(source.encode("utf-8")) != expected["trusted_source"]:
            raise ValueError("The Lean proposition differs from its reviewed source.")
        checked_goal, checked_hypotheses, checked_source = goal, hypotheses, source
        if refute:
            checked_goal = f"_root_.Not (\n{_implication(goal, hypotheses)}\n)"
            checked_hypotheses = ()
            checked_source = _type_source(checked_goal, checked_hypotheses, modules)
        target = "refutation" if refute else "goal"
        library = lean_core_reuse.VerifiedCoreTheorems(store, statement["packet_id"], statement["claim_id"], manifest)
        for item in dict.fromkeys(theorems):
            library.admit(item)
        items = basis.versions(store, "verifier", statement["packet_id"])
        directory = Path(output) / str(statement["id"]) / "core"
        artifacts = Artifacts(directory / "artifacts")
        subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": statement["contract_sha256"]}
        contract = artifacts.put(frozen["contract_bytes"], raw=True)
        author = None
        if model:
            packet = formalization.read(store, "id", statement["packet_id"])
            author = _author(store, artifacts, statement, subject, contract, packet, items, goal, hypotheses, retry)
            submission = author["result"]
        compiler = _Term(items, library=library, admit=not model)
        term = compiler.generate(submission)
        payload = {
            "lean_statement_id": str(statement["id"]), "statement_sha256": statement["statement_sha256"],
            "target": target, "submission": artifacts.put(submission), "checker": CHECKER,
            "checker_revision": environment["inputs"]["driver"], "verification_environment_sha256": environment["sha256"],
            "basis_version_ids": sorted(compiler.references),
        }
        if refute:
            payload["refutation_source_sha256"] = hashlib.sha256(checked_source.encode("utf-8")).hexdigest()

        def execute():
            report, generated = _check(executable, audit, checked_goal, checked_hypotheses, checked_source, term,
                                       compiler.expansion.declarations, compiler.references.values(),
                                       mathlib=mathlib, native=native)
            if not refute and "elaborated_theorem_type" in expected and (
                    report["expected_type"] != expected["elaborated_theorem_type"]):
                raise ValueError("The elaborated Lean proposition differs from the reviewed type.")
            dependencies = report["dependencies"]
            observed = dependencies.pop("leading_binder_occurrence")
            if refute:
                dependencies.update(premise_usage=[], implication_binder_occurs_in_proof=observed[0] if observed else None)
            else:
                dependencies["premise_usage"] = lean_parameters.premise_usage(frozen_contract, hypotheses, observed)
            candidate = artifacts.put({"statement": checked_source, "proof": generated, "submission": submission})
            return "exact_proof", {"report": report, "candidate": candidate}, None

        attempt = runtime.execute(store, artifacts, subject, contract, "verify", execute,
                                  payload=payload, seconds=120, operation_key="lean-core:" + fingerprint(payload),
                                  model_calls=0, retry=retry)
        if attempt["return"]["finding"] != "exact_proof":
            raise ValueError(attempt["result"]["failure_reason"])
        result = attempt["result"]
        report = result["report"]
        lean_core_reuse.archive_candidate(artifacts, result["candidate"])
        body = {
            **report, "theorem": THEOREM, "candidate_theorem": CANDIDATE,
            "target": target,
            "statement_type": goal, "trusted_source_sha256": expected["trusted_source"]["sha256"],
            "environment_sha256": environment["sha256"], "verification_environment_sha256": environment["sha256"],
            "checker_revision": payload["checker_revision"], "checker": CHECKER, "candidate": result["candidate"],
            "submission": payload["submission"], "report": attempt["artifact"], "verification_attempt_id": attempt["id"],
        }
        if refute:
            body.update(refutation_source_sha256=payload["refutation_source_sha256"],
                        refutation_mode="logical_negation", witness=None)
        record = ("SELECT id FROM rm.record_verified_lean_refutation(%s,%s,%s)" if refute
                  else "SELECT id FROM rm.record_verified_lean(%s,%s,%s)")
        receipt = store.query("verifier", record,
                              statement["id"], statement["statement_sha256"], store.json(body))[0]
        if author is not None:
            store.query("verifier", "SELECT rm.record_lean_core_author(%s,%s)", receipt["id"], author["id"])
        authors = store.query("verifier", """
            SELECT attempt_id FROM rm.lean_core_author_provenance WHERE evidence_id = %s ORDER BY attempt_id
        """, receipt["id"])
        output_document = {**report, "evidence_id": str(receipt["id"]), "attempt_id": attempt["id"],
                           "author_attempt_ids": [str(row["attempt_id"]) for row in authors]}
        if refute:
            output_document.update(evidence_kind="counterexample", refutation_mode="logical_negation", witness=None)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(directory / ("refutation.json" if refute else "report.json"), output_document)
        return output_document
