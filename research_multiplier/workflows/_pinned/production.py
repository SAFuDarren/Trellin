"""Orchestrate and export the PostgreSQL-backed public research workflow."""

import hashlib
import json
from pathlib import Path
from uuid import UUID

from . import basis, derivations, flow_claim, flow_certificate, formalization, production_domains, verification, v0a_models
from .artifacts import Artifacts, write_bytes, write_json
from .paths import validate_project_write
from .contracts import validate
from .postgres import Store
from .runtime import attempt_record, execute


MODEL_TIMEOUT = 210
MODEL_LEASE = MODEL_TIMEOUT + 30
RUN_KIND = "postgres-v0a"


def _new_record(claim_id, **fields):
    return {"format_version": 2, "workflow": RUN_KIND, "claim_id": claim_id, "attempts": {}, **fields}


def _read_record(path):
    data = json.loads(path.read_bytes())
    if isinstance(data, dict):
        if data.get("workflow") == RUN_KIND and data.get("format_version") == 2:
            return data, False
        if "workflow" not in data and {"subject", "contract", "attempts"} <= data.keys():
            return data, True
    raise ValueError("Expected a PostgreSQL v0a run.json.")


def run_public(spec, title, output_dir, actor_ref):
    claim = flow_claim.public_claim(spec, title)
    validate("claim", "ClaimContract", claim)
    if not actor_ref.strip():
        raise ValueError("Record the operator who approved the public statement.")
    v0a_models.validate_pair(v0a_models.ModelConfig.from_env("PROVER"),
                             v0a_models.ModelConfig.from_env("AUDITOR"))
    directory = validate_project_write(Path(output_dir) / claim["claim_id"])
    with Store() as store:
        store.verify_runtime_roles()
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        details = _new_record(claim["claim_id"], intake={"claim": claim, "actor_ref": actor_ref})
        write_json(directory / "run.json", details)
        return directory, _run(store, directory, details, False, retry=False)


def run_formalized(packet_id, output_dir, *, retry=False, strategy=None, certificate=None):
    with Store() as store:
        status = formalization.read(store, "id", packet_id)
        if not status or not status["proof_ready"]:
            raise ValueError("The formalization packet must be confirmed with its open questions resolved before proof.")
        claim_id = str(status["claim_id"])
        directory = validate_project_write(Path(output_dir) / claim_id)
        store.verify_runtime_roles(roles=("scheduler", "agent", "verifier"))
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            details, legacy = _read_record(directory / "run.json")
        except FileNotFoundError:
            details, legacy = _new_record(claim_id), False
        previous = details.get("strategy") or store.proof_strategy(claim_id)
        if previous and strategy and previous != strategy:
            raise ValueError("Use the proof strategy already selected for this run.")
        details["strategy"] = previous or strategy or "program"
        if legacy or details["claim_id"] != claim_id:
            raise ValueError("The run directory belongs to a different claim.")
        return directory, _run(store, directory, details, False, retry=retry, certificate=certificate)


def _frozen(store, details, legacy):
    subject = details.get("subject")
    claim_id = str(UUID(subject["claim_id"] if legacy else details.get("claim_id", "")))
    row = store.frozen(claim_id)
    intake = details.get("intake")
    if row is None and not legacy and not subject and intake:
        claim = intake["claim"]
        flow_claim.validate_claim_scope(claim)
        if claim["claim_id"] != claim_id:
            raise ValueError("The intake and run claim IDs differ.")
        try:
            store.freeze(claim, intake["actor_ref"])
        except ValueError:
            if store.frozen(claim_id) is None:
                raise
        row = store.frozen(claim_id)
    if row is None:
        raise ValueError("No frozen PostgreSQL claim matches this run.")
    snapshot = row["contract_bytes"]
    digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    frozen = json.loads(snapshot)
    expected = {"claim_id": claim_id, "contract_sha256": digest}
    if (row["contract_sha256"] != digest or frozen.get("claim_id") != claim_id
            or (subject and subject != expected)):
        raise ValueError("Run subject differs from the frozen PostgreSQL snapshot.")
    production_domains.get(frozen.get("domain")).validate_claim(frozen)
    if intake and intake["claim"] != frozen:
        raise ValueError("Frozen PostgreSQL snapshot differs from the submitted claim.")
    return row, snapshot, frozen, expected


