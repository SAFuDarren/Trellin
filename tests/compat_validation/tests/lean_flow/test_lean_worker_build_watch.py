"""Wake checked work on a rebuilt dependency and bind the completion to its claimant."""

from pathlib import Path
from uuid import uuid4
import json
import os
import unittest
from unittest.mock import patch

import test_lake_scope as fixture
import test_lean_flow as database

from research_multiplier.storage.postgres import Store


@unittest.skipUnless(database.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class LeanWorkerBuildWatchTests(unittest.TestCase):
    def test_lake_rebuild_wakes_signed_policy_and_unclaimed_outcome_is_rejected(self):
        case = fixture.LakeScopeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.build()
        with patch.object(database, "PROJECT", case.project):
            # An impossible goal under these frozen premises stays open.
            anchor, scope, _ = case.case.anchor(
                "lake-worker-build-wake", "ScopeBase.scale = 48",
                [{"kind": "hypothesis", "natural": "Only truth is assumed.", "formal": {"lean": {"proposition": "True"}}}],
                module="ScopeProof")
        mapping = Path(case.project.parent) / "lake-routing.json"
        mapping.write_text(json.dumps({anchor["id"]: str(case.project)}), encoding="utf-8")
        policy = case.cli("advance", "propose", "--for-packet", anchor["packet_id"])
        case.cli("advance", "approve", policy["id"], "--hash", policy["policy_sha256"], "--actor", "researcher",
                 "--reason", "Use the frozen scope under the signed policy.")
        with patch.dict(os.environ, case.case.fixture.env), Store() as owner, Store() as outsider:
            item, = owner.query("scheduler", "SELECT * FROM rm.lean_progress_queue(1,%s::uuid[])",
                                [policy["id"]])
            self.assertTrue(owner.query("scheduler", "SELECT rm.lock_lean_progress(%s,%s) AS locked",
                                        policy["id"], item["inputs_sha256"])[0]["locked"])
            with self.assertRaisesRegex(ValueError, "exact claimed run"):
                outsider.query("scheduler", "SELECT rm.finish_lean_progress(%s,%s,%s)", policy["id"],
                               item["inputs_sha256"], outsider.json({"status": "forged"}))
            with self.assertRaisesRegex(ValueError, "exact claimed run"):
                owner.query("scheduler", "SELECT rm.finish_lean_progress(%s,%s,%s)", policy["id"],
                            uuid4().hex * 2, owner.json({"status": "wrong-input"}))
            owner.query("scheduler", "SELECT rm.unlock_lean_progress(%s)", policy["id"])
        work = ("advance", "worker", "--policy", policy["id"], "--lake-map", mapping)
        first = case.cli(*work)
        self.assertEqual(first["errors"], [], first)
        self.assertEqual(first["lean_runs"][0]["status"], "proof_search_blocked", first)
        self.assertEqual(case.cli(*work)["lean_runs"], [])
        case.build(scale=2)
        changed = case.cli(*work)
        self.assertEqual(changed["errors"], [], changed)
        self.assertEqual(changed["lean_runs"][0]["status"], "lake_scope_review_required", changed)
        resume = case.cli("research", "resume", scope["claim_id"])
        self.assertIn("review_lean_scope_change", {item["kind"] for item in resume["decisions"]["items"]})
        case.build()
        restored = case.cli(*work)
        self.assertEqual(restored["lean_runs"][0]["status"], "proof_search_blocked", restored)
        resume = case.cli("research", "resume", scope["claim_id"])
        self.assertNotIn("review_lean_scope_change", {item["kind"] for item in resume["decisions"]["items"]})
