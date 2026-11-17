"""Submit and recover data-only Lean proofs through the durable job queue."""

from pathlib import Path

from research_multiplier.policy import basis
from research_multiplier import flow_solver, lean_candidates
from research_multiplier.workflows import lean_workflow, runtime
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts, write_json
from research_multiplier.proof.lean_runner import EXPECTED_THEOREM, LeanProject, artifact_ref
from research_multiplier.model_adapters import ModelConfig, complete_structured
from research_multiplier.models.model_errors import ModelOutputError
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint


TARGETS = ("goal", "upper_bound", "attainment")
LEASE_SECONDS = 180
SETUP_SECONDS = 30
CHECK_SECONDS = 120
AUTHOR_PROMPT = (
    "Return a JSON object with format_version (1), target and proof. "
    "Goal proof uses {and:[upper proof, attainment proof]}. An upper proof uses "
    "{cut:[vertex names]}; an attainment proof uses {flow:{edge name:integer for all ten arcs}}. "
    "An approved lemma of the matching type may be used as {ref:version UUID}. "
    "Read the frozen real-flow network exactly. Supply no raw Lean code or extra assumptions."
)


def _directory(out, statement_id):
    return paths.validate_project_write(Path(out) / str(statement_id) / "submissions")


def _context(store, statement_id):
    statement = lean_workflow._statement(store, "verifier", statement_id)
    if not statement["check_ready"]:
        raise ValueError("Confirm the current Lean statement before submitting a candidate.")
    scope = lean_workflow._scope(store, statement["packet_id"])
    if scope != statement["body"]["scope"]:
        raise ValueError("The research scope differs from the approved Lean statement.")
    subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": statement["contract_sha256"]}
    return statement, scope, subject


def _binding_map(rows):
    return {
        row["id"]: row["body"] for row in rows
        if row["key"].startswith("lean:") and isinstance(row["body"].get("source"), dict)
        and row["body"]["source"].get("kind") == "lean" and "submission" in row["body"]
    }


def _reachable_bindings(submission, known, artifacts):
    selected = {}
    pending = [submission.get("proof")]
    visited = 0
    while pending:
        visited += 1
        if visited > lean_candidates.MAX_NODES:
            raise ValueError("Submission has too many proof nodes.")
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("and"), list):
            pending.extend(node["and"])
        if set(node) == {"ref"} and isinstance(node["ref"], str):
            identifier = node["ref"]
            if identifier in selected:
                continue
            entry = known.get(identifier)
            if entry is None:
                raise ValueError(f"Unavailable verified Lean basis version {identifier}.")
            ref = entry["submission"]
            saved = artifacts.read_json(ref)
            selected[identifier] = {"version_id": identifier, "submission": saved, "sha256": ref["sha256"]}
            pending.append(saved.get("proof"))
    return [selected[key] for key in sorted(selected)]


def _program_candidate(scope, target, published):
    value, cert = flow_solver.solve(scope["network"])
    if value != scope["network"]["target_value"]:
        raise ValueError(f"Deterministic search found value {value}, which differs from the confirmed target.")
    cut = {"cut": cert["source_side"]}
    witness = {"flow": {tail + head: amount for tail, head, amount in cert["flows"]}}
    if target == "goal":
        eligible = {}
        for key, item in sorted(published.items()):
            if item["target"] in {"upper_bound", "attainment"}:
                eligible.setdefault(item["target"], {"ref": key})
        proof = {"and": [eligible.get("upper_bound", cut), eligible.get("attainment", witness)]}
    else:
        proof = cut if target == "upper_bound" else witness
    return {"format_version": 1, "target": target, "proof": proof}