def _attempt(store, artifacts, subject, attempt_id, kind):
    rows = store.query("verifier", """
        SELECT * FROM rm.attempts WHERE id = %s AND claim_id = %s AND contract_sha256 = %s
          AND role = %s
    """, attempt_id, subject["claim_id"], subject["contract_sha256"], kind)
    if not rows:
        raise ValueError(f"No saved {kind} attempt matches the frozen PostgreSQL claim.")
    return attempt_record(artifacts, rows[0])


def _legacy_proof(store, artifacts, details, legacy, subject):
    saved = details["attempts"].get("prove")
    if not saved or not (legacy or details.get("legacy_proof")):
        return None
    proof = _attempt(store, artifacts, subject, saved["id"], "prove")
    if (proof["return"]["harness_version"] != "v0a-1" or proof["return"]["finding"] != "exact_proof"
            or proof["artifact"] != saved["artifact"]):
        raise ValueError("The legacy proof differs from its PostgreSQL record.")
    details["legacy_proof"] = True
    return proof


def model_requirements(store, status, packet):
    """Return the exact model-facing network conventions for production and shadow runs."""
    if not packet:
        return flow_claim.CERTIFICATE_REQUIREMENTS
    resolutions = formalization.clarifications(store, "verifier", packet["id"])
    reaffirmed = {item["item_id"] for item in resolutions if item["resolved"]}
    reaffirmed.update(item["id"] for item in status["packet"]["items"] if item["status"] == "needs_confirmation")
    return {
        **flow_claim.CERTIFICATE_REQUIREMENTS,
        "formalization": packet,
        "reaffirmed_items": sorted(reaffirmed),
        "required_basis_items": ["H1", "H2", "H3"],
        "verified_derivations": derivations.model_entries(derivations.available(store, "verifier", packet["id"])),
    }


