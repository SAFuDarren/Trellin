"""Exercise semantic baseline and runtime binding at the PostgreSQL boundary."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier.storage import paths
from research_multiplier.workflows import runtime
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanLakeAdmissionTests(unittest.TestCase):
    def test_job_and_receipt_bind_immutable_baseline_and_measured_runtime(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        anchor, scope, _ = case.anchor("lake-baseline-database-fences", "True ∧ True", [
            {"kind": "theorem", "natural": "The natural order is reflexive.",
             "formal": {"lean": {"proposition": "∀ n : Nat, n ≤ n", "declaration": "Nat.le_refl"}}},
        ], module="Mathlib.Data.Real.Basic")
        basis = case.cli("research", "basis", anchor["packet_id"])
        version, = [row["id"] for row in basis if row["body"]["kind"] == "theorem"]
        approved = [{"version_id": version, "declaration": "Nat.le_refl"}]
        snapshot = {"goal": "1" * 64, "foundation": "2" * 64,
                    "basis": [{**approved[0], "sha256": "3" * 64}]}
        scope_revision = "4" * 64
        runtime_sha = "5" * 64
        expected = anchor["body"]
        original = expected["environment"]["sha256"]

        with patch.dict(os.environ, case.fixture.env), Store() as store:
            def save(*, role="verifier", observed=original, mapped=approved, content=snapshot):
                return store.query(role, "SELECT * FROM rm.record_lean_lake_baseline(%s,%s,%s,%s,%s,%s)",
                                   anchor["id"], anchor["statement_sha256"], observed, scope_revision,
                                   store.json(mapped), store.json(content))[0]

            with self.assertRaisesRegex(ValueError, "permission denied"):
                save(role="scheduler")
            with self.assertRaisesRegex(ValueError, "exact original statement and environment"):
                save(observed=runtime_sha)
            with self.assertRaisesRegex(ValueError, "exact current reviewed declaration map"):
                save(mapped=[])
            with self.assertRaisesRegex(ValueError, "every reviewed declaration exactly once"):
                save(content={**snapshot, "basis": []})
            baseline = save()
            self.assertEqual(save()["id"], baseline["id"])
            with self.assertRaisesRegex(ValueError, "already recorded"):
                save(content={**snapshot, "goal": "6" * 64})
            stored, = store.query("verifier", "SELECT * FROM rm.get_lean_lake_baseline(%s,%s,%s)",
                                  anchor["id"], scope_revision, store.json(approved))
            self.assertEqual(stored["snapshot"], snapshot)

            # Keep this fixture focused on database admission. The separate
            # Lake workflow tests supply and audit real source declarations.
            artifacts = Artifacts(paths.object_store(), workspace=False)
            raw = artifacts.put_bytes(b"isolated Lake SQL fixture")
            subject = {"claim_id": scope["claim_id"], "contract_sha256": anchor["contract_sha256"]}
            contract = artifacts.put(store.frozen(scope["claim_id"])["contract_bytes"], raw=True)
            source = {"mode": "file", "module": "RMSubmitted", "declaration": "result",
                      "verifier_revision": "7" * 64, "candidate_sha256": raw["sha256"],
                      "approved_declarations": approved}
            binding = {
                "baseline_id": str(baseline["id"]), "scope_revision": scope_revision,
                "snapshot_sha256": baseline["snapshot_sha256"], "runtime_sha256": runtime_sha,
            }
            payload = {
                "lean_statement_id": anchor["id"], "statement_sha256": anchor["statement_sha256"],
                "target": "goal", "checker": expected["checker"],
                "checker_revision": expected["environment"]["inputs"]["driver"],
                "verification_environment_sha256": original, "submission": raw,
                "basis_version_ids": [version], "lean_source": source, "lake_validation": binding,
            }
            wrong_inputs = [
                ({key: value for key, value in payload.items() if key != "lake_validation"},
                 "requires a semantic baseline"),
                ({**payload, "verification_environment_sha256": runtime_sha}, "reviewed statement"),
                ({**payload, "lake_validation": {**binding, "snapshot_sha256": "0" * 64}},
                 "original semantic baseline"),
                ({**payload, "basis_version_ids": [], "lean_source": {**source, "approved_declarations": []}},
                 "original semantic baseline"),
            ]
            for index, (proposal, error) in enumerate(wrong_inputs):
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                    runtime.schedule(store, subject, "verify", proposal, 60, f"bad-lake:{index}", model_calls=0)

            def prove(binding, operation):
                job_payload = {**payload, "lake_validation": binding}
                attempt = runtime.execute(
                    store, artifacts, subject, contract, "verify",
                    lambda: ("exact_proof", {"fixture": "isolated Lake database admission"}, None),
                    payload=job_payload, seconds=60, operation_key=operation, model_calls=0,
                )
                return {
                    **{key: job_payload[key] for key in ("target", "checker", "checker_revision",
                        "verification_environment_sha256", "submission", "lean_source", "lake_validation")},
                    "theorem": expected["theorem"], "candidate_theorem": "RMTheory.Proof.checked",
                    "statement_type": expected["theorem_type"], "environment_sha256": original,
                    "expected_type": expected["elaborated_theorem_type"],
                    "trusted_source_sha256": expected["trusted_source"]["sha256"],
                    "compiled": True, "kernel_rechecked": True, "scope_matches": True, "accepted": True,
                    "dependencies": {"axioms": [], "unsafe": [], "unapproved": [], "unauthorized_axioms": [],
                                     "external_constants": [],
                                     "direct_constants": ["Nat.le_refl"], "constants": ["Nat.le_refl"]},
                    "source_declaration": source["declaration"], "source_module": source["module"],
                    "basis_version_ids": [version], "expanded_basis_version_ids": [version],
                    "direct_basis_version_ids": [version],
                    "candidate": artifacts.put({"fixture": operation}),
                    "report": attempt["artifact"], "verification_attempt_id": attempt["id"],
                }

            def record(report):
                return str(store.query("verifier", "SELECT id FROM rm.record_verified_lean(%s,%s,%s)",
                                       anchor["id"], anchor["statement_sha256"], store.json(report))[0]["id"])

            original_evidence = record(prove({**binding, "runtime_sha256": original}, "lake:original"))
            body = prove(binding, "lake:rebuilt")
            for report in ({key: value for key, value in body.items() if key != "lake_validation"},
                           {**body, "lake_validation": {**binding, "runtime_sha256": original}}):
                with self.assertRaisesRegex(ValueError, "must match the successful verification job"):
                    record(report)
            rebuilt_evidence = record(body)
            self.assertNotEqual(rebuilt_evidence, original_evidence)
            self.assertEqual(record(body), rebuilt_evidence)

            store.query("human", "SELECT rm.retire_verification_input(%s,%s,%s)",
                        "environment", runtime_sha, "Only the rebuilt fixture runtime is retired.")
            with self.assertRaisesRegex(ValueError, "observed Lake verification environment requires recheck"):
                record(body)
            with self.assertRaisesRegex(ValueError, "observed Lake verification environment requires recheck"):
                runtime.schedule(store, subject, "verify", payload, 60, "lake:retired", model_calls=0)

        self.assertFalse(case.cli("review", "packet", original_evidence)["verification"]["needs_recheck"])
        self.assertTrue(case.cli("review", "packet", rebuilt_evidence)["verification"]["needs_recheck"])
