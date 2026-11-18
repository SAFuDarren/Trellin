"""Exercise cross-claim native export, replay admission and transitive liveness."""

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
class NativeReplayAdmissionTests(unittest.TestCase):
    def test_replay_binds_exact_export_and_actual_destination_basis(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        origin, original, _ = case.anchor("SQL portable source", "True ∧ True", [{
            "kind": "theorem", "natural": "Natural numbers are ordered reflexively.",
            "formal": {"lean": {"proposition": "∀ n : Nat, n ≤ n", "declaration": "Nat.le_refl"}},
        }], sensitivity="restricted")
        destination, downstream, _ = case.anchor("SQL portable destination", "True ∧ True", [{
            "kind": "theorem", "natural": "The zero value is bounded by any natural number.",
            "formal": {"lean": {"proposition": "∀ n : Nat, 0 ≤ n", "declaration": "Nat.zero_le"}},
        }], sensitivity="restricted", statement_text="Derive the archived conjunction from this scope.")
        public, _, _ = case.anchor("SQL portable public destination", "True ∨ True", [{
            "kind": "hypothesis", "natural": "Truth is explicitly assumed at the public destination.",
            "formal": {"lean": {"proposition": "True"}},
        }], sensitivity="public")
        origin_item, = [row["id"] for row in case.cli("research", "basis", origin["packet_id"])
                        if row["body"]["kind"] == "theorem"]
        dest_item, = [row["id"] for row in case.cli("research", "basis", destination["packet_id"])
                      if row["body"]["kind"] == "theorem"]

        with patch.dict(os.environ, case.fixture.env), Store() as store:
            artifacts = Artifacts(paths.object_store(), workspace=False)

            def payload_for(anchor, raw, inputs, **mode):
                approved = anchor["body"]
                return {
                    "lean_statement_id": anchor["id"], "statement_sha256": anchor["statement_sha256"],
                    "target": "goal", "checker": approved["checker"],
                    "checker_revision": approved["environment"]["inputs"]["driver"],
                    "verification_environment_sha256": approved["environment"]["sha256"],
                    "submission": raw, "basis_version_ids": inputs, **mode,
                }

            def subject_for(scope, anchor):
                return {"claim_id": scope["claim_id"], "contract_sha256": anchor["contract_sha256"]}

            def result_for(anchor, scope, payload, found, method):
                subject = subject_for(scope, anchor)
                contract = artifacts.put(store.frozen(scope["claim_id"])["contract_bytes"], raw=True)
                # The separate workflow regression independently checks real Lean proofs.
                # This fixture changes receipts directly to test PostgreSQL fences.
                attempt = runtime.execute(store, artifacts, subject, contract, "verify",
                    lambda: ("exact_proof", {"fixture": method}, None), payload=payload,
                    seconds=60, operation_key=f"native-replay-boundary:{method}", model_calls=0)
                expected = anchor["body"]
                return {
                    **{key: val for key, val in payload.items()
                       if key not in {"lean_statement_id", "statement_sha256"}},
                    "theorem": expected["theorem"], "candidate_theorem": "RMTheory.Proof.checked",
                    "statement_type": expected["theorem_type"],
                    "trusted_source_sha256": expected["trusted_source"]["sha256"],
                    "environment_sha256": payload["verification_environment_sha256"],
                    "compiled": True, "kernel_rechecked": True, "scope_matches": True, "accepted": True,
                    "dependencies": {"axioms": [], "unsafe": [], "unapproved": [], "unauthorized_axioms": [],
                                     "direct_constants": found, "constants": found},
                    "candidate": artifacts.put({"fixture": f"candidate:{method}"}),
                    "report": attempt["artifact"], "verification_attempt_id": attempt["id"],
                }

            def record(anchor, body):
                return str(store.query("verifier", "SELECT id FROM rm.record_verified_lean(%s,%s,%s)",
                                       anchor["id"], anchor["statement_sha256"], store.json(body))[0]["id"])

            raw = artifacts.put_bytes(b"isolated portable source database fixture")
            archive = artifacts.put({"fixture": "portable native archive"})
            source = {"mode": "file", "module": "RMSubmitted", "declaration": "source",
                      "verifier_revision": "1" * 64, "candidate_sha256": raw["sha256"],
                      "approved_declarations": [{"version_id": origin_item, "declaration": "Nat.le_refl"}]}
            origin_payload = payload_for(origin, raw, [origin_item], lean_source=source)
            origin_body = result_for(origin, original, origin_payload, ["Nat.le_refl"], "source")
            origin_body.update(source_declaration="source", source_module="RMSubmitted", native_capsule=archive,
                               direct_basis_version_ids=[origin_item], expanded_basis_version_ids=[origin_item])
            source_evidence = record(origin, origin_body)
            binding = {"artifact": archive, "candidate": origin_body["candidate"], "submission": raw,
                       "basis_version_ids": [origin_item], "verifier_revision": "2" * 64}

            def register(value, role="verifier"):
                return store.query(role, "SELECT * FROM rm.register_native_lean_replay(%s,%s)",
                                   source_evidence, store.json(value))[0]

            with self.assertRaisesRegex(ValueError, "permission denied"):
                register(binding, role="scheduler")
            for change in ({"basis_version_ids": []}, {"artifact": artifacts.put({"fixture": "other capsule"})},
                           {"submission": archive}):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, "match the exact live native"):
                    register({**binding, **change})
            self.assertEqual(register(binding)["binding"], binding)
            self.assertEqual(register(binding)["evidence_id"], register(binding)["evidence_id"])
            self.assertNotIn(source_evidence, [row["evidence_id"] for row in case.cli("lean", "theorems")])
            case.cli("review-evidence", source_evidence, "approve", "--actor", "researcher",
                     "--reason", "Review the source receipt at its exact scope.")
            case.cli("promote-evidence", source_evidence, "--reason", "Adopt this original theorem.")
            export, = [row for row in case.cli("lean", "theorems") if row["evidence_id"] == source_evidence]
            self.assertEqual(export["body"]["format"], "lean-native-proof")
            self.assertEqual(export["body"]["source"]["replay"], archive)
            self.assertEqual(export["body"]["source"]["basis_version_ids"], [origin_item])
            version = export["id"]
            destination_inputs = sorted([version, dest_item])
            replay = {"version_id": version, "artifact": archive, "verifier_revision": "3" * 64,
                      "approved_declarations": [{"version_id": dest_item, "declaration": "Nat.zero_le"}]}
            raw_replay = artifacts.put({"fixture": "replay request"})
            payload = payload_for(destination, raw_replay, destination_inputs, native_replay=replay)
            subject = subject_for(downstream, destination)
            with self.assertRaisesRegex(ValueError, "unapproved or stale item version"):
                runtime.schedule(store, subject, "verify", payload, 60, "native-replay:not-linked", model_calls=0)
            with self.assertRaisesRegex(ValueError, "compatible reviewed Lean scope"):
                store.query("verifier", "SELECT * FROM rm.admit_lean_core_theorem(%s,%s)", public["packet_id"], version)
            with self.assertRaisesRegex(ValueError, "compatible reviewed Lean scope"):
                store.query("verifier", "SELECT * FROM rm.admit_lean_core_theorem(%s,%s)", origin["packet_id"], version)
            store.query("verifier", "SELECT * FROM rm.admit_lean_core_theorem(%s,%s)", destination["packet_id"], version)
            with self.assertRaisesRegex(ValueError, "requires independent native replay"):
                runtime.schedule(store, subject, "verify",
                                 {key: value for key, value in payload.items() if key != "native_replay"}, 60,
                                 "native-replay:untyped", model_calls=0)
            with self.assertRaisesRegex(ValueError, "current imported theorem and its exact archive"):
                runtime.schedule(store, subject, "verify",
                                 {**payload, "native_replay": {**replay, "artifact": raw_replay}}, 60,
                                 "native-replay:tampered-archive", model_calls=0)
            with self.assertRaisesRegex(ValueError, "observed verification inputs"):
                runtime.schedule(store, subject, "verify", payload, 60, "native-replay:model", model_calls=1)
            body = result_for(destination, downstream, payload, ["Nat.zero_le"], "destination")
            for wrong in ([version], [dest_item], []):
                with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "actual checked declarations"):
                    record(destination, {**body, "basis_version_ids": wrong})
            with self.assertRaisesRegex(ValueError, "exact imported theorem and successful check"):
                record(destination, {**body, "native_replay": {**replay, "artifact": raw_replay}})
            result = record(destination, body)
            self.assertEqual(result, record(destination, body))
            case.cli("review-evidence", result, "approve", "--actor", "researcher",
                     "--reason", "Review the destination result.")
            case.cli("promote-evidence", result, "--reason", "Adopt this derived conclusion.")
            self.assertFalse(case.cli("review", "packet", result)["verification"]["needs_recheck"])
            case.cli("research", "retire-item", origin_item, "--reason", "Withdraw the original relied-upon fact.")
            retired = case.cli("review", "packet", result)
            self.assertTrue(retired["verification"]["needs_recheck"])
            self.assertEqual(retired["claim_status"], "needs_recheck")
            self.assertNotIn(version, [row["id"] for row in case.cli("lean", "theorems")])