def _run(store, directory, details, legacy, *, retry, certificate=None):
    row, snapshot, frozen, subject = _frozen(store, details, legacy)
    domain = production_domains.get(frozen["domain"])
    flow = domain.name == production_domains.DEFAULT_DOMAIN
    if row["status"] == "needs_recheck":
        raise ValueError("Decisive evidence is stale. Reopen the claim before issuing a replacement.")
    strategy = details.get("strategy") or store.proof_strategy(subject["claim_id"]) or ("model" if flow else "program")
    if strategy not in ("program", "model"):
        raise ValueError("Unknown proof strategy for this run.")
    if strategy == "model" and not flow:
        raise ValueError("This domain uses the deterministic production strategy.")
    details["strategy"] = strategy
    status = formalization.read(store, "claim_id", subject["claim_id"])
    packet = formalization.proof_packet(status, frozen) if status else None
    if strategy == "program" and packet is None:
        raise ValueError("The deterministic strategy requires a confirmed research packet.")
    if packet:
        details["formalization"] = {key: value for key, value in packet.items() if key != "items"}
    requirements = model_requirements(store, status, packet) if strategy == "model" else flow_claim.CERTIFICATE_REQUIREMENTS
    artifacts = Artifacts(directory / "artifacts")
    if certificate is not None:
        if strategy != "program" or domain.supplied_candidate is None:
            raise ValueError("The selected production route does not accept a supplied certificate.")
        details["proposed_certificate"] = artifacts.put(certificate)
    proposed_certificate = details.get("proposed_certificate")
    contract = artifacts.put(snapshot, raw=True)
    if details.get("contract", contract) != contract:
        raise ValueError("Run contract differs from the frozen PostgreSQL snapshot.")
    write_bytes(directory / "frozen-claim.json", snapshot.encode("utf-8"))
    details.update(format_version=2, workflow=RUN_KIND, claim_id=subject["claim_id"],
                   subject=subject, contract=contract, claim_status=row["status"])
    details.pop("intake", None)

    def save(state):
        details["human_review"] = state
        write_json(directory / "run.json", details)

    existing = _restore_receipt(store, artifacts, details, directory, row, frozen)
    if existing:
        if packet and flow and details["attempts"]["prove"]["return"]["finding"] == "exact_proof":
            _register_derivations(store, directory, packet, details["attempts"]["verify"])
        save(existing)
        return details

    packet_ref = artifacts.put(packet) if packet else None
    basis_refs = [artifacts.put(version) for version in basis.versions(store, "verifier", packet["id"])] if packet else []

    def stage(kind, action, **options):
        record = execute(store, artifacts, subject, contract, kind, action,
                         formalization_ref=packet_ref, definitions=basis_refs, **options)
        details["attempts"][kind] = record
        save("in_progress")
        return record

    def stop(record, state):
        save(state)
        problem = record["return"].get("failure_reason") or record["result"].get("reason")
        raise ValueError(f"{problem or 'The stage could not finish.'} Review {directory}")

    proof = _legacy_proof(store, artifacts, details, legacy, subject)
    screened_refutation = False
    save("in_progress")
    if proof is None and strategy == "model":
        def screen():
            result = flow_certificate.check_small_cases(frozen["small_case_spec"])
            return ("counterexample" if result["counterexample"] else "numeric_observation"), result, None

        screening = stage("small_case", screen, retry=retry)
        if screening["return"]["finding"] == "no_result":
            stop(screening, "retryable")
        if screening["result"]["counterexample"]:
            if packet is None:
                save("screen_requires_formalization")
                raise ValueError(
                    f"Screening found a lower cut. Confirm the formalization before admitting a refutation. Review {directory}"
                )
            screened_refutation = True

    if proof is None and strategy == "model" and not screened_refutation:
        from .evolution import configuration_sha256, production_revision

        config = v0a_models.ModelConfig.from_env("PROVER")
        route_key = f"prove-config:{configuration_sha256(config)}:{production_revision()}"

        def produce():
            model = v0a_models.prove(config, frozen, requirements, timeout=MODEL_TIMEOUT)
            if "clarification" in model.output:
                return "no_result", {**model.output, "failure_reason": "The prover found an ambiguity."}, model
            finding = "no_result" if "failure_reason" in model.output else "exact_proof"
            return finding, model.output, model

        def preserve_question(record):
            question = record["result"].get("clarification")
            if not packet or not question:
                return
            details["attempts"]["prove"] = record
            if question["item_id"] in requirements["reaffirmed_items"]:
                details.pop("clarification_request_id", None)
                return
            request_id = formalization.request(store, packet["id"], **question)
            details["clarification_request_id"] = str(request_id)
            save("needs_clarification")
            raise ValueError(f"The prover found an ambiguity in {question['item_id']}. "
                             f"Run formalization show {packet['id']} to review it.")

        payload = {"formalization_id": packet["id"], "formalization_sha256": packet["packet_sha256"]} if packet else None
        proof = stage("prove", produce, seconds=MODEL_LEASE, retry=retry, payload=payload,
                      operation_key=route_key, before_retry=preserve_question if packet else None)
        if proof["return"]["finding"] == "no_result":
            preserve_question(proof)
            stop(proof, "retryable")

    if proof is None:
        def deterministic_candidate():
            result = (domain.candidate(frozen["small_case_spec"]) if proposed_certificate is None
                      else domain.supplied_candidate(frozen["small_case_spec"], artifacts.read_json(proposed_certificate)))
            if "failure_reason" in result:
                return "no_result", result, None
            finding = "counterexample" if result.get("evidence_kind") == "counterexample" else "exact_proof"
            return finding, result, None

        payload = {
            "formalization_id": packet["id"], "formalization_sha256": packet["packet_sha256"],
            "strategy": "program",
        }
        operation = "prove-screened-refutation:" + packet["packet_sha256"] if screened_refutation else None
        if proposed_certificate:
            payload["supplied_certificate"] = proposed_certificate
            operation = "prove-supplied:" + proposed_certificate["sha256"]
        proof = stage("prove", deterministic_candidate, payload=payload, operation_key=operation, retry=retry,
                      model_calls=0)
        if proof["return"]["finding"] == "no_result":
            stop(proof, "candidate_unavailable")

    candidate = proof["result"]
    _export_proof(directory, candidate)
    target = {"candidate_attempt_id": proof["id"], "candidate": proof["artifact"]}
    checker = verification.checker_identity(domain.name)

    def verify():
        if packet and strategy == "model" and not screened_refutation:
            live_requirements = {
                **requirements, "verified_derivations": derivations.model_entries(
                    derivations.available(store, "verifier", packet["id"])),
            }
            derivations.validate_basis(live_requirements, candidate["basis_items"])
        result = verification.check(frozen, candidate, checker)
        finding = "counterexample" if result["verification"].get("evidence_kind") == "counterexample" else "exact_proof"
        if finding != proof["return"]["finding"]:
            raise ValueError("The verified result differs from the candidate's declared finding.")
        return finding, result, None

    checked = stage(
        "verify", verify,
        payload={**target, **checker}, operation_key=f"verify:{proof['id']}:{checker['checker_revision']}",
    )
    if checked["return"]["finding"] == "no_result":
        stop(checked, "certificate_rejected")
    write_json(directory / "verification.json", checked["result"]["verification"])

    audit_requirements = requirements
    if packet and flow and checked["return"]["finding"] == "exact_proof":
        _register_derivations(store, directory, packet, checked)
        if strategy == "model":
            # Read the same current, admissible basis used for proof generation.
            audit_requirements = {
                **requirements, "verified_derivations": derivations.model_entries(
                    derivations.available(store, "verifier", packet["id"])),
            }

    if strategy == "program" or screened_refutation:
        details["evidence_id"] = verification.issue(store, subject, proof, checked, domain=domain.name)
        save("pending")
        return details

    def audit_candidate():
        config = v0a_models.ModelConfig.from_env("AUDITOR")
        if config.family.casefold() == proof["return"]["model"]["family"].casefold():
            raise ValueError("The auditor must be from a different model family.")
        model = v0a_models.audit(config, frozen, candidate, audit_requirements, timeout=MODEL_TIMEOUT)
        if model.output["verdict"] == "inconclusive" and model.output["diff_status"] != "different":
            return "no_result", {**model.output, "failure_reason": model.output["reason"]}, model
        return "audit_report", model.output, model

    audit = stage("audit", audit_candidate, seconds=MODEL_LEASE, payload=target,
                  operation_key=f"audit:{proof['id']}", retry=retry)
    write_json(directory / "audit.json", audit["result"])
    if audit["return"]["finding"] == "no_result":
        stop(audit, "awaiting_audit")
    if (audit["result"].get("verdict"), audit["result"].get("diff_status")) != ("verified", "exact"):
        stop(audit, "needs_human")
    details["evidence_id"] = verification.issue(store, subject, proof, checked, historical_audit=audit)
    details.pop("clarification_request_id", None)
    save("pending")
    return details


