"""Exercise production checkers against reproducible public scope and witness mutations."""

from copy import deepcopy
from pathlib import Path
from hashlib import sha256

from . import evaluation, verification
from .serialization import fingerprint


LIMIT = 128
SUPPORTED = {"fixed-directed-max-flow", "graph-coloring"}


def _checked_candidate(domain, frozen):
    witness = domain.candidate(frozen["small_case_spec"]).get("certificate")
    result = domain.verify_certificate(frozen, witness)
    if result.get("evidence_kind") == "counterexample":
        raise ValueError("Injection fixtures need an initially true claim.")
    return witness


def _change_scope(domain, frozen):
    spec = deepcopy(frozen["small_case_spec"])
    if domain.name == "fixed-directed-max-flow":
        spec["target_value"] += 1
    elif spec["colors"] > 1:
        spec["colors"] -= 1
    else:
        first = spec["vertices"][0]
        spec["edges"].append([first, first])
    different = domain.public_claim(spec, "Changed injection scope")
    certificate = domain.candidate(spec).get("certificate")
    domain.verify_certificate(different, certificate)
    digest = fingerprint({"domain": domain.name, "input": spec})
    return {"action": "submit", "subject_sha256": digest, "certificate": certificate}


def _damage_witness(domain, frozen, original, subject):
    certificate = deepcopy(original)
    if domain.name == "fixed-directed-max-flow":
        if not certificate["flows"]:
            raise ValueError("A flow witness injection needs at least one edge.")
        certificate["flows"][0][2] = -1
    else:
        edges = frozen["small_case_spec"]["edges"]
        if not edges:
            raise ValueError("A coloring witness injection needs at least one edge.")
        left, right = edges[0]
        certificate["coloring"][right] = certificate["coloring"][left]
    return {"action": "submit", "subject_sha256": subject, "certificate": certificate}


def run(document):
    """Record the intended failure reason and the verdict of the actual exact checker."""
    cases = evaluation._cases(document)
    if len(cases) > LIMIT:
        raise ValueError(f"An injection run supports at most {LIMIT} cases.")
    responses, results = {}, {}
    for identifier, (group, domain, frozen, subject) in sorted(cases.items()):
        if group not in {"scope_drift", "invalid_proof"}:
            continue
        if domain.name not in SUPPORTED:
            raise ValueError(f"No synthetic injection is implemented for {domain.name}.")
        original = _checked_candidate(domain, frozen)
        if group == "scope_drift":
            response = _change_scope(domain, frozen)
            rejection = "subject_sha256_mismatch"
        else:
            response = _damage_witness(domain, frozen, original, subject)
            rejection = "certificate_check_failed"
        responses[identifier] = response
        report = evaluation._judge((group, domain, frozen, subject), response)
        results[identifier] = {**report, "detected": report["observed"] == "rejected" and report["detail"] == rejection}
    if not results:
        raise ValueError("Include scope_drift or invalid_proof cases.")
    artifacts = evaluation.archive()
    report = {
        "format_version": 1,
        "mode": "injected_certificate_checks",
        "cases": artifacts.put(document),
        "responses": artifacts.put(responses),
        "checkers": {
            name: verification.checker_identity(name)
            for name in sorted({cases[identifier][1].name for identifier in results})
        },
        "injector_revision": sha256(Path(__file__).read_bytes()).hexdigest(),
        "detected": sum(result["detected"] for result in results.values()),
        "count": len(results),
        "results": results,
    }
    return {**report, "artifact": artifacts.put(report)}
