"""Exercise exact imported-archive and actual-use fences on composed local lemmas."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier import general_lean
from research_multiplier.storage import paths
from research_multiplier.workflows import runtime
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanCompositionAdmissionTests(unittest.TestCase):
    def test_bound_native_archive_and_actual_local_use(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        origin, _, _ = case.anchor("Composed archive origin", "True ∧ True", [{
            "kind": "hypothesis", "natural": "The archive origin was reviewed with truth as a premise.",
            "formal": {"lean": {"proposition": "True"}},
        }], sensitivity="restricted")
        destination, scope, _ = case.anchor("Composed local destination", "True ∨ True", [{
            "kind": "hypothesis", "natural": "The local destination explicitly confirms truth at intake.",
            "formal": {"lean": {"proposition": "True"}},
        }], sensitivity="restricted")

        source = case.fixture.workspace.parent / "CompositionOrigin.lean"
        source.write_text(
            "theorem compositionOrigin (_h : True) : True ∧ True := ⟨True.intro, True.intro⟩\n",
            encoding="utf-8",
        )
        checked = case.cli("lean", "check-source", origin["id"], source, "--declaration", "compositionOrigin")
        case.cli("review-evidence", checked["evidence_id"], "approve", "--actor", "researcher",
                 "--reason", "Review the archived conjunction at its exact scope.")
        case.cli("promote-evidence", checked["evidence_id"], "--reason", "Adopt the original theorem.")
        export, = [row for row in case.cli("lean", "theorems") if row["evidence_id"] == checked["evidence_id"]]
        version = export["id"]
        archive = export["body"]["source"]["replay"]

        with patch.dict(os.environ, case.fixture.env), Store() as store:
            artifacts = Artifacts(paths.object_store(), workspace=False)
            body = destination["body"]
            proposition = "True ∧ True"
            source_ref = artifacts.put(general_lean._type_source(proposition, ["True"]), raw=True)
            candidate = artifacts.put({"fixture": "PostgreSQL composition admission"})
            revision = fingerprint("composed boundary checker")
            composition = {
                "imports": [{"version_id": version, "artifact": archive}],
                "verifier_revision": revision, "approved_declarations": [],
            }
            payload = {
                "target": "local_lemma", "lean_statement_id": destination["id"],
                "anchor_sha256": destination["statement_sha256"], "proposition": proposition,
                "statement_sha256": source_ref["sha256"],
                "trusted_source_sha256": body["trusted_source"]["sha256"],
                "verification_environment_sha256": body["environment"]["sha256"],
                "reviewed_driver_sha256": body["environment"]["inputs"]["driver"], "checker_revision": revision,
                "basis_version_ids": [version], "local_lemma_ids": [],
                "submission": candidate, "checker": {"name": "rm-lean-local-lemma", "version": "1"},
                "lean_composition": composition,
            }
            subject = {"claim_id": scope["claim_id"], "contract_sha256": destination["contract_sha256"]}
            candidates = store.query(
                "verifier", "SELECT * FROM rm.lean_search_native_theorems(%s,false)", destination["packet_id"])
            self.assertIn(version, {str(row["id"]) for row in candidates})
            self.assertEqual(store.query("verifier", "SELECT * FROM rm.lean_search_native_theorems(%s,true)",
                                         destination["packet_id"]), [])
            with self.assertRaisesRegex(ValueError, "unapproved or stale item version"):
                runtime.schedule(store, subject, "verify", payload, 60, "unadmitted-composed-lemma", model_calls=0)
            store.query("verifier", "SELECT * FROM rm.admit_lean_core_theorem(%s,%s)", destination["packet_id"], version)
            with self.assertRaisesRegex(ValueError, "live, imported native theorem versions"):
                invalid = {**composition, "imports": [{"version_id": version, "artifact": candidate}]}
                runtime.schedule(store, subject, "verify", {**payload, "lean_composition": invalid}, 60,
                                 "mismatched-composed-archive", model_calls=0)

            contract = artifacts.put(store.frozen(scope["claim_id"])["contract_bytes"], raw=True)
            # Lean composition is independently exercised by the end-to-end workflow tests.
            attempt = runtime.execute(store, artifacts, subject, contract, "verify",
                lambda: ("exact_proof", {"fixture": "Local composition receipt"}, None), payload=payload,
                operation_key="composed-lemma-boundary", model_calls=0)
            alias = "RMTheory.Proof.basis_native_" + version.replace("-", "")
            dependencies = {
                "axioms": [], "unsafe": [], "unapproved": [], "unauthorized_axioms": [],
                "direct_constants": [alias], "constants": [alias], "native_version_ids": [version],
            }
            report = {
                "compiled": True, "kernel_rechecked": True, "scope_matches": True, "accepted": True,
                "dependencies": dependencies, "basis_version_ids": [version], "local_lemma_ids": [],
                "statement_sha256": source_ref["sha256"], "candidate": candidate,
                "report": attempt["artifact"], "lean_composition": composition,
            }

            def record(value):
                return store.query("verifier", "SELECT * FROM rm.record_local_lean_lemma(%s,%s)",
                                   attempt["id"], store.json(value))[0]

            with self.assertRaisesRegex(ValueError, "basis must follow the audited constants"):
                record({**report, "dependencies": {**dependencies, "native_version_ids": []}})
            with self.assertRaisesRegex(ValueError, "native versions do not match"):
                record({**report, "basis_version_ids": []})
            record_id = record(report)["id"]
            self.assertEqual(record_id, record(report)["id"])

            def status():
                [lemma] = store.query("verifier", "SELECT status FROM rm.local_lean_lemma_status(%s) WHERE id = %s",
                                      destination["id"], record_id)
                return lemma["status"]

            self.assertEqual(status(), "verified")
            case.cli("research", "retire-item", version, "--reason", "Withdraw the archived native theorem.")
            self.assertEqual(status(), "needs_recheck")