def _register_derivations(store, directory, packet, checked):
    derivations.register(store, packet["id"], checked)
    available = derivations.available(store, "verifier", packet["id"])
    write_json(directory / "derivations.json", available)
    return available


def _export_proof(directory, candidate):
    write_bytes(directory / "proof.md", candidate["proof_markdown"].encode("utf-8"))
    write_json(directory / "certificate.json", candidate["certificate"])


def _restore_receipt(store, artifacts, details, directory, claim, frozen):
    subject = details["subject"]
    rows = store.query("verifier", """
        SELECT e.id, e.attempt_id, e.audit_attempt_id, e.verification_attempt_id, e.result
        FROM rm.evidence_foundation_status e
        WHERE e.claim_id = %s AND e.contract_sha256 = %s AND NOT e.needs_recheck
          AND e.kind IN ('exact_certificate', 'counterexample') AND e.verdict = 'verified' AND e.diff_status = 'exact'
          AND (e.issuance_sha256 IS NOT NULL OR e.id = %s OR e.id = %s)
        ORDER BY (e.id IS NOT DISTINCT FROM %s) DESC, e.issued_at DESC, e.id
        LIMIT 1
    """, subject["claim_id"], subject["contract_sha256"], claim["decisive_evidence_id"],
        details.get("evidence_id"), claim["decisive_evidence_id"])
    if not rows:
        return None
    receipt = rows[0]
    check_id = receipt["verification_attempt_id"] or receipt["result"].get("verification_attempt_id")
    for kind, attempt_id in (("prove", receipt["attempt_id"]), ("verify", check_id),
                             ("audit", receipt["audit_attempt_id"])):
        if attempt_id is not None:
            details["attempts"][kind] = _attempt(store, artifacts, subject, attempt_id, kind)
    attempts = details["attempts"]
    proof = attempts["prove"]["result"]
    checked = attempts["verify"]["result"]
    expected = checked.get("verification", checked)
    if production_domains.get(frozen["domain"]).verify_certificate(frozen, proof["certificate"]) != expected:
        raise ValueError("Saved evidence did not reproduce with the current checker.")
    _export_proof(directory, proof)
    write_json(directory / "verification.json", expected)
    if "audit" in attempts:
        write_json(directory / "audit.json", attempts["audit"]["result"])
    decisions = store.query("verifier", """
        SELECT action FROM rm.decisions WHERE evidence_id = %s AND action IN ('approve','return')
        ORDER BY sequence_no DESC LIMIT 1
    """, receipt["id"])
    details["evidence_id"] = str(receipt["id"])
    if decisions:
        return {"approve": "approved", "return": "returned"}[decisions[0]["action"]]
    return "pending"


