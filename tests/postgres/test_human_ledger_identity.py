# SPDX-License-Identifier: Apache-2.0
"""Exercise remaining human-ledger attribution using separate local logins."""

from uuid import uuid4

import pytest

from research_multiplier.storage.migrate import upgrade
from tests._native_db import disposable_database, disposable_project_logins, project_logins


pytestmark = pytest.mark.postgres

DISPLAY = "Researcher display label"
REASON = "Policy exercised by a local regression."
POLICY = "SELECT * FROM rm.record_checker_monitor_policy(5, 20, %s, %s)"
CLARIFICATION = """
    SELECT rm.record_target_clarification(%s, NULL, NULL, NULL, 'Test clarification',
        'restricted', '["Sample question?"]'::jsonb, %s) AS id
"""
ANSWER = "SELECT rm.answer_target_clarification(%s, '[\"Sample answer\"]'::jsonb, %s, %s)"


def test_upgrade_preserves_old_rows_and_attributes_new_human_writes():
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    with disposable_database() as dsn:
        upgrade(dsn, "0076")
        with (
            project_logins(dsn) as logins,
            psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True, row_factory=dict_row) as human,
            psycopg.connect(logins["RM_VERIFIER_DSN"], autocommit=True, row_factory=dict_row) as verifier,
            psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as admin,
        ):
            workspace = uuid4()
            database = human.execute("SELECT database_id FROM rm.current_workspace_binding()").fetchone()
            human.execute("SELECT rm.bind_research_workspace(%s,%s)", (workspace, database["database_id"]))
            user = human.execute("SELECT id FROM rm.current_local_user()").fetchone()["id"]
            old = human.execute(POLICY, (DISPLAY, REASON)).fetchone()
            answered = human.execute(CLARIFICATION, ("1" * 64, "2" * 64)).fetchone()["id"]
            human.execute(ANSWER, (answered, DISPLAY, REASON))
            waiting = human.execute(CLARIFICATION, ("3" * 64, "4" * 64)).fetchone()["id"]
            start = human.execute("SELECT rm.workspace_event_cursor(%s) AS id", (workspace,)).fetchone()["id"]

            upgrade(dsn)
            row = admin.execute("SELECT * FROM rm.checker_monitor_policies WHERE id = %s", (old["id"],)).fetchone()
            assert row == {**old, "user_id": None}
            assert human.execute("SELECT id FROM rm.current_local_user()").fetchone()["id"] == user

            saved = human.execute(POLICY, (DISPLAY, REASON)).fetchone()
            assert saved["user_id"] == user and saved["actor_ref"] == DISPLAY
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                verifier.execute(POLICY, (DISPLAY, REASON))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                human.execute("UPDATE rm.checker_monitor_policies SET user_id = %s", (uuid4(),))

            human.execute(ANSWER, (waiting, DISPLAY, REASON))
            human.execute("SELECT rm.complete_target_clarification(%s,%s)", (waiting, "5" * 64))
            for identifier, expected in ((answered, None), (waiting, user)):
                decision = admin.execute("""
                    SELECT user_id, actor_ref FROM rm.target_clarifications WHERE id = %s
                """, (identifier,)).fetchone()
                assert decision == {"user_id": expected, "actor_ref": DISPLAY}

            incident = "SELECT * FROM rm.report_verifier_incident('workflow_blocked', %s, %s)"
            digest = "6" * 64
            reference = Jsonb({"uri": f"urn:sha256:{digest}", "sha256": digest})
            assert human.execute(incident, (REASON, reference)).fetchone()["user_id"] == user
            assert verifier.execute(incident, (REASON, reference)).fetchone()["user_id"] is None

            events = human.execute("SELECT * FROM rm.read_workspace_events(%s,%s,100)", (workspace, start))
            assert [(row["type"], row["user_id"], row["data"]) for row in events] == [
                ("workspace.changed", user, {"area": "operations"}),
                ("workspace.changed", user, {"area": "clarification"}),
                ("workspace.changed", user, {"area": "clarification"}),
                ("workspace.changed", user, {"area": "operations"}),
                ("workspace.changed", None, {"area": "operations"}),
            ]


def test_new_human_ledger_requires_a_bound_identity():
    import psycopg

    with disposable_project_logins() as logins, psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as human:
        assert human.execute("SELECT id FROM rm.current_local_user()").fetchone() is None
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="resolve the authenticated local researcher"):
            human.execute(POLICY, (DISPLAY, REASON))
