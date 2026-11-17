"""Check the frozen statement and issue a receipt from committed attempts."""

import hashlib
from pathlib import Path
import platform

from . import production_domains
from .serialization import fingerprint


def checker_identity(domain=production_domains.DEFAULT_DOMAIN):
    checker = production_domains.get(domain)
    digest = hashlib.sha256()
    for module in checker.checker_sources:
        digest.update(hashlib.sha256(Path(module.__file__).read_bytes()).digest())
    return {"checker_name": checker.checker_name, "checker_version": checker.checker_version,
            "checker_revision": digest.hexdigest()}


def check(frozen, candidate, identity):
    domain = production_domains.get(frozen.get("domain"))
    return {
        "verification": domain.verify_certificate(frozen, candidate["certificate"]),
        "checker": identity,
        "environment": {"python": platform.python_version(), "system": platform.system(),
                        "machine": platform.machine()},
    }


def issue(store, subject, proof, checked, *, domain=production_domains.DEFAULT_DOMAIN, historical_audit=None):
    result = checked["result"]
    identity = result["checker"]
    if identity != checker_identity(domain):
        raise ValueError("The current program checker must accept the candidate.")
    kind = result["verification"].get("evidence_kind", "exact_certificate")
    audit = {}
    if historical_audit is not None:
        audit = {"audit_attempt_id": historical_audit["id"], "audit_mode": "cross_family"}
    environment = result["environment"]
    return str(store.issue_exact({
        "subject": subject, "attempt_id": proof["id"], "artifact": proof["artifact"],
        "kind": kind, "verdict": "verified",
        "diff_status": "exact", "audit_mode": "none", **audit,
        "verifier": {"name": identity["checker_name"], "version": identity["checker_version"]},
        "provenance": {"code_revision": identity["checker_revision"],
                       "environment_sha256": fingerprint(environment)},
        "result": {
            **result["verification"], "verification_attempt_id": checked["id"],
            "verification_artifact": checked["artifact"], "environment": environment,
        },
    }))
