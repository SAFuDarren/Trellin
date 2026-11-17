"""Collect bounded responder outputs without scoring or disclosing case labels."""

from hashlib import sha256
import inspect
from pathlib import Path
from time import monotonic
from uuid import uuid4

from . import evaluation, flow_solver, model_adapters, paths, verification
from .artifacts import write_json
from .model_errors import ModelFailure, ModelOutputError
from .serialization import fingerprint


PROMPT = """Evaluate the exact frozen mathematical claim in this request.
Return a single JSON object with one of these forms:
{"action":"submit","subject_sha256":"the supplied digest","certificate":{...}}
{"action":"reject"}
{"action":"abstain"}
Use the original predicates, domains, and hypotheses. Submit an exact witness for a proof
or an exact counterexample. Reject an invalid request. Abstain if you cannot find a witness.

Certificate formats:
- fixed-directed-max-flow: {"flows":[[tail,head,integer_amount],...],"source_side":[vertex,...]}.
  If the optimum differs from the target, nest this under "flow_cut" with "actual_value".
- graph-coloring: {"coloring":{"vertex":positive_integer,...}}, {"self_loop":"vertex"},
  or {"exhaustive":true} when bounded exhaustive search disproves colorability.
- linear-program and semidefinite-program: {"primal":witness,"dual":witness}. For an LP
  these are vectors. For an SDP these are a symmetric matrix and a vector, respectively.
  Use integers or exact rational strings.
"""


def _source_digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _configuration(responder, config, cases):
    implementation = _source_digest(__file__)
    if responder == "model":
        return {
            "provider": config.provider, "model": config.model, "family": config.family,
            "transport_revision": _source_digest(model_adapters.__file__),
            "adapter_factory": config.adapter_factory, "reasoning_effort": config.reasoning_effort,
            "configured_output_tokens": config.max_output_tokens, "configured_timeout_seconds": config.timeout_seconds,
            "runner_revision": implementation,
        }
    sources = {}
    for _, domain, _, _ in cases.values():
        if domain.name in sources:
            continue
        candidate_source = inspect.getsourcefile(domain.candidate)
        sources[domain.name] = {
            "candidate": _source_digest(candidate_source) if candidate_source else None,
            "checker": verification.checker_identity(domain.name),
        }
    return {
        "model": "deterministic-program", "domains": sources, "runner_revision": implementation,
        "flow_solver_revision": _source_digest(flow_solver.__file__),
    }


def _model_choice(config, contract, limits, artifacts):
    try:
        run = model_adapters.complete_structured(
            config, "eval-baseline", PROMPT, contract, limits["max_output_tokens"], limits["timeout_seconds"],
        )
    except ModelOutputError as error:
        return {"action": "abstain"}, {
            "failure": error.as_dict(), "model": error.metadata, "transcript": artifacts.put(error.transcript),
        }
    except ModelFailure as error:
        if error.code == "configuration":
            raise
        return {"action": "abstain"}, {"failure": error.as_dict()}
    detail = {"model": run.metadata, "transcript": artifacts.put(run.transcript)}
    try:
        evaluation._responses({
            "format_version": 1,
            "version": {"harness": "evaluation", "model": config.model, "revision": "pending"},
            "responses": {"response": run.output},
        }, {"response"})
    except ValueError:
        detail["failure"] = {"code": "response_contract", "retryable": False}
        return {"action": "abstain"}, detail
    return run.output, detail


def _program_choice(domain, frozen, subject):
    result = domain.candidate(frozen["small_case_spec"])
    if "failure_reason" in result:
        return {"action": "abstain"}, {"unavailable": result["failure_reason"]}
    return {"action": "submit", "subject_sha256": subject, "certificate": result["certificate"]}, {}


def collect(development, holdout, split, responder, *, confirm_public=False, max_cases=32,
            timeout_seconds=60, max_output_tokens=4096):
    """Collect one response per case and archive the inputs, transcripts and resource use."""
    if split not in ("development", "holdout") or responder not in ("model", "program"):
        raise ValueError("Select a development or holdout split and a model or program responder.")
    if not (type(max_cases) is int and 1 <= max_cases <= 256
            and type(timeout_seconds) is int and 1 <= timeout_seconds <= 3600
            and type(max_output_tokens) is int and 1 <= max_output_tokens <= 32768):
        raise ValueError("Collection requires valid case, time and output-token limits.")
    cases = evaluation._split(development, holdout)[split]
    if len(cases) > max_cases:
        raise ValueError("The selected split exceeds the declared case limit.")
    config = None
    if responder == "model":
        if not confirm_public or any(case[2].get("sensitivity") != "public" for case in cases.values()):
            raise ValueError("Confirm the selected contracts are public before sending them to a model.")
        config = model_adapters.ModelConfig.from_env("PROVER", use_active=False)
        if len(config.model) > 250:
            raise ValueError("The evaluator requires a model ID of no more than 250 characters.")
    settings = _configuration(responder, config, cases)
    collection_id = str(uuid4())
    location = paths.data_root() / "evaluation" / "private" / "collections" / collection_id
    location.mkdir(mode=0o700, parents=True)
    artifacts = evaluation.archive()
    limits = {
        "max_cases": max_cases, "timeout_seconds": timeout_seconds, "max_output_tokens": max_output_tokens,
        "max_model_calls": len(cases) if config else 0,
    }
    manifest = {
        "format_version": 1, "collection_id": collection_id, "split": split, "responder": responder,
        "status": "collecting", "limits": limits, "settings": settings, "attempts": {},
        "inputs": {name: artifacts.put(document) for name, document in
                   (("development", development), ("holdout", holdout))},
    }
    manifest_path = location / "collection.json"
    write_json(manifest_path, manifest)
    started = monotonic()
    responses = {}
    for case_id, (_, domain, frozen, subject) in sorted(cases.items()):
        contract = {"frozen_claim": frozen, "subject_sha256": subject}
        case_started = monotonic()
        if config is None:
            response, detail = _program_choice(domain, frozen, subject)
        else:
            response, detail = _model_choice(config, contract, limits, artifacts)
        responses[case_id] = response
        manifest["attempts"][case_id] = {
            "contract": artifacts.put(contract), "response": artifacts.put(response),
            "elapsed_ms": round((monotonic() - case_started) * 1000), **detail,
        }
        write_json(manifest_path, manifest)
    effective_models = sorted({
        (model["id"], model.get("revision", ""), model["parameters"].get("adapter_version", ""))
        for attempt in manifest["attempts"].values() if (model := attempt.get("model"))
    })
    version = {
        "harness": f"evaluation-{responder}", "model": settings["model"],
        "revision": fingerprint({"settings": settings, "models": effective_models,
                                 "timeout_seconds": timeout_seconds, "max_output_tokens": max_output_tokens}),
    }
    document = {"format_version": 1, "version": version, "responses": responses}
    evaluation._responses(document, cases.keys())
    output = location / "responses.json"
    write_json(output, document)
    manifest.update(status="complete", responses=artifacts.put(document), elapsed_ms=round((monotonic() - started) * 1000))
    write_json(manifest_path, manifest)
    provenance = artifacts.put(manifest)
    return {
        "collection_id": collection_id, "split": split, "responder": responder, "cases": len(cases),
        "responses_path": str(output), "responses": manifest["responses"], "provenance": provenance,
    }
