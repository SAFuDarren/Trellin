"""Exercise consolidated intake and literature provenance against PostgreSQL."""

from contextlib import contextmanager
from datetime import date
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from research_multiplier.workflows import goal_suggestions, intake, literature, literature_vectors, research_review
from research_multiplier.workflows import target_clarifications, target_draft
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.model_adapters import ModelRun
from research_multiplier.models.model_errors import ModelOutputError


DSN = os.environ.get("RM_TEST_DSN")
BASIS = {"kind": "hypothesis", "natural": "The example uses True.", "formal": {"lean": {"proposition": "True"}}}


def notes(items=None, **fields):
    return json.dumps({"title": "Research intake", "domain": "lean-core", "objective": "Clarify a local question",
                       "sensitivity": "public", **({"items": items} if items is not None else {}), **fields}).encode()


class IntakeTests(unittest.TestCase):
    def test_model_reads_only_explicit_public_notes_and_preserves_exact_quotes(self):
        proposed = {"kind": "hypothesis", "natural": "Invented", "formal": None}
        with patch.object(intake.ModelConfig, "from_env", return_value=object()), \
                patch.object(intake, "complete_structured", return_value=ModelRun({"items": [proposed]}, {}, {})) as model:
            with self.assertRaisesRegex(ValueError, "public"):
                intake._extract(intake._read_notes(notes(notes="The example uses True.", sensitivity="restricted")))
            model.assert_not_called()
            with self.assertRaises(ModelOutputError):
                intake._extract(intake._read_notes(notes(notes="The example uses True.")))
            self.assertEqual(model.call_args.args[3], {"domain": "lean-core", "objective": "Clarify a local question",
                                                       "notes": "The example uses True."})


