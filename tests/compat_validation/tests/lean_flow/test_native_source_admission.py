"""Exercise native-method, exact basis and checker-liveness admission fences."""

import os
import unittest
from unittest.mock import patch

import test_final_local_lemmas as fixtures
import test_lean_flow as database

from research_multiplier.storage import paths
from research_multiplier.workflows import runtime, workbench_sections
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class NativeSourceAdmissionTests(unittest.TestCase):
    def test_receipt_binds_native_method_and_all_audited_mapped_declarations(self):
        case = fixtures.FinalLocalLemmaTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        anchor, scope, _ = case.anchor("native-source-database-fences", "True ∧ True", [
            {"kind": "theorem", "natural": "A natural number is no greater than itself.",
             "formal": {"lean": {"proposition": "∀ n : Nat, n ≤ n", "declaration": "Nat.le_refl"}}},
            {"kind": "theorem", "natural": "Natural number order is transitive.",
             "formal": {"lean": {"proposition": "∀ a b c : Nat, a ≤ b → b ≤ c → a ≤ c",
                                 "declaration": "Nat.le_trans"}}},
            {"kind": "definition", "natural": "The zero constructor names the distinguished zero of Nat.",
             "formal": {"lean": {"proposition": "(0 : Nat) = 0", "declaration": "Nat.zero"}}},
        ], sensitivity="public")
        basis = case.cli("research", "basis", anchor["packet_id"])
        mapping = {row["body"]["formal"]["lean"]["declaration"]: row["id"] for row in basis
                   if row["body"]["kind"] == "theorem"}
        self.assertEqual(set(mapping), {"Nat.le_refl", "Nat.le_trans"})
        definition, = [row["id"] for row in basis if row["body"]["kind"] == "definition"]
        versions = sorted(mapping.values())
        direct = [mapping["Nat.le_refl"]]
        revision = "7" * 64

        with patch.dict(os.environ, case.fixture.env), Store() as store:
            artifacts = Artifacts(paths.object_store(), workspace=False)
            raw = artifacts.put_bytes(b"native source admission fixture")
            # Exercise the verifier database boundary without treating these
            # synthetic audit rows as a real proof of a research theorem.
            source = {
                "mode": "file", "module": "RMSubmitted", "declaration": "Fixture.result",
                "verifier_revision": revision, "candidate_sha256": raw["sha256"],
                "approved_declarations": [{"version_id": version, "declaration": declaration}
                                          for declaration, version in mapping.items()],
            }
            subject = {"claim_id": scope["claim_id"], "contract_sha256": anchor["contract_sha256"]}
            contract = artifacts.put(store.frozen(scope["claim_id"])["contract_bytes"], raw=True)
            expected = anchor["body"]
            environment = expected["environment"]
            payload = {
                "lean_statement_id": anchor["id"], "statement_sha256": anchor["statement_sha256"],
                "target": "goal", "checker": expected["checker"], "checker_revision": environment["inputs"]["driver"],
                "verification_environment_sha256": environment["sha256"], "submission": raw,
                "basis_version_ids": versions, "lean_source": source,
            }
            invalid_mapping = [{**item, "declaration": "Different.Declaration"} if item["version_id"] == direct[0]
                               else item for item in source["approved_declarations"]]
            for change, error in (
                ({"candidate_sha256": "0" * 64}, "exact submission"),
                ({"approved_declarations": invalid_mapping}, "current reviewed basis"),
            ):
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, error):
                    runtime.schedule(store, subject, "verify", {**payload, "lean_source": {**source, **change}}, 60,
                                     f"native-source-rejected:{error}", model_calls=0)

            definition_input = {**source, "approved_declarations": [
                *source["approved_declarations"], {"version_id": definition, "declaration": "Nat.zero"},
            ]}
            with self.assertRaisesRegex(ValueError, "current reviewed basis"):
                runtime.schedule(store, subject, "verify", {
                    **payload, "basis_version_ids": sorted([*versions, definition]), "lean_source": definition_input,
                }, 60, "native-source-rejected:definition", model_calls=0)

            attempt = runtime.execute(store, artifacts, subject, contract, "verify",
                                      lambda: ("exact_proof", {"fixture": "isolated database admission"}, None),
                                      payload=payload, seconds=60, operation_key="native-source-verified", model_calls=0)
            body = {
                **{key: payload[key] for key in ("target", "checker", "checker_revision",
                                                  "verification_environment_sha256", "submission", "lean_source")},
                "theorem": expected["theorem"], "candidate_theorem": "RMTheory.Proof.checked",
                "statement_type": expected["theorem_type"],
                "environment_sha256": environment["sha256"], "trusted_source_sha256": expected["trusted_source"]["sha256"],
                "accepted": True, "compiled": True, "kernel_rechecked": True, "scope_matches": True,
                "dependencies": {
                    "axioms": [], "unsafe": [], "unapproved": [], "unauthorized_axioms": [],
                    "direct_constants": ["Nat.le_refl"], "constants": ["Nat.le_refl", "Nat.le_trans"],
                },
                "source_declaration": source["declaration"], "source_module": source["module"],
                "basis_version_ids": versions, "expanded_basis_version_ids": versions, "direct_basis_version_ids": direct,
                "candidate": artifacts.put({"fixture": "native source candidate"}),
                "report": attempt["artifact"], "verification_attempt_id": attempt["id"],
            }

            def record(report):
                return store.query("verifier", "SELECT id FROM rm.record_verified_lean(%s,%s,%s)",
                                   anchor["id"], anchor["statement_sha256"], store.json(report))[0]["id"]

            invalid = [
                ({key: value for key, value in body.items() if key != "lean_source"}, "native verification job"),
                ({**body, "lean_source": {**source, "mode": "module"}}, "native verification job"),
                ({**body, "source_declaration": "Fixture.other"}, "native verification job"),
                ({key: value for key, value in body.items() if key != "source_module"}, "native verification job"),
                ({**body, "source_module": "OtherModule"}, "native verification job"),
                ({**body, "direct_basis_version_ids": []}, "audited declarations"),
                ({**body, "basis_version_ids": direct, "expanded_basis_version_ids": direct}, "audited declarations"),
            ]
            for report, error in invalid:
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                    record(report)

            evidence = str(record(body))
            pending = next(item for item in workbench_sections.inbox(store)["items"] if str(item["item_id"]) == evidence)
            self.assertEqual(pending["detail"]["declaration"], source["declaration"])
            self.assertEqual(pending["detail"]["target"], "goal")
            actual = store.query("verifier", """
                SELECT u.version_id FROM rm.verification_basis_use u
                JOIN rm.evidence e ON e.lean_check_id = u.check_id
                WHERE e.id = %s AND u.dependency_kind = 'actual' ORDER BY u.version_id
            """, evidence)
            self.assertEqual([str(row["version_id"]) for row in actual], versions)

        case.cli("review-evidence", evidence, "approve", "--actor", "researcher", "--reason", "Check the fixture.")
        case.cli("promote-evidence", evidence, "--reason", "Test the native receipt promotion path.")
        self.assertNotIn(evidence, [item["evidence_id"] for item in case.cli("lean", "theorems")])
        self.assertIn("archived native proof", case.cli("lean", "export-theorem", evidence, expect=2))
        incident = case.cli("trust", "report", case.document("native-checker", {"issue": "database fixture"}),
                            "--kind", "verifier_vulnerability", "--summary", "Test effective native checker retirement.",
                            "--job", attempt["job_id"])
        self.assertEqual(incident["checker_revision"], revision)
        case.cli("trust", "decide", incident["id"], "contain", "--actor", "researcher",
                 "--reason", "Retire the isolated fixture verifier revision.")
        after = case.cli("review", "packet", evidence)
        self.assertEqual(after["claim_status"], "needs_recheck")
        self.assertTrue(after["verification"]["needs_recheck"])
        with patch.dict(os.environ, case.fixture.env), Store() as store:
            with self.assertRaisesRegex(ValueError, "native Lean verification input requires recheck"):
                record(body)
