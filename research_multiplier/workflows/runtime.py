"""Execute or recover a stage through the PostgreSQL task queue."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from uuid import uuid4

from research_multiplier.policy.contracts import ArtifactRef, Budget, Subject, validate
from research_multiplier.models.model_errors import BeforeDispatchUnavailable, ModelFailure, ModelOutputError
from research_multiplier.storage.postgres import DatabaseOperationError


HARNESS = "v0a-2"
_REPLAY_VERIFICATION = ContextVar("replay_verification", default=None)


class VerificationQueueError(RuntimeError):
    """The verification queue could not complete a candidate check."""

    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


@contextmanager
def fresh_verification(execution_token=None):
    """Namespace a zero-model verification. A queued run can recover its attempt."""
    prefix = f"lean-regression:{execution_token or uuid4()}:"
    token = _REPLAY_VERIFICATION.set(prefix)
    try:
        yield f"{HARNESS}:{prefix}"
    finally:
        _REPLAY_VERIFICATION.reset(token)


def attempt_record(artifacts, row):
    def ref(prefix):
        return {"uri": row[f"{prefix}_uri"], "sha256": row[f"{prefix}_sha256"]}

    artifact = ref("artifact") if row["artifact_uri"] else None
    reason = row["failure_reason"]
    reply = {
        "job_id": str(row["job_id"]), "generation": row["lease_generation"], "role": row["role"],
        "subject": {"claim_id": str(row["claim_id"]), "contract_sha256": row["contract_sha256"]},
        "context_sha256": row["context_sha256"], "harness_version": row["harness_version"],
        "finding": row["finding"],
    }
    for key, value in (("artifact", artifact), ("failure_reason", reason), ("route_key", row["route_key"])):
        if value:
            reply[key] = value
    if row["transcript_uri"]:
        reply["transcript"] = ref("transcript")
    if row["model_provider"]:
        model = {"provider": row["model_provider"], "id": row["model_id"], "family": row["model_family"],
                 "parameters": row["model_parameters"], "prompt_sha256": row["prompt_sha256"]}
        for key, column in (("revision", "model_revision"), ("input_tokens", "input_tokens"),
                            ("output_tokens", "output_tokens",), ("cost_usd", "cost_usd")):
            value = row[column]
            if value is not None:
                model[key] = str(value) if key == "cost_usd" else value
        reply["model"] = model
    return {"id": str(row["id"]), "job_id": str(row["job_id"]), "artifact": artifact,
            "context": {"uri": f"urn:sha256:{row['context_sha256']}", "sha256": row["context_sha256"]},
            "return": reply, "result": artifacts.read_json(artifact) if artifact else {"failure_reason": reason}}


def _undispatched_failure(attempt):
    reply = attempt["return"]
    failure = attempt["result"].get("failure", {})
    return (reply["finding"] == "no_result" and "model" not in reply
            and failure.get("code") == BeforeDispatchUnavailable.code and failure.get("retryable") is True)


def schedule(store, subject, kind, payload, seconds, operation_key, *, model_calls=None):
    if model_calls is None:
        model_calls = int(kind in {"prove", "audit"})
    prefix = _REPLAY_VERIFICATION.get()
    if prefix is not None:
        if kind != "verify" or model_calls != 0 or not payload.get("lean_statement_id"):
            raise ValueError("A Lean regression can run only a zero-model verification.")
        operation_key = prefix + (operation_key or kind)
    budget: Budget = {"wall_seconds": seconds, "max_attempts": 3, "max_model_calls": model_calls}
    request = {"subject": subject, "kind": kind, "payload": payload, "budget": budget}
    return store.enqueue(request, f"{HARNESS}:{operation_key or kind}")


def execute(store, artifacts, subject: Subject, contract: ArtifactRef, kind, action, *,
            payload=None, seconds=120, operation_key=None, retry=False, formalization_ref=None, before_retry=None,
            definitions=(), dependencies=(), model_calls=None, budgeted=False, resume_undispatched=False):
    if resume_undispatched and not budgeted:
        raise ValueError("Automatic retry before dispatch requires a budgeted proposal.")
    payload = {} if payload is None else payload
    job = schedule(store, subject, kind, payload, seconds, operation_key, model_calls=model_calls)
    budget = job["budget"]
    lean_verification = kind == "verify" and budget["max_model_calls"] == 0 and bool(payload.get("lean_statement_id"))
    job_id = job["id"]
    can_resume = resume_undispatched and job["lease_generation"] < job["max_attempts"]
    failed_retry = job["status"] == "failed" and retry
    if job["status"] == "failed" and (can_resume or (failed_retry and before_retry)):
        saved = store.latest_attempt(job_id)
        if saved and saved["lease_generation"] == job["lease_generation"]:
            previous = attempt_record(artifacts, saved)
            failed_retry = failed_retry or _undispatched_failure(previous)
            if failed_retry and before_retry:
                before_retry(previous)
    if failed_retry and not budgeted:
        store.requeue_job(job_id)
    owner = str(uuid4())
    if budgeted:
        from research_multiplier.policy.lean_budget import claim

        leased = claim(store, owner, seconds, job_id, retry=failed_retry)
    else:
        leased = store.claim_job(kind, owner, seconds, job_id)
    if not leased:
        current = store.job_state(job_id)
        if current["status"] == "running" and current["lease_generation"] >= current["max_attempts"]:
            store.expire_exhausted_jobs()
            current = store.job_state(job_id)
        if current["status"] in {"succeeded", "failed"}:
            saved = store.latest_attempt(job_id)
            if saved and saved["lease_generation"] == current["lease_generation"]:
                restored = attempt_record(artifacts, saved)
                if current["status"] == "succeeded":
                    store.record_job_reuse(job_id)
                return restored
        queue_error = VerificationQueueError if lean_verification else ValueError
        if current["status"] == "cancelled":
            raise queue_error(f"The {kind} task was cancelled by the researcher.")
        if current["status"] == "failed" and current["lease_generation"] >= current["max_attempts"]:
            raise queue_error(f"The {kind} stage exhausted its recovery attempts.")
        message = f"The {kind} stage is already running. Resume after its current lease expires."
        if lean_verification:
            raise VerificationQueueError(message, retryable=True)
        raise ValueError(message)
    generation = leased[0]["lease_generation"]
    context = {"subject": subject, "contract": contract,
               "definitions": list(definitions), "dependencies": list(dependencies),
               "budget": budget, "harness_version": HARNESS}
    if "candidate" in payload:
        context["candidate"] = payload["candidate"]
    if formalization_ref:
        context["formalization"] = formalization_ref
    validate("task", "ContextPackage", context)
    context_ref = artifacts.put(context)
    body = {"job_id": str(job_id), "subject": subject, "generation": generation, "role": kind,
            "context_sha256": context_ref["sha256"], "harness_version": HARNESS, "route_key": "public-v0a"}
    started = monotonic()
    outcome = {}
    unavailable = None
    try:
        finding, result, model = action()
    except (ValueError, OSError) as error:
        outcome = {"failure_code": getattr(error, "code", type(error).__name__),
                   "retryable": getattr(error, "retryable", isinstance(error, OSError))}
        body.update(finding="no_result", failure_reason=str(error)[:2000] or type(error).__name__)
        if isinstance(error, (ModelFailure, BeforeDispatchUnavailable)):
            body["artifact"] = artifacts.put({"failure_reason": str(error), "failure": error.as_dict()})
        if isinstance(error, BeforeDispatchUnavailable):
            unavailable = error
        if isinstance(error, ModelOutputError):
            body.update(model=error.metadata, transcript=artifacts.put(error.transcript))
    else:
        body.update(finding=finding, artifact=artifacts.put(result))
        if finding == "no_result":
            outcome = {"failure_code": result.get("failure_code", "no_result"),
                       "retryable": result.get("retryable")}
            body["failure_reason"] = result["failure_reason"][:2000]
        if model:
            body.update(model=model.metadata, transcript=artifacts.put(model.transcript))
    outcome["execution_ms"] = round((monotonic() - started) * 1000)
    try:
        submitted = store.submit(kind, job_id, owner, generation, body, metrics=outcome)
    except DatabaseOperationError as error:
        # Published schemas share 23514 with other invariant violations. Only
        # a lost zero-model Lean verification lease can be retried here.
        if (lean_verification and error.state == "23514"
                and error.detail == "missing, expired, or superseded job lease"):
            raise VerificationQueueError(
                "The Lean verification lease expired or was superseded. Resume to check the same candidate.",
                retryable=True,
            ) from error
        raise
    attempt = attempt_record(artifacts, submitted)
    if resume_undispatched and unavailable is not None and unavailable.retryable and generation < job["max_attempts"]:
        raise unavailable
    return attempt