def _model_candidate(store, artifacts, statement, scope, subject, target, rows, bindings, *, retry):
    config = ModelConfig.from_env("LEAN_AUTHOR")
    input_packet = {
        "target": target, "network": scope["network"],
        "approved_goal": statement["body"]["theorem_type"],
        "frozen_basis": [
            {key: row[key] for key in ("id", "label", "sha256")} |
            {key: row["body"][key] for key in ("kind", "natural", "formal") if key in row["body"]}
            for row in rows if row["label"] is not None
        ],
        "approved_lemmas": {
            key: {"target": value["target"], "statement_type": value["statement_type"]}
            for key, value in bindings.items()
        },
    }
    packet_ref = artifacts.put(input_packet)
    contract = artifacts.put(store.frozen(subject["claim_id"])["contract_bytes"], raw=True)
    operation = "lean-author:" + fingerprint({
        "statement": statement["statement_sha256"], "input": packet_ref["sha256"],
        "provider": config.provider, "model": config.model, "family": config.family,
        "endpoint": config.base_url, "binary": config.binary, "adapter": config.adapter_factory,
        "reasoning_effort": config.reasoning_effort, "prompt": AUTHOR_PROMPT,
        "token_limit": config.max_output_tokens, "timeout": config.timeout_seconds,
    })

    def propose():
        model = complete_structured(config, "lean_author", AUTHOR_PROMPT, input_packet, 2000, 90)
        try:
            if model.output.get("target") != target:
                raise ValueError("The model changed the requested Lean target.")
            inputs = _reachable_bindings(model.output, bindings, artifacts)
            lean_candidates.validate_submission(model.output, inputs)
        except ValueError as error:
            raise ModelOutputError(str(error), model) from None
        return "exact_proof", model.output, model

    attempt = runtime.execute(
        store, artifacts, subject, contract, "prove", propose,
        payload={"formalization_id": str(statement["packet_id"]),
                 "formalization_sha256": scope["principal_packet_sha256"], "lean_author": True, "input": packet_ref},
        seconds=120, operation_key=operation, model_calls=1, retry=retry,
    )
    if attempt["return"]["finding"] == "no_result":
        raise ValueError(attempt["result"]["failure_reason"])
    return attempt["result"]


def _project(statement, project_dir, lake):
    project = LeanProject(project_dir, lake, setup_timeout=SETUP_SECONDS)
    expected = statement["body"]
    if (artifact_ref(project.expected_source) != expected["trusted_source"]
            or project.environment != expected["environment"]):
        raise ValueError("The Lean statement or its reviewed environment changed.")
    return project


def submit(statement_id, submission, target, output, project_dir, lake=None, *, model=False, retry=False):
    with Store() as store:
        statement, scope, subject = _context(store, statement_id)
        directory = _directory(output, statement["id"])
        artifacts = Artifacts(directory / "artifacts")
        rows = basis.versions(store, "verifier", statement["packet_id"])
        bindings = _binding_map(rows)
        if submission is None:
            submission = (_model_candidate(store, artifacts, statement, scope, subject, target, rows, bindings,
                                           retry=retry)
                          if model else _program_candidate(scope, target, bindings))
        if not isinstance(submission, dict) or submission.get("target") not in TARGETS:
            raise ValueError("The submission needs a supported Lean proof target.")
        inputs = _reachable_bindings(submission, bindings, artifacts)
        project = _project(statement, project_dir, lake)
        environment = lean_candidates.candidate_environment(project)
        payload = {
            "lean_statement_id": str(statement["id"]),
            "statement_sha256": statement["statement_sha256"],
            "target": submission["target"],
            "submission": artifacts.put(submission),
            "checker": lean_candidates.CHECKER,
            "checker_revision": environment["inputs"]["candidate_audit_sha256"],
            "verification_environment_sha256": environment["sha256"],
            "basis_version_ids": [entry["version_id"] for entry in inputs],
        }
        job = runtime.schedule(store, subject, "verify", payload, LEASE_SECONDS,
                               "lean:" + fingerprint(payload), model_calls=0)
        return {"job_id": str(job["id"]), "statement_id": str(statement["id"]),
                "submission": payload["submission"], "basis_version_ids": payload["basis_version_ids"],
                "target": submission["target"], "status": job["status"]}


