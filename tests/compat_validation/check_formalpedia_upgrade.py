"""Exercise optional Formalpedia migrations in a disposable test database."""

import os
from unittest.mock import patch

import psycopg

from research_multiplier.storage.migrate import upgrade
from research_multiplier.storage.postgres import Store
from research_multiplier.workflows import formalpedia_review
from test_final_local_lemmas import FinalLocalLemmaTests


def decisions(case, claim_id):
    report = case.cli("research", "resume", claim_id)
    return [row for row in report["decisions"]["items"] if row["kind"] == "review_formalpedia_basis"]


case = FinalLocalLemmaTests()
dsn = os.environ["RM_TEST_DSN"]
try:
    with patch("research_multiplier.storage.migrate.upgrade", side_effect=lambda address: upgrade(address, "0066")):
        case.setUp()
    anchor, scope, _ = case.anchor("Preserve existing Formalpedia project", "True ∧ (True ∧ True)", [])
    statement_id, claim_id = anchor["id"], scope["claim_id"]
    candidate = {"snapshot": "a" * 64, "environment": "c5ea003", "theorem": "Sample.pair", "solution_sha256": "b" * 64}
    missing = [{"kind": "premise", "proposition": "False"}]
    plan = case.document("existing-pair", {"format_version": 1, "nodes": [
        {"name": "Pair", "proposition": "True ∧ True", "proof": {"and": [{"trivial": True}, {"trivial": True}]}},
    ]})
    lemma, = case.cli("lean", "derive", statement_id, plan)["lemmas"]
    old_error = case.cli("lean", "formalpedia-sources", statement_id, expect=2)
    assert "Enable PostgreSQL migration 0068" in old_error, old_error
    assert decisions(case, claim_id) == []
    with psycopg.connect(dsn) as database:
        revision, = database.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
        assert revision == "0066"
    print("Ordinary Lean and research resume worked in 0066; new source lookup declined without migration.")
    upgrade(dsn, "0067")
    source = {"author": "Existing source"}
    with patch.dict(os.environ, case.fixture.env), Store() as store:
        for snapshot in ("a" * 64, "f" * 64):
            store.query("verifier", "SELECT rm.record_formalpedia_basis_review(%s,%s,%s)", statement_id,
                        store.json({**candidate, "snapshot": snapshot}), store.json(missing))
        store.query("verifier", "SELECT rm.record_formalpedia_lemma(%s,%s,%s,%s)", statement_id,
                    lemma["id"], store.json(candidate), store.json(source))
        previous, = formalpedia_review.provenance(store, statement_id)
        assert previous["binding"] == "legacy_unbound"
        try:
            formalpedia_review.require_enabled(store)
        except ValueError as error:
            assert "0068" in str(error)
        else:
            raise AssertionError("Replay unexpectedly enabled for 0067")
    previous_notes = decisions(case, claim_id)
    assert len(previous_notes) == 2
    upgrade(dsn)
    next_note, = decisions(case, claim_id)
    assert next_note["reference"]["item_id"] in {item["reference"]["item_id"] for item in previous_notes}
    with patch.dict(os.environ, case.fixture.env), Store() as store:
        assert formalpedia_review.require_enabled(store) == 68
        current, = formalpedia_review.provenance(store, statement_id)
        assert current == previous
        bound = {**candidate, "statement_sha256": "c" * 64, "index_entry_sha256": "d" * 64,
                 "solution_file_sha256": "e" * 64}
        note = formalpedia_review.record(store, statement_id, bound, missing)
        assert str(note) == next_note["reference"]["item_id"]
    with psycopg.connect(dsn) as database:
        current_revision, = database.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
        old_record_count, = database.execute("SELECT count(*) FROM rm.formalpedia_basis_reviews").fetchone()
        assert current_revision == "0068"
        assert old_record_count == 2
    print("0067 sources and proof receipts survived 0068 without changing their source binding or identities.")
    print("Two historical snapshots collapsed to one review and later snapshots reused its id.")
finally:
    case.doCleanups()
