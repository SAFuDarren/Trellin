"""Verify evidence review, archived originals, and novelty gates on PostgreSQL."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_lean_db as lean_fixtures
import test_production as production_fixture

from research_multiplier.workflows import literature, research_review
from research_multiplier.policy import review_disclosure
from research_multiplier.protocol.serialization import fingerprint


DSN = os.environ.get("RM_TEST_DSN")


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ResearchReviewTests(unittest.TestCase):
    def test_review_inbox_and_scoped_literature_clearance(self):
        import psycopg
        from psycopg import errors
        from psycopg.rows import dict_row
        from psycopg.types.json import Jsonb

        lean_fixtures.LeanDbTests.setUpClass()
        fixture = lean_fixtures.LeanDbTests()
        with fixture.as_role("rm_human") as human:
            packet_id, claim_id = fixture.packet(human, approved=True)
            statement = fixture.propose(human, packet_id)
            fixture.confirm(human, statement)
        with fixture.as_role("rm_verifier") as verifier:
            check_id = fixture.record(verifier, statement)
            evidence_id = verifier.execute("SELECT id FROM rm.issue_lean_evidence(%s)", (check_id,)).fetchone()[0]
        with fixture.as_role("rm_agent") as agent:
            with self.assertRaises(errors.InsufficientPrivilege), agent.transaction():
                agent.execute("SELECT * FROM rm.research_review_inbox")
            with self.assertRaises(errors.InsufficientPrivilege), agent.transaction():
                agent.execute("SELECT * FROM rm.register_literature_source(%s,%s)", (claim_id, Jsonb({})))

        with TemporaryDirectory() as directory, patch.dict(os.environ, {"RM_ARTIFACT_ROOT": str(Path(directory) / "objects")}):
            original = Path(directory) / "original.pdf"
            content = b"%PDF-1.5\nexample\n"
            original.write_bytes(content)
            meta = {"title": "Original terms", "locator": "Theorem 1", "excerpt": "A related case.",
                    "convention_map": {"value": "net source outflow"}}
            with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as db:
                db.execute("SET SESSION AUTHORIZATION rm_human")

                class ReviewStore:
                    json = staticmethod(Jsonb)

                    def query(self, role, query, *params):
                        assert role == "human"
                        return db.execute(query, params).fetchall()

                store = ReviewStore()

                contract = db.execute("SELECT contract_snapshot FROM rm.claims WHERE id = %s", (claim_id,)).fetchone()
                duplicate = contract["contract_snapshot"]
                duplicate_id, changed_id = uuid4(), uuid4()
                duplicate.update(claim_id=str(duplicate_id), title="Another entry for the same maximum flow scope")
                db.execute("SELECT rm.create_claim(%s)", (Jsonb(duplicate),))
                db.execute("SELECT rm.freeze_claim(%s,'researcher','Test the same scope')", (duplicate_id,))
                duplicate["claim_id"] = str(changed_id)
                duplicate["hypotheses"][1]["statement"] = duplicate["hypotheses"][1]["statement"].replace("real", "integer")
                db.execute("SELECT rm.create_claim(%s)", (Jsonb(duplicate),))
                db.execute("SELECT rm.freeze_claim(%s,'researcher','Test an altered scope')", (changed_id,))
                matches = {row["id"] for row in literature.duplicates(store, claim_id, limit=100)["matches"]}
                self.assertIn(str(duplicate_id), matches)
                self.assertNotIn(str(changed_id), matches)

                def novelty(assessment, sources, evidence=None, searches=()):
                    report = Jsonb({"search_scope": "PRIVATE_QUERY_FOR_TEST",
                                    "comparison": "PRIVATE_COMPARISON_FOR_TEST",
                                    "search_ids": [str(identifier) for identifier in searches]})
                    db.execute("SELECT rm.record_novelty_review(%s,%s,%s,%s,%s,'researcher','Reviewed')",
                               (claim_id, evidence, assessment, sources, report))

                source = research_review.archive_source(store, claim_id, "doi:10.1234/Original", meta, original)
                self.assertEqual(source["body"]["identifier"], "doi:10.1234/original")
                inbox = db.execute("SELECT kind FROM rm.research_review_inbox WHERE claim_id=%s", (claim_id,)).fetchall()
                self.assertEqual({row["kind"] for row in inbox}, {"confirm_literature_source", "review_evidence"})
                packet = research_review.evidence_packet(store, evidence_id, directory)
                self.assertEqual(packet["contract_comparison"]["scope_status"], "exact")
                self.assertEqual(packet["verification"]["grade"], "lean")
                self.assertIn("axioms", packet["key_steps"])

                novelty("not_found", [])
                def status():
                    return db.execute("SELECT status FROM rm.novelty_review_status WHERE claim_id=%s",
                                      (claim_id,)).fetchone()["status"]
                self.assertEqual(status(), "possibly_known")
                with self.assertRaises(errors.CheckViolation):
                    novelty("known", [source["id"]])
                with self.assertRaisesRegex(ValueError, "version hash"):
                    research_review.confirm_source(store, source["id"], "0"*64, "researcher", "Reviewed")
                stored = Path(directory) / "objects" / source["body"]["archive"]["sha256"]
                stored.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    research_review.confirm_source(store, source["id"], source["source_sha256"], "researcher", "Reviewed")
                stored.write_bytes(content)
                research_review.confirm_source(store, source["id"], source["source_sha256"], "researcher", "Reviewed")
                local_log = Path(directory) / "search-log.json"
                local_log.write_text(json.dumps({"searched_in": "PRIVATE_CATALOG_FOR_TEST",
                    "query": "PRIVATE_QUERY_FOR_TEST",
                    "identifiers": ["doi:10.1234/original", "arXiv:2401.01234v1"]}), encoding="utf-8")
                saved = literature.import_search(store, claim_id, local_log)
                search_id = saved["search"]["id"]
                self.assertEqual(saved["hits"][1]["body"]["identifier"], "arXiv:2401.01234v1")
                literature.verify_search_archive(store, search_id)
                receipt = Path(directory) / "objects" / saved["search"]["body"]["response"]["sha256"]
                original_receipt = receipt.read_bytes()
                receipt.write_bytes(b"replaced")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    literature.verify_search_archive(store, search_id)
                receipt.write_bytes(original_receipt)
                with self.assertRaises(errors.CheckViolation):
                    novelty("distinct", [source["id"]], evidence_id, [search_id])
                db.execute("SELECT rm.record_research_understanding(%s,'researcher','PRIVATE_PROOF_FOR_TEST','Exact scope')",
                           (evidence_id,))
                db.execute("SELECT rm.review_evidence(%s,'approve','researcher','Verified scope')", (evidence_id,))
                with self.assertRaisesRegex(errors.CheckViolation, "requires completed searches"):
                    novelty("distinct", [source["id"]], evidence_id)
                with self.assertRaisesRegex(errors.CheckViolation, "each selected search"):
                    novelty("distinct", [source["id"]], evidence_id, [uuid4()])
                with self.assertRaisesRegex(errors.InvalidParameterValue, "unique canonical UUIDs"):
                    novelty("distinct", [source["id"]], evidence_id, [search_id, search_id])
                failed_search = literature.search(store, claim_id, "Fixture query", download=lambda _url: b"invalid")
                with self.assertRaisesRegex(errors.CheckViolation, "each selected search"):
                    novelty("distinct", [source["id"]], evidence_id, [failed_search["search"]["id"]])
                with self.assertRaisesRegex(errors.CheckViolation, "link every compared original"):
                    novelty("distinct", [source["id"]], evidence_id, [search_id])
                literature.link_source(store, search_id, 1, source["id"])
                novelty("distinct", [source["id"]], evidence_id, [search_id])
                self.assertEqual(status(), "human_cleared")
                disclosure = review_disclosure.export(store, evidence_id)
                self.assertEqual(disclosure, review_disclosure.export(store, evidence_id))
                self.assertEqual(disclosure["sha256"], fingerprint({k: v for k, v in disclosure.items() if k != "sha256"}))
                self.assertTrue(disclosure["ai_use"]["human_approval"])
                self.assertEqual(disclosure["provenance"]["literature"]["originals"][0]["linked_hits"],
                                 [{"search_id": str(search_id), "rank": 1}])
                serialized = json.dumps(disclosure)
                self.assertNotIn("PRIVATE_", serialized)
                self.assertNotIn(meta["title"], serialized)
                self.assertNotIn(meta["locator"], serialized)
                self.assertNotIn(directory, serialized)
                db.execute("SELECT rm.retire_verification_input('source',%s,'Search withdrawn')",
                           (saved["search"]["body"]["response"]["sha256"],))
                self.assertEqual(status(), "needs_review")
                self.assertEqual(review_disclosure.export(store, evidence_id)["provenance"]["literature"]["status"],
                                 "needs_review")


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class DisclosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        production_fixture.ProductionTests.setUpClass()
        cls.addClassCleanup(production_fixture.ProductionTests.doClassCleanups)

    def test_disclosure_counts_only_linked_fixed_model_attempts(self):
        fixture = production_fixture.ProductionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with fixture.models():
            run = fixture.start()
            evidence = fixture.record(run)["evidence_id"]
            path = fixture.workspace / "exports" / "disclosure.json"
            exported = fixture.cli("review", "disclosure", evidence, "--output", path)
            document = json.loads(exported.stdout)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)
            self.assertEqual(document["ai_use"]["model_attempts_by_role"], {"audit": 1, "prove": 1})
            self.assertFalse(document["ai_use"]["human_approval"])
            self.assertEqual(len(fixture.calls), 2)
            self.assertEqual({attempt["role"] for attempt in document["provenance"]["attempts"]},
                             {"audit", "prove", "verify"})
            fixture.cli("review", "disclosure", evidence, "--output", path, expect=2)
            for private in ("feasible and has value", "fixed_proof_family", "test-prover", str(fixture.root),
                            "127.0.0.1", "Public flow fixture", "transcript"):
                self.assertNotIn(private, exported.stdout)
