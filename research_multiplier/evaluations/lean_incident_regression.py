"""Turn a recorded Lean proof rejection into an executable development case."""

from hashlib import sha256
import re

from research_multiplier import evaluation, final_lean_local, general_lean, lean_source
from research_multiplier.workflows import lean_workflow, runtime
from research_multiplier.storage import paths
from ..storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint


CASE = "lean-structured-verification"
OUTCOMES = ("rejected", "verified")
_CANDIDATE_DIAGNOSTIC = re.compile(r"(?:^|\s)RMCandidate\.lean:(\d+):\d+: error:")
_INPUT_FIELDS = (
    "lean_statement_id", "statement_sha256", "target", "submission", "checker", "checker_revision",
    "verification_environment_sha256", "basis_version_ids", "local_lemma_ids", "refutation_source_sha256",
)


def _ref(uri, checksum):
    return {"uri": uri, "sha256": checksum}


def _by_hash(checksum):
    return _ref(f"urn:sha256:{checksum}", checksum)


def _identity():
    workspace = paths.require_workspace()
    database, directory = paths.database_id(workspace), paths.workspace_id(workspace)
    if database is None or directory is None:
        raise ValueError("Bind the selected workspace to its PostgreSQL database before using Lean regressions.")
    return {"database_id": str(database), "workspace_id": str(directory)}


def _rejection(reason):
    if reason == "The independent Lean kernel or dependency policy rejected the proof.":
        return True
    if not isinstance(reason, str) or not reason.startswith("Lean did not establish the reviewed proposition."):
        return False
    # The generated candidate starts with the fixed import, options and namespace.
    return any(int(line) >= 5 for line in _CANDIDATE_DIAGNOSTIC.findall(reason))


def _inputs(payload):
    return {field: payload[field] for field in _INPUT_FIELDS if field in payload}


def _revision(payload):
    return payload.get("local_checker_revision", payload["checker_revision"])


def _original(store, artifacts, report_id):
    matches = store.query("human", """
        SELECT i.id AS report_id, i.record_sha256, i.checker_revision AS incident_checker_revision,
               i.report_uri, i.report_sha256, j.id AS job_id, j.claim_id, j.contract_sha256, j.payload,
               a.id AS attempt_id, a.context_sha256, a.failure_reason, a.submission_sha256 AS attempt_sha256
        FROM rm.verifier_incident_status i JOIN rm.jobs j ON j.id = i.job_id
        JOIN LATERAL (
            SELECT * FROM rm.attempts
            WHERE job_id = j.id AND role = 'verify' AND finding = 'no_result' AND submitted_at <= i.created_at
            ORDER BY lease_generation DESC LIMIT 1
        ) a ON true
        WHERE i.id = %s AND j.kind = 'verify' AND starts_with(j.operation_key, %s)
    """, report_id, f"{runtime.HARNESS}:lean-core:")
    if not matches or not _rejection(matches[0]["failure_reason"]):
        raise ValueError("Link the incident to a structured Lean job with a recorded candidate rejection.")
    row, = matches
    payload = row["payload"]
    if (payload.get("checker") != general_lean.CHECKER or payload.get("target") not in {"goal", "refutation"}
            or {"lean_source", "lean_composition", "lake_validation"} & payload.keys()):
        raise ValueError("This incident needs a verifier replay for a different kind of Lean input.")
    if row["incident_checker_revision"] not in {payload["checker_revision"], _revision(payload)}:
        raise ValueError("The incident does not name the checker used by its recorded job.")

    statement = lean_workflow._statement(store, "verifier", payload["lean_statement_id"])
    frozen, _, _, _, _ = lean_source._trusted_scope(store, statement)
    expected = statement["body"]
    subject = {"claim_id": str(row["claim_id"]), "contract_sha256": row["contract_sha256"]}
    if (str(statement["claim_id"]) != subject["claim_id"]
            or statement["statement_sha256"] != payload["statement_sha256"]
            or statement["contract_sha256"] != subject["contract_sha256"]
            or frozen["contract_sha256"] != subject["contract_sha256"]
            or expected["environment"]["sha256"] != payload["verification_environment_sha256"]
            or expected["environment"]["inputs"]["driver"] != payload["checker_revision"]):
        raise ValueError("The original job no longer matches the exact reviewed Lean statement.")
    incident = _ref(row["report_uri"], row["report_sha256"])
    artifacts.read(incident)
    context_ref = _by_hash(row["context_sha256"])
    context = artifacts.read_json(context_ref)
    contract = artifacts.read(context["contract"])
    if (context["subject"] != subject or context["contract"] != _by_hash(subject["contract_sha256"])
            or contract != frozen["contract_bytes"].encode("utf-8")):
        raise ValueError("The archived job context does not match the frozen contract.")
    submission = artifacts.read_json(payload["submission"])
    if (not isinstance(submission, dict) or set(submission) != {"format_version", "proof"}
            or type(submission["format_version"]) is not int or submission["format_version"] != 1
            or not isinstance(submission["proof"], dict)):
        raise ValueError("The archived candidate is not a supported structured Lean submission.")

    source = {
        "incident_id": str(row["report_id"]), "incident_record_sha256": row["record_sha256"],
        "incident_artifact": incident, "job_id": str(row["job_id"]), "attempt_id": str(row["attempt_id"]),
        "attempt_sha256": row["attempt_sha256"], "context": context_ref, "subject": subject,
        "job_payload_sha256": fingerprint(payload), "inputs": _inputs(payload),
        "effective_checker_revision": _revision(payload), "trusted_source": expected["trusted_source"],
        "rejection": row["failure_reason"],
    }
    return source, statement, submission