def resume(directory, *, retry=False):
    directory = Path(directory)
    try:
        details, legacy = _read_record(directory / "run.json")
    except FileNotFoundError:
        if not directory.is_dir():
            raise
        details, legacy = _new_record(str(UUID(directory.name))), False
    with Store() as store:
        roles = ("scheduler", "agent", "verifier")
        if details.get("intake"):
            roles += ("human",)
        store.verify_runtime_roles(roles=roles)
        return _run(store, directory, details, legacy, retry=retry)


def resume_audit(directory):
    return resume(directory, retry=True)["evidence_id"]


def replay(path):
    details, _ = _read_record(path)
    artifacts = Artifacts(path.parent / "artifacts")
    try:
        subject, contract = details["subject"], details["contract"]
        proof_ref = details["attempts"]["prove"]["artifact"]
        verify_ref = details["attempts"]["verify"]["artifact"]
    except KeyError:
        raise ValueError("The run does not have a proof and completed certificate check.") from None
    frozen = json.loads(artifacts.read(contract))
    if contract["sha256"] != subject.get("contract_sha256") or frozen.get("claim_id") != subject.get("claim_id"):
        raise ValueError("Run subject differs from the frozen contract.")
    proof = artifacts.read_json(proof_ref)
    saved = artifacts.read_json(verify_ref)
    return production_domains.get(frozen.get("domain")).verify_certificate(
        frozen, proof["certificate"]) == saved.get("verification", saved)


def review_evidence(evidence_id, action, actor_ref, reason, *, explanation=None, lesson=None, minutes=None):
    with Store() as store:
        return store.review(evidence_id, action, actor_ref, reason,
                            explanation=explanation, lesson=lesson, minutes=minutes)


def promote_evidence(evidence_id, reason):
    with Store() as store:
        return store.promote(evidence_id, reason)