@unittest.skipUnless(DSN, "set RM_TEST_DSN to a disposable PostgreSQL database")
class ResearchIntakeDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.types.json import Jsonb
        from research_multiplier.storage.migrate import upgrade

        upgrade(DSN)
        cls.connect = staticmethod(psycopg.connect)
        cls.errors = psycopg.errors
        cls.json = staticmethod(Jsonb)

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        env = patch.dict(os.environ, {"RM_ARTIFACT_ROOT": str(self.work / "objects")})
        env.start()
        self.addCleanup(env.stop)

    @contextmanager
    def as_role(self, role="rm_human"):
        from psycopg import sql
        from psycopg.rows import dict_row

        with self.connect(DSN, autocommit=True, row_factory=dict_row) as db:
            db.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role)))

            class TestStore:
                json = self.json

                def query(self, role, query, *params):
                    assert role == "human"
                    return db.execute(query, params).fetchall()

                def transaction(self, role, action):
                    assert role == "human"
                    with db.transaction():
                        return action()

            yield db, TestStore()

    def test_only_outstanding_items_need_a_new_decision_and_old_versions_are_reused(self):
        with self.as_role() as (db, store):
            version = db.execute("SELECT * FROM rm.propose_formal_item(%s,%s)",
                                 ("published-intake-example", self.json(BASIS))).fetchone()
            db.execute("SELECT rm.approve_formal_item(%s,%s,'reviewer','Already reviewed')",
                       (version["id"], version["version_sha256"]))
            packet = intake.draft(store, notes([BASIS, BASIS, {
                "kind": "hypothesis", "natural": "This example states the reflexive equality of zero.",
            }]))
            self.assertEqual([row["status"] for row in packet["comparison"]], ["reuse", "formalization_required"])
            self.assertEqual(packet["comparison"][0]["match_version_id"], str(version["id"]))
            self.assertEqual(db.execute("SELECT kind FROM rm.research_review_inbox WHERE item_id = %s",
                                        (packet["id"],)).fetchone()["kind"], "confirm_intake")
            with self.assertRaises(self.errors.CheckViolation):
                intake.confirm(store, packet["id"], packet["intake_sha256"], {}, "researcher", "Ready")
            self.assertIsNone(intake.show(store, packet["id"])["goal_id"])
            decisions = {"i2": {"action":"accept", "reason":"Matches the intended equality",
                                "formal":{"lean":{"proposition":"(0 : Nat) = 0"}}}}
            goal = intake.confirm(store, packet["id"], packet["intake_sha256"], decisions, "researcher", "Ready")
            self.assertTrue(goal["basis_current"])
            self.assertIsNotNone(goal["approved_at"])
            self.assertEqual(goal["body"]["basis_version_ids"][0], str(version["id"]))
            self.assertEqual(len(goal["body"]["basis_version_ids"]), 2)
            self.assertEqual(goal["id"], intake.confirm(store, packet["id"], packet["intake_sha256"],
                                                       decisions, "researcher", "Ready")["id"])
            self.assertIsNone(goal["conjecture_id"])
            self.assertIsNone(db.execute("SELECT kind FROM rm.research_review_inbox WHERE item_id = %s",
                                         (packet["id"],)).fetchone())
            with self.assertRaises(self.errors.InvalidParameterValue):
                other = intake.draft(store, notes([BASIS], title="Check unknown decisions"))
                intake.confirm(store, other["id"], other["intake_sha256"],
                               {"i1": {"action": "omit", "reason": "Would change the basis"}},
                               "researcher", "Ready")

    def test_model_match_requires_scope_confirmation_and_a_retired_match_blocks_commit(self):
        with self.as_role() as (db, store):
            version = db.execute("SELECT * FROM rm.propose_formal_item(%s,%s)",
                                 ("scope-intake-example", self.json(BASIS))).fetchone()
            db.execute("SELECT rm.approve_formal_item(%s,%s,'reviewer','Previously reviewed')",
                       (version["id"], version["version_sha256"]))
            run = ModelRun({"items": [BASIS]}, {}, {"text": "fixture transcript"})
            with patch.object(intake.ModelConfig, "from_env", return_value=object()), \
                    patch.object(intake, "complete_structured", return_value=run):
                packet = intake.draft(store, notes(notes="First: The example uses True."), model=True)
            self.assertEqual(packet["comparison"][0]["status"], "confirm_scope")
            self.assertIsNotNone(packet["comparison"][0]["match_version_id"])
            with self.assertRaises(self.errors.CheckViolation):
                intake.confirm(store, packet["id"], packet["intake_sha256"], {}, "researcher", "Ready")
            matched_id = packet["comparison"][0]["match_version_id"]
            db.execute("SELECT rm.retire_formal_item(%s,'Earlier context withdrawn')", (matched_id,))
            with self.assertRaises(self.errors.CheckViolation):
                intake.confirm(store, packet["id"], packet["intake_sha256"], {
                    "i1": {"action": "accept", "reason": "Intended here"},
                }, "researcher", "Ready")
            self.assertIsNone(intake.show(store, packet["id"])["goal_id"])

    def test_target_is_frozen_before_notes_and_never_becomes_a_proof_input(self):
        document = {"title": "Target before the starting basis", "domain": "lean-core", "sensitivity": "public",
                    "statement": "The two propositions both hold.", "formal": {"lean": {"proposition": "True ∧ True"}}}
        raw = json.dumps(document).encode()
        condition = {**BASIS, "natural": "The source explicitly assumes True."}
        circular = {"kind": "hypothesis", "natural": "Use the desired result as a hypothesis.",
                    "formal": document["formal"]}
        with self.as_role() as (db, store):
            preview = intake.preview_target(store, raw)
            checksum = preview["target_sha256"]
            changed = json.dumps({**document, "statement": "A different natural target."}).encode()
            with self.assertRaisesRegex(self.errors.CheckViolation, "reviewed target hash"):
                intake.freeze_target(store, changed, checksum, "researcher", "Reviewed target only")
            frozen = intake.freeze_target(store, raw, checksum, "researcher", "Reviewed target only")
            self.assertIsNone(frozen["claim_id"])
            self.assertIsNone(frozen["goal_id"])
            self.assertEqual(frozen, intake.freeze_target(store, raw, checksum, "researcher", "Reviewed target only"))
            with self.assertRaisesRegex(ValueError, "match the frozen target"):
                intake.draft(store, json.dumps({"items": [], "sensitivity": "restricted"}).encode(),
                             target_id=frozen["id"])

            model_run = ModelRun({"items": [condition]}, {}, {"text": "fixture transcript"})
            proposed = json.dumps({"notes": condition["natural"], "items": [circular]}).encode()
            with patch.object(intake.ModelConfig, "from_env", return_value=object()), \
                    patch.object(intake, "complete_structured", return_value=model_run) as model:
                packet = intake.draft(store, proposed, target_id=frozen["id"], model=True)
                self.assertEqual(model.call_args.args[3]["frozen_target"]["formal"], document["formal"])
                self.assertEqual(model.call_args.args[3]["frozen_target"]["statement"], document["statement"])
            self.assertEqual([item["status"] for item in packet["comparison"]],
                             ["omit_frozen_target", "confirm_scope"])
            self.assertIsNone(intake.show(store, packet["id"])["goal_id"])
            backup_draft = intake.draft(store, b'{"items":[]}', target_id=frozen["id"])

            decisions = {"i1": {"action": "accept", "reason": "Attempt to add the target."},
                         "i2": {"action": "accept", "reason": "This condition is in the original notes."}}
            with self.assertRaisesRegex(self.errors.CheckViolation, "cannot be used as a starting"):
                intake.confirm(store, packet["id"], packet["intake_sha256"], decisions, "researcher", "Checked basis")
            self.assertIsNone(intake.target(store, frozen["id"])["claim_id"])
            self.assertIsNone(intake.show(store, packet["id"])["goal_id"])
            decisions["i1"] = {"action": "omit", "reason": "The target is the conclusion."}
            goal = intake.confirm(store, packet["id"], packet["intake_sha256"], decisions, "researcher", "Checked basis")
            self.assertTrue(goal["proof_ready"])
            self.assertEqual(goal["id"], intake.target(store, frozen["id"])["goal_id"])
            self.assertEqual(goal["claim_id"], intake.target(store, frozen["id"])["claim_id"])
            self.assertEqual(goal["id"], intake.confirm(store, packet["id"], packet["intake_sha256"], decisions,
                                                       "researcher", "Checked basis")["id"])
            self.assertIsNone(db.execute("SELECT kind FROM rm.research_review_inbox WHERE item_id = %s",
                                         (backup_draft["id"],)).fetchone())
            with self.assertRaisesRegex(self.errors.CheckViolation, "confirmed starting basis"):
                intake.confirm(store, backup_draft["id"], backup_draft["intake_sha256"], {},
                               "researcher", "Competing basis")

            contract = db.execute("SELECT contract_snapshot FROM rm.claims WHERE id = %s",
                                  (goal["claim_id"],)).fetchone()["contract_snapshot"]
            self.assertEqual(contract["statement"], document["statement"])
            self.assertEqual(contract["formal_skeleton"]["target"], document["formal"])
            self.assertEqual([item["statement"] for item in contract["hypotheses"]], [condition["natural"]])
            theorem = db.execute("SELECT * FROM rm.propose_formal_item(%s,%s)", (
                "circular-target-attachment", self.json({"kind": "theorem", "natural": "Another possible label",
                    "formal": {"lean": {"declaration": "Some.alias", "proposition": "True ∧ True"}}}),
            )).fetchone()
            db.execute("SELECT rm.approve_formal_item(%s,%s,'researcher','Test attachment')",
                       (theorem["id"], theorem["version_sha256"]))
            with self.assertRaisesRegex(self.errors.CheckViolation, "cannot be attached"):
                db.execute("SELECT rm.attach_formal_item(%s,%s,'Test attachment')", (goal["packet_id"], theorem["id"]))
        with self.as_role("rm_agent") as (agent, _):
            with self.assertRaises(self.errors.InsufficientPrivilege):
                agent.execute("SELECT rm.freeze_research_target(%s,%s,'agent','Unauthorized')",
                              (self.json(document), checksum))

    def test_frozen_target_flags_implicit_premise_without_repeating_a_stated_item(self):
        target = {"title": "Interpret a nonempty family", "domain": "lean-core", "sensitivity": "public",
                  "statement": "A nonempty input family admits at least one item.",
                  "formal": {"lean": {"proposition": "True ∧ True"}}}
        raw = json.dumps(target).encode()
        stated = {**BASIS, "natural": "This input family has well-defined members."}
        possible = {"natural": "There is a member of the indicated type.",
                    "formal": {"lean": {"proposition": "∃ x : Nat, x = x"}},
                    "source": "target", "anchor": "nonempty input family",
                    "question": "Does nonempty refer to the type Nat or to a particular set?"}
        duplicate = {**possible, "natural": stated["natural"], "formal": stated["formal"]}
        with self.as_role() as (db, store):
            preview = intake.preview_target(store, raw)
            frozen = intake.freeze_target(store, raw, preview["target_sha256"], "researcher", "Checked target")
            notes = json.dumps({"items": [stated], "notes": ""}).encode()
            run = ModelRun({"items": [], "implicit_hypotheses": [possible, duplicate]}, {}, {})
            with patch.object(intake.ModelConfig, "from_env", return_value=object()), \
                    patch.object(intake, "complete_structured", return_value=run) as model:
                packet = intake.draft(store, notes, target_id=frozen["id"], model=True)
                self.assertEqual(model.call_args.args[3]["frozen_target"]["statement"], target["statement"])
                model.return_value = ModelRun(
                    {"items": [], "implicit_hypotheses": [{**possible, "anchor": "an invented quote"}]}, {}, {},
                )
                with self.assertRaisesRegex(ModelOutputError, "quote its original source exactly"):
                    intake.draft(store, notes, target_id=frozen["id"], model=True)

            items = packet["body"]["items"]
            self.assertEqual([item["natural"] for item in items], [stated["natural"], possible["natural"]])
            self.assertEqual([row["status"] for row in packet["comparison"]], ["confirm_meaning", "clarify"])
            self.assertIn(possible["anchor"], items[1]["question"])
            decisions = {"i1": {"action": "accept", "reason": "Stated explicitly in the input."}}
            with self.assertRaisesRegex(self.errors.CheckViolation, "item i2 needs an explicit decision"):
                intake.confirm(store, packet["id"], packet["intake_sha256"], decisions, "researcher", "Check scope")
            self.assertIsNone(intake.target(store, frozen["id"])["claim_id"])
            decisions["i2"] = {"action": "omit", "reason": "No additional premise about Nat was intended."}
            goal = intake.confirm(store, packet["id"], packet["intake_sha256"], decisions, "researcher", "Check scope")
            contract = db.execute("SELECT contract_snapshot FROM rm.claims WHERE id = %s",
                                  (goal["claim_id"],)).fetchone()["contract_snapshot"]
            self.assertEqual([item["statement"] for item in contract["hypotheses"]], [stated["natural"]])

    def test_discussion_formalization_keeps_exact_provenance_and_waits_for_selection(self):
        with self.as_role() as (db, store):
            premise = {**BASIS, "natural": "All allowed inputs satisfy this initial true predicate."}
            packet = intake.draft(store, notes([premise], title="Review an archived discussion candidate"))
            frame = intake.confirm(store, packet["id"], packet["intake_sha256"], {
                "i1": {"action": "accept", "reason": "The initial scope states this explicitly."},
            }, "researcher", "Scope reviewed")
            version_ids = frame["body"]["basis_version_ids"]
            proposals = [
                {"statement": "An equality holds at an unspecified type.", "reason": "Choose a carrier.",
                 "basis_version_ids": []},
                {"statement": "Both copies of the allowed predicate hold.", "reason": "Use the approved predicate.",
                 "basis_version_ids": version_ids},
            ]
            discussed = ModelRun({"proposals": proposals}, {"model_id": "fixture-discuss"}, {"messages": []})
            draft = ModelRun({"formal": {"lean": {"proposition": "True ∧ True"}}},
                             {"model_id": "fixture-formalize"}, {"messages": []})
            with patch.object(goal_suggestions.ModelConfig, "from_env", return_value=object()), \
                    patch.object(goal_suggestions, "complete_structured", return_value=discussed), \
                    patch.object(target_draft, "complete_structured", return_value=draft) as model:
                discussion = goal_suggestions.discuss(store, frame["id"], {"intuition": "Try a repeated predicate."})
                checksum = discussion["record"]["sha256"]
                with self.assertRaisesRegex(ValueError, "numbered candidate"):
                    goal_suggestions.formalize_discussion(store, frame["id"], checksum, 0)
                model.assert_not_called()
                other_report = Artifacts(self.work / "objects", workspace=False).put({
                    "goal_id": str(frame["id"]), "frame_sha256": "0" * 64, "proposals": proposals,
                })
                with self.assertRaisesRegex(ValueError, "does not match this goal"):
                    goal_suggestions.formalize_discussion(store, frame["id"], other_report["sha256"], 2)
                model.assert_not_called()

                model.return_value = ModelRun({"questions": ["What is the carrier type?"]}, {}, {})
                unresolved = goal_suggestions.formalize_discussion(store, frame["id"], checksum, 1)
                self.assertIsNone(unresolved["conjecture"])
                self.assertEqual(unresolved["target_review"]["questions"], ["What is the carrier type?"])
                pending = unresolved["clarification"]["id"]
                self.assertEqual(db.execute("SELECT kind FROM rm.research_actionable_review_inbox WHERE item_id=%s",
                                            (pending,)).fetchone()["kind"], "clarify_formalization")
                answered = target_clarifications.answer(store, pending, ["The carrier is Nat."],
                                                        "Researcher", "Clarified the intended claim.")
                self.assertEqual(answered["status"], "answered")
                model.return_value = ModelRun({"formal": {"lean": {"proposition": "(0 : Nat) = 0"}}}, {}, {})
                continued = target_clarifications.continue_formalization(store, pending, confirm_local=False)
                self.assertEqual(continued["conjecture"]["body"]["provenance"]["clarification_id"], str(pending))
                self.assertEqual(continued["conjecture"]["body"]["statement"], proposals[0]["statement"])
                self.assertEqual(model.call_args.args[3]["interpretation_answers"], [
                    {"question": "What is the carrier type?", "answer": "The carrier is Nat."},
                ])

                model.return_value = draft
                reviewed = goal_suggestions.formalize_discussion(store, frame["id"], checksum, 2)
                request = model.call_args.args[3]
                self.assertEqual(request["statement"], proposals[1]["statement"])
                self.assertEqual(request["reviewed_context"]["basis"][0]["version_id"], version_ids[0])

            candidate = reviewed["conjecture"]
            self.assertEqual(candidate["body"]["statement"], proposals[1]["statement"])
            provenance = candidate["body"]["provenance"]
            self.assertEqual(provenance["discussion"], discussion["record"])
            self.assertEqual(provenance["candidate_index"], 2)
            objects = Artifacts(self.work / "objects", workspace=False)
            self.assertEqual(objects.read_json(provenance["target_draft"])["target"]["formal"], draft.output["formal"])
            self.assertIsNone(db.execute("SELECT claim_id FROM rm.research_goal_status WHERE id = %s",
                                         (frame["id"],)).fetchone()["claim_id"])
            select = "SELECT * FROM rm.select_research_conjecture(%s,%s,'researcher','Confirmed exact Lean statement')"
            with self.assertRaisesRegex(self.errors.CheckViolation, "reviewed conjecture hash"):
                db.execute(select, (candidate["id"], "0" * 64))
            selection = db.execute(select, (candidate["id"], candidate["conjecture_sha256"])).fetchone()
            contract = db.execute("SELECT contract_snapshot FROM rm.claims WHERE id = %s",
                                  (selection["claim_id"],)).fetchone()["contract_snapshot"]
            self.assertEqual(contract["formal_skeleton"]["basis_version_ids"], version_ids)
            self.assertEqual(contract["formal_skeleton"]["target"], draft.output["formal"])
            with self.assertRaisesRegex(ValueError, "before selection"):
                goal_suggestions.formalize_discussion(store, frame["id"], checksum, 2)

    def test_reusing_restricted_intake_in_public_frame_requires_explicit_release(self):
        premise = {**BASIS, "natural": "An unpublished assumption from a restricted intake."}
        with self.as_role() as (db, store):
            private = intake.draft(store, notes([premise], sensitivity="restricted"))
            accepted = intake.confirm(store, private["id"], private["intake_sha256"],
                                     {"i1": {"action": "accept", "reason": "Reviewed privately"}},
                                     "researcher", "Study only")
            version_id, = accepted["body"]["basis_version_ids"]
            public = intake.draft(store, notes([premise], title="Public proposal only"))
            self.assertEqual(public["comparison"][0]["match_version_id"], version_id)
            intake.confirm(store, public["id"], public["intake_sha256"], {}, "researcher", "Exact local reuse")
            versions = self.json([version_id])
            gate = "SELECT rm.require_public_model_basis(%s)"
            with self.assertRaisesRegex(self.errors.CheckViolation, "restricted or unclassified"):
                db.execute(gate, (versions,))
            checksum = db.execute("SELECT version_sha256 FROM rm.formal_item_status WHERE id = %s",
                                  (version_id,)).fetchone()["version_sha256"]
            with self.assertRaises(self.errors.CheckViolation):
                db.execute("SELECT rm.release_formal_item(%s,%s,'researcher','Authorized release')", (version_id, "0" * 64))
            db.execute("SELECT rm.release_formal_item(%s,%s,'researcher','Authorized publication of this statement')",
                       (version_id, checksum))
            db.execute(gate, (versions,))

    def _claim(self, db, sensitivity):
        frame = db.execute("SELECT * FROM rm.create_research_goal(%s)", (self.json({
            "title": "A literature subject", "domain": "lean-core", "objective": "Find related work",
            "sensitivity": sensitivity, "basis_version_ids": [],
        }),)).fetchone()
        db.execute("SELECT rm.confirm_research_goal(%s,%s,'researcher','Scope reviewed')",
                   (frame["id"], frame["frame_sha256"]))
        conjecture = db.execute("SELECT * FROM rm.propose_research_conjecture(%s,%s)", (frame["id"], self.json({
            "statement": "A scoped claim", "formal": {"lean": {"proposition": "True"}}, "provenance": {"kind": "human"},
        }))).fetchone()
        return db.execute("SELECT claim_id FROM rm.select_research_conjecture(%s,%s,'researcher','Exact goal')",
                          (conjecture["id"], conjecture["conjecture_sha256"])).fetchone()["claim_id"]

    def test_search_records_query_hit_response_and_links_exact_original_only(self):
        reply = json.dumps({"status": "ok", "message": {"total-results": 1, "items": [
            {"DOI": "10.1234/Example", "title": ["An example original"], "published": {"date-parts": [[2025, 2]]}},
        ]}}).encode()
        with self.as_role() as (db, store):
            restricted = self._claim(db, "restricted")
            claim = self._claim(db, "public")
            calls = []

            def download(url):
                calls.append(url)
                return reply

            with self.assertRaisesRegex(ValueError, "frozen public"):
                literature.search(store, restricted, "private topic", download=download)
            self.assertEqual(calls, [])
            result = literature.search(store, claim, "mathematical term", since=date(2025, 1, 1), download=download)
            self.assertEqual(len(calls), 1)
            self.assertIn("query.bibliographic=mathematical+term", calls[0])
            self.assertEqual(result["search"]["body"]["parameters"]["filter"], "from-pub-date:2025-01-01")
            self.assertEqual(result["hits"][0]["body"]["published"], [2025, 2])
            self.assertEqual(Artifacts(self.work / "objects", workspace=False).read(result["search"]["body"]["response"]), reply)
            self.assertEqual(result["hits"][0]["originals"], [])
            pdf = self.work / "source.pdf"
            pdf.write_bytes(b"%PDF-1.5\nexample\n")
            meta = {"title": "Example original", "locator": "Theorem 2", "excerpt": "A related claim", "convention_map": {}}
            wrong = research_review.archive_source(store, claim, "doi:10.1234/other", meta, pdf)
            search_id = result["search"]["id"]
            with self.assertRaises(self.errors.CheckViolation):
                literature.link_source(store, search_id, 1, wrong["id"])
            source = research_review.archive_source(store, claim, "doi:10.1234/example", meta, pdf)
            literature.link_source(store, search_id, 1, source["id"])
            linked = literature.show(store, search_id)["hits"][0]["originals"][0]
            self.assertEqual(linked["id"], str(source["id"]))
            self.assertIsNone(linked["confirmed_at"])
            self.assertEqual(literature.find(store, "doi:10.1234/example")[0]["id"], source["id"])
            errored = literature.search(store, claim, "terms", download=lambda url: b"malformed")
            self.assertEqual(errored["search"]["body"]["status"], "provider_error")
            self.assertEqual(errored["hits"], [])
            self.assertEqual([row["hit_count"] for row in literature.history(store, claim)], [0, 1])

        with self.as_role("rm_agent") as (db, _):
            with self.assertRaises(self.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM rm.literature_searches")

    def test_pgvector_search_is_profile_scoped_and_filters_stale_sources(self):
        with self.as_role() as (db, store):
            claim_id = self._claim(db, "public")
            pdf = self.work / "source.pdf"
            pdf.write_bytes(b"%PDF-1.5\nvector fixture\n")
            meta = {"title": "Related source", "locator": "Theorem 1",
                    "excerpt": "An independently stated result.", "convention_map": {}}
            source = research_review.archive_source(store, claim_id, "doi:10.1234/index", meta, pdf)
            with self.assertRaisesRegex(ValueError, "eligible"):
                literature_vectors.document(store, "source", source["id"])
            research_review.confirm_source(store, source["id"], source["source_sha256"], "reviewer", "Exact source")
            original = literature_vectors.document(store, "source", source["id"])
            claim = literature_vectors.document(store, "claim", claim_id)

            def vector(doc, values):
                return {"kind": doc["kind"], "id": str(doc["id"]), "content_sha256": doc["content_sha256"],
                        "vector": values}

            first = {"model": "model-a", "revision": "weights-v1", "dimensions": 2}
            second = {"model": "model-b", "revision": "weights-v1", "dimensions": 2}
            third = {"model": "model-a", "revision": "weights-v2", "dimensions": 3}
            a_vectors = [vector(original, [1, 0]), vector(claim, [0, 1])]
            b_vectors = [vector(original, [-1, 0]), vector(claim, [1, 0])]
            a_id = literature_vectors.import_vectors(store, {**first, "documents": a_vectors})["profile_id"]
            b_id = literature_vectors.import_vectors(store, {**second, "documents": b_vectors})["profile_id"]
            self.assertNotEqual(a_id, b_id)
            self.assertEqual(literature_vectors.import_vectors(store, {**first, "documents": a_vectors})["profile_id"],
                             a_id)
            literature_vectors.import_vectors(store, {**third, "documents": [vector(claim, [0, 0, 1])]})
            self.assertEqual(len(literature_vectors.profiles(store)), 3)

            def search(profile, values, kind=None):
                return literature_vectors.similar(store, {**profile, "vector": values}, kind=kind)

            matches = search(first, [1, 0])
            self.assertEqual([row["document_id"] for row in matches], [source["id"], claim_id])
            self.assertAlmostEqual(matches[0]["similarity"], 1.0)
            self.assertEqual(search(second, [1, 0])[0]["document_id"], claim_id)
            self.assertEqual([row["document_id"] for row in search(third, [0, 0, 1])], [claim_id])
            with self.assertRaises(self.errors.CheckViolation):
                literature_vectors.import_vectors(store, {**first, "documents": b_vectors})
            with self.assertRaises(self.errors.CheckViolation):
                literature_vectors.import_vectors(store, {
                    **first, "revision": "bad-hash",
                    "documents": [vector(claim, [1, 0]), {**a_vectors[0], "content_sha256": "0" * 64}],
                })
            self.assertEqual(len(literature_vectors.profiles(store)), 3)
            with self.assertRaisesRegex(ValueError, "dimensions"):
                search(first, [0, 0, 1])
            db.execute("SELECT rm.retire_verification_input('source',%s,'Withdraw this original')",
                       (source["body"]["archive"]["sha256"],))
            self.assertEqual([row["document_id"] for row in search(first, [1, 0])], [claim_id])
            self.assertEqual(search(second, [1, 0], kind="source"), [])
            with self.assertRaisesRegex(ValueError, "eligible"):
                literature_vectors.document(store, "source", source["id"])
        with self.as_role("rm_agent") as (db, _):
            with self.assertRaises(self.errors.InsufficientPrivilege):
                db.execute("SELECT * FROM rm.search_literature_vectors('model-a','weights-v1',2,'[1,0]')")
