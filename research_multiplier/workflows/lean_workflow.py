"""Bind checked Lean proof artifacts to a human-confirmed formal statement."""

import json
from pathlib import Path

from research_multiplier import flow_claim
from research_multiplier.workflows import formalization
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts, write_json
from research_multiplier.proof.lean_runner import EXPECTED_THEOREM, NETWORK_SHA256, LeanProject, artifact_ref
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint


def _scope(store, packet_id):
    packet = formalization.read(store, "id", packet_id)
    if not packet or not packet["proof_ready"]:
        raise ValueError("The current research packet must be confirmed before Lean review.")
    row = store.frozen(packet["claim_id"])
    frozen = json.loads(row["contract_bytes"])
    formalization.proof_packet(packet, frozen)
    network = flow_claim.validate_claim_scope(frozen)
    if fingerprint(network) != NETWORK_SHA256:
        raise ValueError("The Lean example is bound to the published six-vertex CLRS network.")
    return {
        "principal_packet_sha256": packet["packet_sha256"],
        "claim_contract_sha256": row["contract_sha256"],
        "flow_type": "real",
        "network": network,
    }


def draft(packet_id, project_dir, lake=None):
    with Store() as store:
        scope = _scope(store, packet_id)
        project = LeanProject(project_dir, lake)
        report = project.read_report()
        body = {
            "theorem": EXPECTED_THEOREM,
            "theorem_type": report["expected_type"],
            "scope": scope,
            "trusted_source": artifact_ref(project.expected_source),
            "environment": project.environment,
        }
        return store.query("human", "SELECT * FROM rm.propose_lean_statement(%s, %s)",
                           packet_id, store.json(body))[0]


def _statement(store, role, identifier):
    rows = store.query(role, "SELECT * FROM rm.lean_statement_status WHERE id = %s", identifier)
    if not rows:
        raise ValueError("No Lean statement matches this ID.")
    return rows[0]


def show(statement_id):
    with Store() as store:
        result = _statement(store, "human", statement_id)
        result["checks"] = store.query("human", """
            SELECT * FROM rm.lean_check_status WHERE statement_id = %s ORDER BY recorded_at, id
        """, statement_id)
        return result


def confirm(statement_id, version, checksum, actor, reason):
    with Store() as store:
        return store.query("human", "SELECT rm.confirm_lean_statement(%s,%s,%s,%s,%s) AS id",
                           statement_id, version, checksum, actor, reason)[0]


def verify(statement_id, project_dir, candidate, output_dir, lake=None):
    with Store() as store:
        statement = _statement(store, "verifier", statement_id)
        if not statement["check_ready"]:
            raise ValueError("Confirm the current Lean statement before checking its proof.")
        expected = statement["body"]
        if _scope(store, statement["packet_id"]) != expected["scope"]:
            raise ValueError("The frozen research scope differs from the reviewed Lean statement.")
        project = LeanProject(project_dir, lake)
        if (artifact_ref(project.expected_source) != expected["trusted_source"]
                or project.environment != expected["environment"]):
            raise ValueError("The current Lean source or environment differs from the reviewed version.")
        report = project.read_report(candidate)
        if report["expected_type"] != expected["theorem_type"]:
            raise ValueError("The compiled trusted proposition differs from the reviewed statement.")

        directory = paths.validate_project_write(Path(output_dir) / str(statement["id"]) / candidate)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        artifacts = Artifacts(directory / "artifacts")
        trusted = artifacts.put(project.expected_source.decode("utf-8"), raw=True)
        candidate_ref = artifacts.put(project.candidate_bundle(candidate))
        report_ref = artifacts.put(report)
        output = {**report, "statement_id": str(statement["id"]), "candidate_artifact": candidate_ref,
                  "trusted_source": trusted, "report_artifact": report_ref}
        if report["accepted"]:
            body = {
                "theorem": report["expected_declaration"],
                "candidate_theorem": report["candidate_declaration"],
                "trusted_source_sha256": trusted["sha256"],
                "environment_sha256": project.environment["sha256"],
                "checker": report["checker"],
                "candidate": candidate_ref,
                "report": report_ref,
                "dependencies": report["dependencies"],
                "compiled": report["compiled"],
                "kernel_rechecked": report["kernel_rechecked"],
                "scope_matches": report["scope_matches"],
                "accepted": report["accepted"],
            }
            checked = store.query("verifier", "SELECT * FROM rm.record_lean_check(%s,%s,%s)",
                                  statement_id, statement["statement_sha256"], store.json(body))[0]
            output["check_id"] = str(checked["id"])
            receipt = store.query("verifier", "SELECT id FROM rm.issue_lean_evidence(%s)", checked["id"])[0]
            output["evidence_id"] = str(receipt["id"])
        write_json(directory / "report.json", output)
        return output


def review(check_id, action, actor, reason):
    with Store() as store:
        return store.query("human", "SELECT rm.review_lean_check(%s,%s,%s,%s) AS id",
                           check_id, action, actor, reason)[0]