def capture(store, report_id, expected, actor, reason):
    if expected not in OUTCOMES:
        raise ValueError("Review the expected Lean regression outcome before capture.")
    artifacts = Artifacts(paths.object_store(), workspace=False)
    source, _, _ = _original(store, artifacts, report_id)
    case = {"format_version": 1, "kind": CASE, "split": "development", "origin": _identity(),
            "expected": expected, "original": source}
    ref = artifacts.put(case)
    saved, = store.query("human", "SELECT * FROM rm.record_incident_regression(%s,%s,%s,%s,%s)",
                         report_id, CASE, store.json(ref), actor, reason)
    return {**saved, "case": case}


def _replay_case(store, artifacts, identity, regression_id):
    links = store.query("human", """
        SELECT report_id, artifact_uri, artifact_sha256 FROM rm.incident_regressions
        WHERE id = %s AND test_case = %s
    """, regression_id, CASE)
    if not links:
        raise ValueError("No executable Lean regression matches this ID in the restored workspace.")
    link, = links
    fixture = _ref(link["artifact_uri"], link["artifact_sha256"])
    case = artifacts.read_json(fixture)
    if (not isinstance(case, dict) or case.get("kind") != CASE or case.get("split") != "development"
            or type(case.get("format_version")) is not int or case["format_version"] != 1
            or case.get("expected") not in OUTCOMES or not isinstance(case.get("origin"), dict)
            or set(case["origin"]) != set(identity) or not all(case["origin"].values())):
        raise ValueError("The registered Lean regression is malformed.")
    if any(case["origin"][key] == value for key, value in identity.items()):
        raise ValueError("Restore the captured project into a separate workspace and database before replaying it.")

    source, statement, submission = _original(store, artifacts, link["report_id"])
    if source != case.get("original") or not statement["check_ready"]:
        raise ValueError("Replay requires the complete original incident, candidate and current confirmed Lean scope.")
    store.verify_runtime_roles(roles=("scheduler", "verifier"))
    return fixture, case["expected"], source, statement, submission


def replay(regression_id, *, lean=None, mathlib_project=None, execution_token=None):
    artifacts = Artifacts(paths.object_store(), workspace=False)
    identity = _identity()
    with Store() as store:
        fixture, expected, source, statement, submission = _replay_case(store, artifacts, identity, regression_id)
    error = None
    result = None
    output = paths.runs() / "lean-regressions"
    with runtime.fresh_verification(execution_token) as prefix:
        try:
            result = final_lean_local.check(statement["id"], submission, output, lean,
                                            mathlib_project=mathlib_project,
                                            refute=source["inputs"]["target"] == "refutation")
        except (ValueError, OSError) as caught:
            error = str(caught)[:2000]

    with Store() as store:
        attempts = store.query("verifier", """
            SELECT j.id AS job_id, j.payload, j.status, a.id AS attempt_id, a.finding, a.failure_reason,
                   a.artifact_uri, a.artifact_sha256
            FROM rm.jobs j LEFT JOIN rm.attempts a ON a.job_id = j.id AND a.lease_generation = j.lease_generation
            WHERE j.claim_id = %s AND j.kind = 'verify' AND starts_with(j.operation_key, %s)
        """, statement["claim_id"], prefix)
    observed = "inconclusive"
    execution = None
    if execution_token is not None and any(row["status"] in {"queued", "running"} for row in attempts):
        return {"status": "deferred", "model_calls": 0}
    if len(attempts) == 1 and attempts[0]["attempt_id"] is not None:
        saved, = attempts
        execution = {"job_id": str(saved["job_id"]), "attempt_id": str(saved["attempt_id"]),
                     "finding": saved["finding"], "checker_revision": _revision(saved["payload"]),
                     "job_payload_sha256": fingerprint(saved["payload"])}
        if _inputs(saved["payload"]) == source["inputs"]:
            if (saved["finding"] == "exact_proof" and result is not None
                    and result.get("attempt_id") == execution["attempt_id"] and result.get("evidence_id")
                    and all(result.get(flag) is True for flag in ("accepted", "scope_matches", "kernel_rechecked"))):
                observed = "verified"
                evidence = _ref(saved["artifact_uri"], saved["artifact_sha256"])
                artifacts.read(evidence)
                execution.update(evidence_id=result["evidence_id"], verifier_artifact=evidence)
            elif saved["finding"] == "no_result" and _rejection(saved["failure_reason"]):
                observed = "rejected"
                execution["rejection"] = saved["failure_reason"]
    report = {
        "format_version": 1, "kind": CASE, "split": "development", "fixture": fixture,
        "regression_id": str(regression_id), "original_attempt_id": source["attempt_id"],
        "workspace": identity, "expected": expected, "observed": observed,
        "status": ("inconclusive" if observed == "inconclusive"
                   else "passed" if observed == expected else "failed"),
        "execution": execution, "error": error, "model_calls": 0,
    }
    return {**report, "report": evaluation.archive().put(report)}
