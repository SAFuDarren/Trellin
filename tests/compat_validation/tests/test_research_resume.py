"""Exercise the researcher-facing resume projection with native PostgreSQL."""

import json
import os
from unittest import TestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

import test_production as fixtures

from research_multiplier.storage.postgres import Store
from research_multiplier.workflows.research_resume import snapshot


@skipUnless(fixtures.DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ResearchResumeTests(TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from sqlalchemy.engine import make_url

        # A project-level view must start with only the claims this test creates.
        database = f"rm_resume_{uuid4().hex}"
        with psycopg.connect(fixtures.DSN, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

        def drop_database():
            with psycopg.connect(fixtures.DSN, autocommit=True) as admin:
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))

        self.addCleanup(drop_database)
        isolated_dsn = make_url(fixtures.DSN).set(database=database).render_as_string(hide_password=False)

        class IsolatedFixture(fixtures.ProductionTests):
            dsn = isolated_dsn

        self.addCleanup(IsolatedFixture.doClassCleanups)
        IsolatedFixture.setUpClass()
        self.fixture = IsolatedFixture()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        env = patch.dict(os.environ, self.fixture.env)
        env.start()
        self.addCleanup(env.stop)
        self.title = f"Resume the reviewed flow scope {uuid4()}"

    def cli(self, *args):
        return json.loads(self.fixture.cli(*args).stdout)

    def resume(self, *args):
        with patch.dict(os.environ, {"RM_VERIFIER_DSN": "", "RM_SCHEDULER_DSN": "", "RM_AGENT_DSN": ""}):
            return self.cli("research", "resume", *args)

    def test_pending_adoption_and_stale_receipt_are_distinct(self):
        draft = self.fixture.draft_formalization(self.title)
        waiting = self.resume(draft["claim_id"])
        self.assertFalse(waiting["scope"]["basis_reviewed"])
        self.assertEqual(waiting["next"]["action"], "confirm_formalization")
        self.assertEqual(waiting["next"]["reference"]["item_id"], draft["id"])
        self.fixture.confirm_formalization(draft)
        verified = self.cli("formalization", "prove", draft["id"], "--out", self.fixture.root)
        claim, evidence = draft["claim_id"], verified["evidence_id"]

        pending = self.resume(claim, "--limit", 1)
        self.assertEqual(pending["title"], self.title)
        self.assertEqual(pending["status"], "open")
        self.assertEqual(pending["next"]["action"], "review_evidence")
        self.assertIsNone(pending["conclusion"])
        self.assertTrue(pending["scope"]["proof_ready"])
        self.assertEqual(len(pending["scope"]["hypotheses"]), 3)
        self.assertTrue(pending["scope"]["basis"]["has_more"])
        self.assertEqual(len(pending["recent_activity"]["items"]), 1)
        self.assertNotIn("details", pending)

        explanation = "A feasible flow of 23 and a cut of capacity 23 meet at the same bound."
        self.cli("review", "understanding", evidence, "--actor", "researcher",
                 "--explanation", explanation, "--lesson", "The cut establishes the upper bound.")
        self.fixture.cli("review-evidence", evidence, "approve", "--actor", "researcher", "--reason", "Review exact scope.")
        approved = self.resume(claim)
        self.assertIsNone(approved["conclusion"])
        self.assertEqual(approved["next"]["action"], "review_promotion")
        self.fixture.cli("promote-evidence", evidence, "--reason", "Adopt the exact receipt.")
        promoted = self.resume(claim, "--details")
        self.assertEqual(promoted["conclusion"]["reference"]["evidence_id"], evidence)
        self.assertEqual(promoted["conclusion"]["status"], "proved")
        self.assertIsNotNone(promoted["details"]["contract_sha256"])

        certificate_report = self.cli("review", "packet", evidence)["verification"]["artifact"]
        self.cli("research", "retire-input", "source", certificate_report["sha256"],
                 "--reason", "The archived verification evidence was withdrawn.")
        stale = self.resume(claim, "--details")
        self.assertEqual(stale["status"], "needs_recheck")
        self.assertEqual(stale["details"]["recorded_status"], "proved")
        self.assertTrue(stale["details"]["prior_decisive_evidence"]["needs_recheck"])
        self.assertIsNone(stale["conclusion"])
        self.assertEqual(stale["next"]["action"], "reopen_for_recheck")

    def test_project_view_preserves_a_frozen_target_before_it_has_a_claim(self):
        body = {"title": self.title, "domain": "lean-core", "sensitivity": "restricted",
                "statement": "Truth holds twice.", "formal": {"lean": {"proposition": "True ∧ True"}}}
        target_file = self.fixture.root / "target.json"
        target_file.write_text(json.dumps(body), encoding="utf-8")
        preview = self.cli("intake", "preview-target", target_file)
        target = self.cli("intake", "freeze-target", target_file, "--hash", preview["target_sha256"],
                          "--actor", "researcher", "--reason", "Read exact target before touching the basis.")
        report = self.resume("--limit", 1)
        pending, = report["targets_waiting_for_basis"]["items"]
        self.assertEqual(pending["reference"]["target_id"], target["id"])
        self.assertEqual(pending["formal"], body["formal"])
        self.assertEqual(pending["next"]["action"], "prepare_basis")
        with Store() as store:
            with self.assertRaisesRegex(ValueError, "between 1 and 20"):
                snapshot(store, limit=21)
            with self.assertRaisesRegex(ValueError, "No research claim"):
                snapshot(store, uuid4())

    def test_an_old_claim_decision_survives_recent_claim_pagination(self):
        self.assertEqual(self.resume("--limit", 1)["decisions"]["items"], [])
        old = self.fixture.draft_formalization(self.title)
        before = self.resume("--limit", 1)
        newer = self.fixture.draft_formalization(f"Newer scope {uuid4()}")
        report = self.resume("--limit", 1)
        self.assertEqual(report["claims"]["items"][0]["reference"]["claim_id"], newer["claim_id"])
        self.assertTrue(report["decisions"]["has_more"])
        self.assertEqual(report["decisions"]["items"], before["decisions"]["items"])
        self.assertEqual(report["decisions"]["items"][0]["reference"]["claim_id"], old["claim_id"])

    def test_target_question_stays_in_resume_until_a_human_answers(self):
        target = self.fixture.workspace / "inputs" / "ambiguous-target.json"
        target.write_text(json.dumps({
            "title": self.title, "domain": "lean-core", "sensitivity": "public",
            "statement": "Show that equality is reflexive on the intended carrier.",
        }), encoding="utf-8")
        answers = target.with_name("interpretation-answers.json")
        answers.write_text('["The intended carrier is Nat."]', encoding="utf-8")
        question = {"questions": ["Which carrier is intended?"]}
        second_question = "Is the equality ordinary equality on that carrier?"
        formal = {"formal": {"lean": {"proposition": "∀ n : Nat, n = n"}}}
        outputs = iter((question, question, {"questions": [*question["questions"], second_question]}, formal))
        with self.fixture.models(proof=lambda: next(outputs)):
            proposal = self.cli("intake", "propose-target", target, "--confirm-public")
            saved = proposal["clarification"]["id"]
            self.assertIsNone(proposal["target"])
            repeated = self.cli("intake", "propose-target", target, "--confirm-public")
            self.assertEqual(repeated["clarification"]["id"], saved)
            self.assertEqual(len(self.fixture.calls), 1)
            project = self.resume("--limit", 20)
            waiting = next(item for item in project["decisions"]["items"] if item["reference"]["item_id"] == saved)
            self.assertEqual(waiting["questions"], question["questions"])
            text = self.fixture.cli("research", "resume", "--format", "text", "--limit", 20).stdout
            self.assertIn(f"research-multiplier research clarify {saved}", text)
            self.fixture.cli("research", "clarify", saved, "--continue", "--confirm-public", expect=2)
            decided = self.cli("research", "clarify", saved, "--answers", answers,
                               "--actor", "Researcher", "--reason", "Clarified the original carrier.")
            self.assertEqual(decided["status"], "answered")
            self.assertEqual(len(self.fixture.calls), 1)
            project = self.resume("--limit", 20)
            self.assertNotIn(saved, {item["reference"]["item_id"] for item in project["decisions"]["items"]})
            self.assertIn(saved, {item["id"] for item in project["answered_formalizations"]["items"]})
            repeated = self.fixture.cli("research", "clarify", saved, "--continue", "--confirm-public", expect=2)
            self.assertIn("repeated previously answered questions", repeated.stderr)
            continued = self.cli("research", "clarify", saved, "--continue", "--confirm-public")
            next_id = continued["clarification"]["id"]
            self.assertEqual(continued["clarification"]["questions"], [second_question])
            replay = self.cli("research", "clarify", saved, "--continue", "--confirm-public")
            self.assertEqual(replay["clarification"]["id"], next_id)
            self.assertEqual(len(self.fixture.calls), 3)
            answers.write_text('["Use ordinary equality."]', encoding="utf-8")
            self.cli("research", "clarify", next_id, "--answers", answers, "--actor", "Researcher",
                     "--reason", "Confirmed the meaning already intended.")
            report = self.cli("research", "clarify", next_id, "--continue", "--confirm-public")
            self.assertEqual(report["target"]["formal"], formal["formal"])
            self.assertEqual(self.fixture.model_inputs[-1]["interpretation_answers"], [
                {"question": question["questions"][0], "answer": "The intended carrier is Nat."},
                {"question": second_question, "answer": "Use ordinary equality."},
            ])
            self.cli("research", "clarify", next_id, "--continue", "--confirm-public")
            self.assertEqual(len(self.fixture.calls), 4)
            self.assertEqual(self.resume("--limit", 20)["answered_formalizations"]["items"], [])
            preview = self.cli("intake", "preview-target", "--from-proposal", report["record"]["sha256"])
            self.assertTrue(preview["target_sha256"])
            self.assertNotIn(self.title, {item["title"] for item in
                                        self.resume("--limit", 20)["targets_waiting_for_basis"]["items"]})
