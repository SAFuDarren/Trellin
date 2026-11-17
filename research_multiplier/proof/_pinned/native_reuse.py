"""Archive and replay native Lean proofs against a destination's confirmed scope."""

from contextlib import contextmanager
from hashlib import sha256
from importlib.resources import files
from json import dumps
from pathlib import Path

from . import general_lean, lake_scope, lean_core_reuse, lean_source, lean_workflow
from . import local_lemma_kernel, paths, runtime
from . import lean_parameters
from .artifacts import Artifacts, write_json
from .postgres import Store
from .serialization import fingerprint


MAX_CAPSULE_BYTES = 8 * 1024 * 1024


def capture(artifacts, report):
    """Keep the kernel data in the content store rather than in the receipt JSON."""
    capsule = report.pop("native_proof", None)
    if capsule is None:
        return
    data = dumps(capsule, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_CAPSULE_BYTES:
        report["native_proof_unavailable"] = "The portable kernel proof exceeds 8 MiB."
        return
    report["native_capsule"] = artifacts.put_bytes(data)
    lean_core_reuse.archive_candidate(artifacts, report["native_capsule"])


def register(store, evidence_id, body, artifact, revision):
    binding = {
        "artifact": artifact, "candidate": body["candidate"], "submission": body["submission"],
        "basis_version_ids": body["basis_version_ids"], "verifier_revision": revision,
    }
    return store.query("verifier", "SELECT * FROM rm.register_native_lean_replay(%s,%s)",
                       evidence_id, store.json(binding))


def _receipt(store, evidence_id):
    rows = store.query("verifier", """
        SELECT checked.statement_id, checked.body, e.review_status, e.needs_recheck,
               c.status AS claim_status, c.decisive_evidence_id
        FROM rm.evidence_foundation_status e
        JOIN rm.lean_check_status checked ON checked.id = e.lean_check_id AND checked.check_ready
        JOIN rm.claims c ON c.id = e.claim_id
        WHERE e.id = %s AND e.verifier_name = 'rm-lean-core-audit'
    """, evidence_id)
    return rows[0] if rows else None


@contextmanager
def _historical_runtime(statement, report, *, lean, mathlib_project):
    environment = statement["body"]["environment"]
    if "external" not in environment:
        yield local_lemma_kernel.verify_environment(statement, lean=lean)
        return
    current = lake_scope.open_runtime(statement, lean=lean, mathlib_project=mathlib_project)
    observed = report.get("lake_validation", {}).get("runtime_sha256", environment["sha256"])
    if current.environment["sha256"] == observed:
        yield current
        return
    source = report["lean_source"]
    if source["mode"] == "module":
        relative = Path(*source["module"].split(".")).with_suffix(".olean")
        with lake_scope.restore_archived_runtime(current, {relative: report["submission"]}) as restored:
            if restored is not None and restored.environment["sha256"] == observed:
                yield restored
                return
    raise ValueError("Select the historical Lake build for this receipt. Its exact dependencies could not be recovered.")


def publish(evidence_id, run_directory=None, *, lean=None, mathlib_project=None):
    """Prepare an older native receipt on demand and export it through the theorem library."""
    with Store() as store:
        row = _receipt(store, evidence_id)
        body = row["body"] if row else {}
        if not (body.get("lean_source") or body.get("native_replay")):
            return lean_core_reuse.publish(evidence_id, run_directory)
        if (row["claim_status"] != "proved" or str(row["decisive_evidence_id"]) != str(evidence_id)
                or row["review_status"] != "approved" or row["needs_recheck"]):
            raise ValueError("The native theorem needs the current promoted exact proof.")
        published = store.query("verifier", "SELECT 1 FROM rm.current_lean_core_theorems WHERE evidence_id = %s",
                                evidence_id)
        if published:
            # Keep the same return contract as structured export.
            return store.query("verifier", "SELECT * FROM rm.publish_lean_core_theorem(%s)", evidence_id)[0]
        if not body.get("lean_source"):
            raise ValueError("This replay receipt has no portable native proof.")
        statement = lean_workflow._statement(store, "verifier", row["statement_id"])
        source = body["lean_source"]
        archive = Artifacts(paths.object_store(), workspace=False)
        try:
            raw = archive.read(body["submission"])
            proof = archive.read_json(body["candidate"])
        except FileNotFoundError:
            if run_directory is None:
                raise ValueError("The original native proof is unavailable in this workspace.") from None
            original = Artifacts(Path(run_directory) / "artifacts")
            raw = original.read(body["submission"])
            proof = original.read_json(body["candidate"])
            for ref in (body["submission"], body["candidate"]):
                lean_core_reuse.archive_candidate(original, ref)
        _, _, trusted, goal, hypotheses = lean_source._trusted_scope(store, statement)
        if (proof != {"statement": trusted, "lean_source": source, "source": body["submission"]}
                or sha256(raw).hexdigest() != source["candidate_sha256"]):
            raise ValueError("The archived native proof differs from the reviewed receipt.")
        actual = set(body["basis_version_ids"])
        active = lean_source._accepted_declarations(store, statement["packet_id"])
        names_to_ids = {entry["declaration"]: entry["version_id"] for entry in source["approved_declarations"]}
        approved = {name: pair for name, pair in active.items()
                    if pair[0] in actual and names_to_ids.get(name) == pair[0]}
        if {pair[0] for pair in approved.values()} != actual:
            raise ValueError("An actual source theorem dependency is no longer current.")
        support = lean_source._support()
        names = statement["body"]["environment"].get("external", {}).get("modules", [])
        with _historical_runtime(statement, body, lean=lean, mathlib_project=mathlib_project) as checked:
            lean_source._check_internal_modules(checked)
            if source["mode"] == "module":
                relative = Path(*source["module"].split(".")).with_suffix(".olean")
                if checked.mathlib.backend.artifacts[relative][1] != source["candidate_sha256"]:
                    raise ValueError("The compiled theorem differs from the archived original.")
            report = lean_source._audit(checked, trusted, approved, names, source["declaration"], source["module"],
                                        raw if source["mode"] == "file" else None, support,
                                        scope_terms=general_lean._scoped_terms(goal, hypotheses) if hypotheses.parameters else ())
        if not report["accepted"] or set(report["basis_version_ids"]) != actual:
            raise ValueError("The historical native proof did not repeat its exact audit and dependency check.")
        capture(archive, report)
        if "native_capsule" not in report:
            raise ValueError(report.get("native_proof_unavailable", "This checked proof has no portable native proof."))
        register(store, evidence_id, body, report["native_capsule"],
                 lean_source._revision(support, body.get("lake_validation")))
        return store.query("verifier", "SELECT * FROM rm.publish_lean_core_theorem(%s)", evidence_id)[0]


def _audit(checked, trusted, approved, names, capsule, source_count, destination_count, support):
    generated = lean_source._approval_sources(approved)
    facts = [name for name in generated if name.endswith(".txt")]
    commands = []
    if facts:
        validate = ["validate-imports", ",".join(names)] if names else ["validate"]
        commands.append(["--run", "TheoryAudit.lean", *validate, *facts])
    commands.extend([
        ["-o", "RMProofData.olean", "RMProofData.lean"],
        ["-o", "RMExpected.olean", "RMExpected.lean"],
        ["-o", "RMApproved.olean", "RMApproved.lean"],
        ["--run", "NativeReplay.lean", "replay", str(source_count), str(destination_count)],
    ])
    code, raw = (checked.mathlib or checked.native).run({
        "TheoryAudit.lean": checked.audit, "RMExpected.lean": trusted.encode("utf-8"),
        "native-proof.json": capsule, **generated, **support,
    }, commands)
    report = general_lean._lean_report(raw, "Lean did not replay the native theorem.")
    if report.get("accepted") is not (code == 0):
        raise ValueError("The independent native replay report is inconsistent.")
    return report


def reuse(statement_id, version_id, *, lean=None, mathlib_project=None, output=None, retry=False):
    """Recheck a source theorem and derive the destination from its approved basis."""
    with Store() as store:
        statement = lean_workflow._statement(store, "verifier", statement_id)
        expected = statement["body"]
        if not statement["check_ready"] or expected.get("checker") != general_lean.CHECKER:
            raise ValueError("Confirm the destination Lean statement before reusing a theorem.")
        rows = store.query("verifier", "SELECT * FROM rm.current_lean_core_theorems WHERE id = %s", version_id)
        if not rows or rows[0]["body"].get("format") != "lean-native-proof":
            raise ValueError("Choose a current native theorem version from 'lean theorems'.")
        origin = store.query("verifier", "SELECT * FROM rm.admit_lean_core_theorem(%s,%s)",
                             statement["packet_id"], version_id)[0]["body"]
        frozen, contract, trusted, goal, hypotheses = lean_source._trusted_scope(store, statement)
        names = expected["environment"].get("external", {}).get("modules", [])
        environment = expected["environment"]
        approved = lean_source._accepted_declarations(store, statement["packet_id"])
        observation = None
        if names:
            checked = lake_scope.open_runtime(statement, lean=lean, mathlib_project=mathlib_project)
            lean_source._check_internal_modules(checked)
            observation, changes = lake_scope.validate(store, statement, checked, trusted, approved)
            if changes:
                return {"status": "lake_scope_review_required", "accepted": False, "changes": changes}
        else:
            checked = local_lemma_kernel.verify_environment(statement, lean=lean, mathlib_project=mathlib_project)
        directory = paths.validate_project_write(Path(output or paths.runs() / "lean") / str(statement_id) / "replay")
        artifacts = Artifacts(directory / "artifacts")
        archive = Artifacts(paths.object_store(), workspace=False)
        source = origin["source"]
        ref = source["replay"]
        try:
            capsule = archive.read(ref)
        except FileNotFoundError:
            raise ValueError("The original native replay artifact is unavailable in this workspace.") from None
        if len(capsule) > MAX_CAPSULE_BYTES:
            raise ValueError("The native replay artifact exceeds its size limit.")
        support = {name: files("research_multiplier").joinpath("lean_support", name).read_bytes()
                   for name in ("RMProofData.lean", "NativeReplay.lean")}
        revision = fingerprint({"driver": sha256(Path(__file__).read_bytes()).hexdigest(),
                                "native_inputs": lean_source._revision(lean_source._support(), observation),
                                **{name: sha256(code).hexdigest() for name, code in support.items()}})
        replay = {
            "version_id": str(version_id), "artifact": ref, "verifier_revision": revision,
            "approved_declarations": [{"declaration": name, "version_id": row[0]}
                                      for name, row in sorted(approved.items())],
        }
        subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": frozen["contract_sha256"]}
        submission = artifacts.put({"native_replay": replay, "source_statement_sha256": source["statement_sha256"]})
        payload = {
            "target": "goal", "lean_statement_id": str(statement_id), "statement_sha256": statement["statement_sha256"],
            "checker": general_lean.CHECKER, "checker_revision": environment["inputs"]["driver"],
            "verification_environment_sha256": environment["sha256"], "submission": submission,
            "basis_version_ids": sorted({str(version_id), *(row[0] for row in approved.values())}), "native_replay": replay,
        }
        if observation is not None:
            payload["lake_validation"] = observation
        verification_ran = False
        source_premises = lean_core_reuse.antecedents(origin)

        def execute():
            nonlocal verification_ran
            verification_ran = True
            report = _audit(checked, trusted, approved, names, capsule, len(source_premises), hypotheses.argument_count, support)
            report["native_replay"] = replay
            report["origin"] = {"claim_id": source["claim_id"], "statement_sha256": source["statement_sha256"],
                                "consequent": general_lean._proposition(origin["formal"]),
                                "antecedents": source_premises, "basis_version_ids": source["basis_version_ids"]}
            if observation is not None:
                report["lake_validation"] = observation
            if not report["accepted"]:
                return "no_result", {"report": report, "failure_reason":
                                     "The destination's confirmed inputs did not complete the native replay."}, None
            allowed = {row[0] for row in approved.values()}
            used = set(report["basis_version_ids"])
            if not used <= allowed or report.get("scope_matches") is not True or report.get("kernel_rechecked") is not True:
                raise ValueError("The replay did not prove the exact destination using only its approved inputs.")
            direct = report.pop("direct_basis_version_ids")
            report["basis_version_ids"] = sorted({str(version_id), *used})
            report["expanded_basis_version_ids"] = report["basis_version_ids"]
            evidence = report["dependencies"]
            evidence["direct_basis_version_ids"] = sorted({str(version_id), *direct})
            observed = evidence.pop("leading_binder_occurrence")
            evidence["premise_usage"] = lean_parameters.premise_usage(contract, hypotheses, observed)
            capture(artifacts, report)
            candidate = artifacts.put({"statement": trusted, "native_replay": replay, "submission": submission})
            return "exact_proof", {"report": report, "candidate": candidate}, None

        attempt = runtime.execute(store, artifacts, subject, artifacts.put(frozen["contract_bytes"], raw=True),
                                  "verify", execute, payload=payload, model_calls=0, retry=retry,
                                  operation_key="lean-native-replay:" + fingerprint(payload))
        if not verification_ran:
            checked.assert_current()
        result = attempt["result"]
        if attempt["return"]["finding"] != "exact_proof":
            if "report" not in result:
                raise ValueError(result["failure_reason"])
            blocked = result["report"]
            return {**blocked, "status": "replay_blocked", "block_reason": blocked.get("status"),
                    "attempt_id": attempt["id"]}
        report = result["report"]
        lean_core_reuse.archive_candidate(artifacts, result["candidate"])
        body = {
            **report, "theorem": general_lean.THEOREM, "candidate_theorem": general_lean.CANDIDATE, "target": "goal",
            "statement_type": goal, "trusted_source_sha256": expected["trusted_source"]["sha256"],
            "environment_sha256": environment["sha256"], "verification_environment_sha256": environment["sha256"],
            "checker": general_lean.CHECKER, "checker_revision": environment["inputs"]["driver"],
            "candidate": result["candidate"], "submission": submission, "report": attempt["artifact"],
            "verification_attempt_id": attempt["id"],
        }

        def issue():
            receipt = store.query("verifier", "SELECT id FROM rm.record_verified_lean(%s,%s,%s)",
                                  statement_id, statement["statement_sha256"], store.json(body))[0]
            if report.get("native_capsule"):
                register(store, receipt["id"], body, report["native_capsule"], revision)
            return str(receipt["id"])

        evidence_id = store.transaction("verifier", issue)
        document = {**report, "status": "awaiting_evidence_review", "evidence_id": evidence_id,
                    "attempt_id": attempt["id"]}
        report_path = directory / str(attempt["id"]) / "report.json"
        report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(report_path, document)
        return document