def work(job_id, output, project_dir, lake=None, *, retry=False):
    with Store() as store:
        jobs = store.query("scheduler", "SELECT * FROM rm.jobs WHERE id = %s", job_id)
        if not jobs or jobs[0]["kind"] != "verify" or "lean_statement_id" not in jobs[0]["payload"]:
            raise ValueError("The task is not a Lean candidate verification.")
        job = jobs[0]
        payload = job["payload"]
        statement, scope, subject = _context(store, payload["lean_statement_id"])
        if ("lean:" + fingerprint(payload)) != job["operation_key"].split(":", 1)[-1]:
            raise ValueError("The Lean task does not match its durable operation key.")
        directory = _directory(output, statement["id"])
        artifacts = Artifacts(directory / "artifacts")
        submission = artifacts.read_json(payload["submission"])
        rows = basis.versions(store, "verifier", statement["packet_id"])
        bindings = _reachable_bindings(submission, _binding_map(rows), artifacts)
        if [entry["version_id"] for entry in bindings] != payload["basis_version_ids"]:
            raise ValueError("The candidate's versioned Lean dependencies changed.")
        contract = artifacts.put(store.frozen(subject["claim_id"])["contract_bytes"], raw=True)

        def verify():
            project = _project(statement, project_dir, lake)
            environment = lean_candidates.candidate_environment(project)
            if environment["sha256"] != payload["verification_environment_sha256"]:
                raise ValueError("The Lean verification environment changed. Submit a new check.")
            checked = lean_candidates.check_submission(project, submission, bindings, timeout=CHECK_SECONDS)
            report = checked["report"]
            if report["accepted"] and payload["target"] == "goal":
                if report["expected_type"] != statement["body"]["theorem_type"]:
                    raise ValueError("The checked theorem differs from the reviewed Lean target.")
            result = {"report": report, "candidate": artifacts.put(checked["bundle"])}
            if not report["accepted"]:
                result.update(failure_reason=" ".join(report["failures"]), failure_code="candidate_rejected",
                              retryable=False)
            return ("exact_proof" if report["accepted"] else "no_result"), result, None

        attempt = runtime.execute(
            store, artifacts, subject, contract, "verify", verify, payload=payload, seconds=LEASE_SECONDS,
            operation_key="lean:" + fingerprint(payload), retry=retry, model_calls=0,
        )
        result = attempt["result"]
        report = result.get("report", {"accepted": False, "failures": [result.get("failure_reason", "Check failed.")]})
        response = {**report, "job_id": str(job["id"]), "verification_attempt_id": attempt["id"]}
        if report["accepted"]:
            body = {
                "theorem": EXPECTED_THEOREM,
                "candidate_theorem": report["candidate_declaration"],
                "statement_type": report["expected_type"],
                "target": payload["target"],
                "trusted_source_sha256": statement["body"]["trusted_source"]["sha256"],
                "environment_sha256": statement["body"]["environment"]["sha256"],
                "verification_environment_sha256": payload["verification_environment_sha256"],
                "checker_revision": payload["checker_revision"],
                "checker": report["checker"],
                "candidate": result["candidate"],
                "submission": payload["submission"],
                "report": attempt["artifact"],
                "dependencies": report["dependencies"],
                "basis_version_ids": report["basis_version_ids"],
                "verification_attempt_id": attempt["id"],
                "compiled": report["compiled"],
                "kernel_rechecked": report["kernel_rechecked"],
                "scope_matches": report["scope_matches"],
                "accepted": report["accepted"],
            }
            goal = payload["target"] == "goal"
            function = "record_verified_lean" if goal else "record_lean_derivation"
            approved = store.query("verifier", f"SELECT * FROM rm.{function}(%s,%s,%s)",
                                   statement["id"], statement["statement_sha256"], store.json(body))[0]
            response["evidence_id" if goal else "item_version_id"] = str(approved["id"])
        report_dir = directory / str(job["id"])
        paths.validate_project_write(report_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(report_dir / "report.json", response)
        return response
