# SPDX-License-Identifier: Apache-2.0
"""Verify scoped dispatch, receipt fences, and durable human review."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from research_multiplier.storage.migrate import upgrade
from tests._native_db import disposable_database, project_logins


pytestmark = pytest.mark.postgres

LIST = "SELECT rm.list_agent_work()"
READ = "SELECT rm.read_agent_work(%s)"
SUBMIT = "SELECT rm.submit_agent_work(%s,%s)"
CLAIM = "SELECT rm.claim_agent_work(%s,%s)"
FINISH = "SELECT rm.finish_agent_work(%s,%s,%s,%s,%s)"
RETRY = "SELECT rm.retry_agent_work(%s,%s,%s,%s)"
REVIEW = "SELECT rm.review_agent_work(%s,%s,%s,%s,%s)"
CONTEXT = "a" * 64
DELIVERY = "b" * 64
FINDINGS = "c" * 64


def spec(mode="implement", **extra):
    from psycopg.types.json import Jsonb

    return Jsonb({
        "title": "A synthetic task",
        "mode": mode,
        "agent_id": str(uuid4()),
        "agent_name": "Example agent",
        "agent_revision": 1,
        "connection": {"provider": "test"},
        "context_sha256": CONTEXT,
        **extra,
    })


def bind(human):
    workspace = uuid4()
    database, = human.execute("SELECT rm.current_database_id()").fetchone()
    human.execute("SELECT rm.bind_research_workspace(%s,%s)", (workspace, database))
    return workspace, database


def value(connection, query, *parameters):
    result, = connection.execute(query, parameters).fetchone()
    return result


def test_agent_work_forward_upgrade_and_exact_handoff():
    import psycopg

    with disposable_database() as dsn:
        upgrade(dsn, "0082")
        with (
            project_logins(dsn) as logins,
            psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as human,
            psycopg.connect(logins["RM_SCHEDULER_DSN"], autocommit=True) as scheduler,
            psycopg.connect(logins["RM_AGENT_DSN"], autocommit=True) as agent,
        ):
            workspace, _ = bind(human)
            actor = value(human, "SELECT id FROM rm.current_local_user()")
            upgrade(dsn)
            assert value(human, LIST) == []
            assert value(human, READ, uuid4()) is None

            implementation, audit, worker = uuid4(), uuid4(), uuid4()
            definition = spec()
            submitted = value(human, SUBMIT, implementation, definition)
            assert submitted["state"] == "queued"
            assert submitted["user_id"] == str(actor)
            assert submitted["workspace_id"] == str(workspace)
            assert value(human, SUBMIT, implementation, definition) == submitted
            with pytest.raises(psycopg.errors.UniqueViolation):
                value(human, SUBMIT, implementation, spec(title="Different task"))
            with pytest.raises(psycopg.errors.CheckViolation):
                value(human, SUBMIT, audit, spec("review", predecessor_id=str(implementation),
                                                  predecessor_sha256=DELIVERY))

            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                value(agent, READ, implementation)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                value(human, CLAIM, worker, 30)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                value(scheduler, LIST)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                value(agent, "SELECT spec FROM rm.agent_work_items")

            assert value(scheduler, CLAIM, worker, 30)["state"] == "running"
            assert value(scheduler, CLAIM, uuid4(), 30) is None
            with pytest.raises(psycopg.errors.CheckViolation):
                value(scheduler, FINISH, implementation, 1, uuid4(), DELIVERY, None)
            delivered = value(scheduler, FINISH, implementation, 1, worker, DELIVERY, None)
            assert delivered["attempt"]["result_sha256"] == DELIVERY
            assert value(scheduler, FINISH, implementation, 1, worker, DELIVERY, None) == delivered
            with pytest.raises(psycopg.errors.CheckViolation):
                value(scheduler, FINISH, implementation, 1, worker, FINDINGS, None)
            with pytest.raises(psycopg.errors.CheckViolation):
                value(human, SUBMIT, audit, spec("review", predecessor_id=str(implementation),
                                                  predecessor_sha256=FINDINGS))

            reviewer = uuid4()
            assignment = spec("review", predecessor_id=str(implementation), predecessor_sha256=DELIVERY)
            assert value(human, SUBMIT, audit, assignment)["state"] == "queued"
            received = value(scheduler, CLAIM, reviewer, 30)
            assert received["spec"]["predecessor_sha256"] == DELIVERY
            assert received["id"] == str(audit)
            value(scheduler, FINISH, audit, 1, reviewer, FINDINGS, None)
            action = uuid4()
            with pytest.raises(psycopg.errors.CheckViolation):
                value(human, REVIEW, audit, action, DELIVERY, "accept", "Unexpected version")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                value(scheduler, REVIEW, audit, action, FINDINGS, "accept", "Fake review")
            with psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as reopened:
                assert value(reopened, READ, audit)["state"] == "delivered"
                reviewed = value(reopened, REVIEW, audit, action, FINDINGS, "accept", "Checks passed")
                assert reviewed["state"] == "accepted"
                assert reviewed["review"]["delivery_sha256"] == FINDINGS
                assert value(reopened, REVIEW, audit, action, FINDINGS, "accept", "Checks passed") == reviewed
                with pytest.raises(psycopg.errors.UniqueViolation):
                    value(reopened, REVIEW, audit, action, FINDINGS, "return", "Changed decision")
            events = human.execute("""
                SELECT type, user_id, subject_id, data FROM rm.read_workspace_events(%s,0,200)
                WHERE type = 'agent_work.changed' ORDER BY id
            """, (workspace,)).fetchall()
            assert [data["state"] for _, _, _, data in events] == [
                "queued", "running", "delivered", "queued", "running", "delivered", "accepted",
            ]
            assert events[-1] == (
                "agent_work.changed", actor, audit, {"state": "accepted", "generation": 1},
            )
            assert all(set(data) == {"state", "generation"} for _, _, _, data in events)


def test_agent_work_concurrent_claim_expiry_retry_and_stale_predecessor():
    import psycopg

    with disposable_database() as dsn:
        upgrade(dsn)
        with (
            project_logins(dsn) as logins,
            psycopg.connect(dsn, autocommit=True) as owner,
            psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as human,
            psycopg.connect(logins["RM_SCHEDULER_DSN"], autocommit=True) as scheduler,
        ):
            workspace, database = bind(human)
            implementation, audit = uuid4(), uuid4()
            value(human, SUBMIT, implementation, spec())
            barrier = Barrier(2, timeout=5)

            def try_claim(worker):
                with psycopg.connect(logins["RM_SCHEDULER_DSN"], autocommit=True) as connection:
                    barrier.wait()
                    return worker, value(connection, CLAIM, worker, 1)

            with ThreadPoolExecutor(max_workers=2) as executors:
                claims = list(executors.map(try_claim, [uuid4(), uuid4()]))
            (stale, claimed), = [(worker, result) for worker, result in claims if result is not None]
            assert claimed["id"] == str(implementation)
            scheduler.execute("SELECT pg_sleep(1.1)")
            assert value(human, READ, implementation)["state"] == "interrupted"
            assert value(scheduler, CLAIM, uuid4(), 30) is None
            with pytest.raises(psycopg.errors.CheckViolation):
                value(scheduler, FINISH, implementation, 1, stale, DELIVERY, None)

            action = uuid4()
            with pytest.raises(psycopg.errors.CheckViolation):
                value(human, RETRY, implementation, action, FINDINGS, 1)
            retried = value(human, RETRY, implementation, action, CONTEXT, 1)
            assert retried["generation"] == 2
            assert retried["retry_generation"] == 2
            assert value(human, RETRY, implementation, action, CONTEXT, 1) == retried
            worker = uuid4()
            assert value(scheduler, CLAIM, worker, 30)["generation"] == 2
            failed = value(scheduler, FINISH, implementation, 2, worker, None, "agentWork.runtimeUnavailable")
            assert failed["state"] == "failed"
            assert failed["attempt"]["error"] == "agentWork.runtimeUnavailable"
            with pytest.raises(psycopg.errors.CheckViolation):
                value(human, RETRY, implementation, uuid4(), CONTEXT, 1)
            assert value(human, RETRY, implementation, uuid4(), CONTEXT, 2)["generation"] == 3
            replayed = value(human, RETRY, implementation, action, CONTEXT, 1)
            assert replayed["generation"] == 3
            assert replayed["retry_generation"] == 2
            with pytest.raises(psycopg.errors.UniqueViolation):
                value(human, RETRY, implementation, action, CONTEXT, 2)
            assert value(scheduler, CLAIM, worker, 30)["generation"] == 3
            with pytest.raises(psycopg.errors.CheckViolation):
                value(scheduler, FINISH, implementation, 2, worker, None, "agentWork.runtimeUnavailable")
            assert value(scheduler, FINISH, implementation, 3, worker, DELIVERY, None)["state"] == "delivered"
            value(human, SUBMIT, audit, spec("review", predecessor_id=str(implementation),
                                               predecessor_sha256=DELIVERY))
            value(human, REVIEW, implementation, uuid4(), DELIVERY, "return", "Needs rework")
            assert value(scheduler, CLAIM, uuid4(), 30) is None
            assert value(human, READ, audit)["state"] == "blocked"
            assert value(human, READ, audit)["attempt"]["error"] == "stale_predecessor"

            restored = uuid4()
            owner.execute("SELECT rm.reidentify_restored_database(%s,%s)", (database, restored))
            assert value(human, READ, implementation) is None
            assert value(human, LIST) == []
            with pytest.raises(psycopg.errors.NoDataFound):
                value(scheduler, FINISH, implementation, 3, worker, DELIVERY, None)
            assert value(human, "SELECT rm.workspace_event_cursor(%s)", restored) == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                value(human, "SELECT rm.workspace_event_cursor(%s)", workspace)
