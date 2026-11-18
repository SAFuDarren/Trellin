"""Exercise direct structured comparison and blind proof preparation."""

from copy import deepcopy
import json
import os
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import test_production as production_fixture

from research_multiplier.policy import scope_review
from research_multiplier.storage.postgres import Store


DSN = os.environ.get("RM_TEST_DSN")


class ScopeReviewTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"items": [
            {"id": "goal", "kind": "goal", "formal": {"quantifiers": ["forall", "exists"], "bound": 3}},
            {"id": "H1", "kind": "hypothesis", "formal": {"field": "real"}},
        ]}

    def test_structural_report_exposes_quantifiers_assumptions_and_restricted_type(self):
        observed = deepcopy(self.expected)
        observed["items"][0]["formal"]["quantifiers"].reverse()
        observed["items"][1]["formal"]["field"] = "integer"
        observed["items"].append({"id": "H2", "kind": "hypothesis", "formal": {"predicate": "independent"}})
        report = scope_review.compare(self.expected, observed)
        self.assertEqual(report["status"], "different")
        self.assertEqual({tuple(change["path"]) for change in report["differences"]}, {
            ("H1", "formal", "field"), ("H2",), ("goal", "formal", "quantifiers", 0),
            ("goal", "formal", "quantifiers", 1),
        })
        with self.assertRaisesRegex(ValueError, "unique ID"):
            scope_review.compare(self.expected, {"items": observed["items"] + [observed["items"][0]]})
        reordered = {"items": list(reversed(self.expected["items"]))}
        self.assertEqual(scope_review.compare(self.expected, reordered)["status"], "exact_structure")

    def test_blind_input_excludes_labelled_preamble_and_premise_labels(self):
        proof = "# Theorem\nThe proposed optimum is 93.\n# Assumptions\nH1: Secret label.\n"
        with self.assertRaisesRegex(ValueError, "Isolate the derivation"):
            scope_review._derivation_text(proof)
        text = scope_review._derivation_text(proof + "# Proof\nBy [H1], every feasible x has f(x) ≤ 4.")
        self.assertEqual(text, "By [premise reference], every feasible x has f(x) ≤ 4.")
        with self.assertRaisesRegex(ValueError, "Isolate the derivation"):
            scope_review._derivation_text("# Proof\nActual derivation.\n# Claim\nStated target.")
        self.assertEqual(scope_review._derivation_text("**Proof.** First inference.\nSecond inference."),
                         "First inference.\nSecond inference.")

    def test_extraction_does_not_accept_model_verdicts(self):
        valid = {"assumptions": [], "definitions": [], "limitations": ["Missing proof"], "conclusion": None}
        self.assertEqual(scope_review._validate_extraction(valid), valid)
        with self.assertRaisesRegex(ValueError, "unexpected structure"):
            scope_review._validate_extraction({**valid, "verdict": "verified"})


    def test_lean_scope_review_cues_and_blind_input(self):
        statement = {
            "id": "statement", "packet_id": "packet", "claim_id": "claim", "statement_sha256": "reviewed",
            "approved": False, "current": True, "body": {
                "theorem_type": "∀ n : Nat, n / 2 - 1 ≤ n",
                "scope": {"lean_hypotheses": ["True"]}, "environment": {},
            },
        }
        items = [{"id": "goal", "natural": "An intended division statement."}]
        packet = {"packet_sha256": "packet-digest", "sensitivity": "public", "packet": {"items": items}}
        findings = {"assumptions": ["True"], "definitions": ["Natural-number division"],
                    "conclusion": "The stated natural-number bound.", "limitations": []}
        run = scope_review.model_adapters.ModelRun(findings, {"id": "fixed-auditor"}, {"response": findings})
        store = Mock()
        with TemporaryDirectory() as root, patch.dict(os.environ, {"RM_ARTIFACT_ROOT": root}), \
                patch.object(scope_review.lean_workflow, "_statement", return_value=statement), \
                patch.object(scope_review, "expected_scope", return_value=packet), \
                patch.object(scope_review.model_adapters.ModelConfig, "from_env", return_value="auditor"), \
                patch.object(scope_review.model_adapters, "complete_structured", return_value=run) as completion:
            result = scope_review.lean_statement(store, "statement", backtranslate=True)
        self.assertEqual(result["research_items"], items)
        self.assertEqual(len(result["inspection_cues"]), 2)
        self.assertEqual(completion.call_args.args[3], result["lean"])
        self.assertNotIn("natural", completion.call_args.args[3])
        self.assertEqual(result["backtranslation"]["findings"], findings)
        self.assertEqual(result["backtranslation"]["statement_sha256"], "reviewed")
        store.query.assert_called_once_with("human", "SELECT rm.require_public_model_packet(%s)", "packet")


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ScopeDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        production_fixture.ProductionTests.setUpClass()
        cls.addClassCleanup(production_fixture.ProductionTests.doClassCleanups)

    def test_blind_archive_is_bound_to_the_stored_attempt_and_never_sees_frozen_scope(self):
        fixture = production_fixture.ProductionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        proof = {**production_fixture.PROOF, "proof_markdown":
                 "# Claim\nHidden statement preamble.\n# Proof\nBy H1, witness (value 23) meets the cut."}
        extraction = {"assumptions": ["Flow feasibility"], "definitions": ["Cut capacity"],
                      "limitations": [], "conclusion": "Optimum 23 for this network."}
        with fixture.models(
            proof, lambda: extraction if len(fixture.calls) == 3 else production_fixture.AUDIT,
        ):
            outcome = fixture.cli("v0a", fixture.network_input, "--title", "Frozen private title field",
                                  "--record-freeze-as", "test researcher", "--out", fixture.root / "claims")
            directory = next((fixture.root / "claims").iterdir())
            evidence_id = fixture.record(directory)["evidence_id"]
            args = ("scope", "blind", evidence_id, "--run-directory", directory)
            fixture.cli(*args, expect=2)
            self.assertEqual(len(fixture.calls), 2)
            with patch.dict(os.environ, {"RESEARCH_MULTIPLIER_AUDITOR_FAMILY": "fixed_proof_family"}):
                fixture.cli(*args, "--confirm-public", expect=2)
            self.assertEqual(len(fixture.calls), 2)
            result = json.loads(fixture.cli(*args, "--confirm-public").stdout)
            self.assertIn(str(evidence_id), outcome.stdout)
            self.assertEqual(result["findings"], extraction)
            self.assertEqual(fixture.model_inputs[-1],
                             {"derivation_steps": "By [premise reference], witness (value 23) meets the cut."})
            self.assertEqual(json.loads(fixture.cli("scope", "show", result["id"]).stdout), result)
            listed, = json.loads(fixture.cli("scope", "list", evidence_id).stdout)
            self.assertEqual(listed["report_sha256"], result["artifact"]["sha256"])
            fixture.cli("promote-evidence", evidence_id, "--reason", "scope extraction alone", expect=2)
            with Store() as store:
                proof_record, = store.query("verifier", "SELECT attempt_id, proof_sha256 FROM "
                                            "rm.proof_scope_extractions WHERE id = %s", result["id"])
                statement = "SELECT rm.record_scope_extraction(%s,%s,%s,%s,%s) AS id"
                record = evidence_id, proof_record["attempt_id"], proof_record["proof_sha256"]
                with self.assertRaisesRegex(ValueError, "independent model family"):
                    store.query("verifier", statement, *record, "fixed_proof_family", "3"*64)
                with self.assertRaisesRegex(ValueError, "permission denied"):
                    store.query("agent", statement, *record, "third-family", "3"*64)
