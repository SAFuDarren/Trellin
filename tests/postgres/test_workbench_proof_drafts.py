# SPDX-License-Identifier: Apache-2.0
"""Exercise private proof drafts with native, independent database logins."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from research_multiplier.storage.migrate import upgrade
from tests._native_db import disposable_database, project_logins


pytestmark = pytest.mark.postgres

READ = "SELECT * FROM rm.read_workbench_proof_draft(%s,%s,%s)"
SAVE = "SELECT * FROM rm.save_workbench_proof_draft(%s,%s,%s,%s,%s)"
DISCARD = "SELECT rm.discard_workbench_proof_draft(%s,%s,%s,%s,%s)"


def test_proof_draft_upgrade_concurrency_privacy_and_restore():
    import psycopg

    first_text = "  私有证明草稿\n"
    workspace = uuid4()

    with disposable_database() as dsn:
        upgrade(dsn, "0081")
        with (
            project_logins(dsn) as logins,
            psycopg.connect(dsn, autocommit=True) as owner,
            psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as human,
        ):
            database, = human.execute("SELECT rm.current_database_id()").fetchone()
            human.execute("SELECT rm.bind_research_workspace(%s,%s)", (workspace, database))
            user, = human.execute("SELECT id FROM rm.current_local_user()").fetchone()
            claim, = owner.execute("""
                INSERT INTO rm.claims (kind, origin, domain, title, statement, sensitivity)
                VALUES ('researcher_claim', 'human', 'lean-core', 'Draft scope', 'True', 'restricted')
                RETURNING id
            """).fetchone()
            target = (workspace, claim, f"goal:{claim}")
            guidance_before, = owner.execute("SELECT count(*) FROM rm.workbench_guidance").fetchone()

            upgrade(dsn)
            assert human.execute("SELECT * FROM rm.current_workspace_binding()").fetchone() == (database, workspace)
            assert human.execute(READ, target).fetchall() == []
            start, = human.execute("SELECT rm.workspace_event_cursor(%s)", (workspace,)).fetchone()
            first = human.execute(SAVE, (*target, None, first_text)).fetchone()
            assert first[1] == first_text
            assert human.execute(READ, target).fetchone() == first
            assert human.execute(READ, (workspace, claim, "different-node")).fetchall() == []
            assert human.execute(READ, (workspace, uuid4(), f"goal:{claim}")).fetchall() == []
            assert human.execute(SAVE, (*target, None, "stale creation")).fetchall() == []
            assert human.execute("SELECT data FROM rm.read_workspace_events(%s,%s,100)", (workspace, start)).fetchall() == [
                ({"area": "activity", "claim_id": str(claim)},),
            ]

            with pytest.raises(psycopg.errors.InvalidParameterValue):
                human.execute(SAVE, (*target, first[0], " \n\t "))
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                human.execute(SAVE, (*target, first[0], "界" * 1501))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                human.execute(READ, (uuid4(), *target[1:]))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                owner.execute(READ, target)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                human.execute("SELECT text FROM rm.workbench_proof_drafts")
            for role in ("AGENT", "SCHEDULER", "VERIFIER"):
                with psycopg.connect(logins[f"RM_{role}_DSN"], autocommit=True) as service:
                    for statement, arguments in (
                        ("SELECT text FROM rm.workbench_proof_drafts", ()),
                        (READ, target),
                        (SAVE, (*target, first[0], "forged edit")),
                        (DISCARD, (*target, first[0], None)),
                    ):
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            service.execute(statement, arguments)

            barrier = Barrier(2, timeout=5)

            def edit(text):
                with psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as connection:
                    barrier.wait()
                    return connection.execute(SAVE, (*target, first[0], text)).fetchall()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(edit, ("  first edit\n", "  second edit\n")))
            assert sorted(map(len, results)) == [0, 1]
            winner, = [row for result in results for row in result]
            assert winner[0] != first[0]
            assert human.execute(READ, target).fetchone() == winner
            assert human.execute(DISCARD, (*target, first[0], None)).fetchone() == (False,)
            assert human.execute(DISCARD, (*target, winner[0], winner[1].strip())).fetchone() == (False,)

            # This is the identity change performed after the authorized import
            # of a project archive. The local researcher and draft survive it.
            restored_workspace = uuid4()
            owner.execute("SELECT rm.reidentify_restored_database(%s,%s)", (database, restored_workspace))
            restored_target = (restored_workspace, *target[1:])
            assert human.execute("SELECT id FROM rm.current_local_user()").fetchone() == (user,)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                human.execute(READ, target)
            assert human.execute(READ, restored_target).fetchone() == winner
            assert human.execute(DISCARD, (*restored_target, winner[0], winner[1])).fetchone() == (True,)
            assert human.execute(DISCARD, (*restored_target, winner[0], winner[1])).fetchone() == (False,)
            assert human.execute(READ, restored_target).fetchall() == []
            recreated = human.execute(SAVE, (*restored_target, None, first_text)).fetchone()
            assert recreated[0] not in (first[0], winner[0])
            assert human.execute(SAVE, (*restored_target, winner[0], "stale edit")).fetchall() == []
            assert owner.execute("SELECT count(*) FROM rm.workbench_guidance").fetchone() == (guidance_before,)
