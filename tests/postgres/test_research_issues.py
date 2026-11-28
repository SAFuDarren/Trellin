# SPDX-License-Identifier: Apache-2.0
"""Verify version-pinned offers against bound, disposable PostgreSQL projects."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Thread
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.migrate import upgrade
from research_multiplier.storage.postgres import DatabaseOperationError, Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import workbench_proof_drafts, workbench_research_issues as issues
from tests._native_db import disposable_database, project_logins
from tests.postgres.test_agent_work import CLAIM, FINISH, READ, REVIEW, SUBMIT, spec, value
from tests.postgres.test_claim_withdrawal import frozen_claim
from tests.postgres.test_workbench_routes import _request


pytestmark = pytest.mark.postgres

DIRECTION = {"title": "Trace the fixture", "question": "Which recorded input changed?",
             "deliverable": "One checked comparison", "acceptance_criteria": "Reproduce the change",
             "required_capabilities": ["file.read"]}
OFFER = "SELECT rm.offer_research_issue(%s,%s,%s,%s)"
LIST = "SELECT * FROM rm.read_research_offers(%s,%s)"


@pytest.fixture
def project(tmp_path, monkeypatch):
    import psycopg

    workspace = paths.initialize_workspace(tmp_path / "fixture")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    with disposable_database() as dsn:
        upgrade(dsn, "0084")
        with project_logins(dsn) as logins, paths.use_workspace(workspace, strict=True):
            for key, setting in logins.items():
                monkeypatch.setenv(key, setting)
            bind_workspace(workspace)
            # Preserve a real approval from the prior schema, including its exact delivery.
            with (
                psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as human,
                psycopg.connect(logins["RM_SCHEDULER_DSN"], autocommit=True) as scheduler,
            ):
                task, worker = uuid4(), uuid4()
                value(human, SUBMIT, task, spec())
                value(scheduler, CLAIM, worker, 30)
                value(scheduler, FINISH, task, 1, worker, "f" * 64, None)
                original = value(human, REVIEW, task, uuid4(), "f" * 64, "accept", "Checked fixture")
            upgrade(dsn)
            yield workspace, dsn, logins
            with psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as reopened:
                assert value(reopened, READ, task) == original


def record(document, resolver):
    with Store() as store:
        return store.transaction("human", lambda: issues.offer(store, document, resolver))["issue"]


def unexpected_resolve(_):
    pytest.fail("A replay or an unlinked issue must not read the source.")


def test_selective_recheck_after_restart_and_exact_retry(project):
    workspace, _, _ = project
    workspace_id = str(paths.workspace_id(workspace))
    ref, other = str(uuid4()), str(uuid4())
    first, second, unrelated = "a" * 64, "b" * 64, "c" * 64
    report = {"source_id": workspace_id, "revision": str(uuid4()), "status": "available", "files": [
        {"reference_id": ref, "status": "unchanged", "sha256": first, "recorded_sha256": first},
        {"reference_id": other, "status": "unchanged", "sha256": unrelated, "recorded_sha256": unrelated},
    ]}

    def create(pins):
        request = {"offer_id": str(uuid4()), "direction": DIRECTION,
                   "inputs": [{"reference_id": identifier, "sha256": digest} for identifier, digest in pins]}
        return request, record(request, lambda _: report)

    request, original = create([(ref, first)])
    _, independent = create([(other, unrelated)])
    _, unlinked = create([])
    report["files"][0].update(status="changed", sha256=second)
    _, newer = create([(ref, second)])
    assert newer["inputs"] == [{"source_id": workspace_id, "reference_id": ref, "sha256": second}]
    assert record(request, unexpected_resolve) == original
    with pytest.raises(issues.ResearchIssuePreviewError, match="research_issue.conflict"):
        record({**request, "inputs": []}, unexpected_resolve)
    with pytest.raises(issues.ResearchIssuePreviewError, match="research_issue.input_changed"):
        record({**request, "offer_id": str(uuid4())}, lambda _: report)
    with pytest.raises(issues.ResearchIssuePreviewError, match="research_issue.input_unavailable"):
        record({**request, "offer_id": str(uuid4())}, lambda _: {**report, "source_id": str(uuid4())})

    def statuses(observations):
        with Store() as reopened:
            result = reopened.transaction("human", lambda: issues.list_offers(reopened, observations), read_only=True)
        return {row["id"]: row["input_status"] for row in result["issues"]}

    expected = {original["id"]: "needs_recheck", independent["id"]: "ready",
                unlinked["id"]: "ready", newer["id"]: "ready"}
    assert statuses(report) == expected
    report["files"][0] = {"reference_id": ref, "status": "missing"}
    assert statuses(report) == {**expected, newer["id"]: "needs_recheck"}
    report["files"][0] = {"reference_id": ref, "status": "inaccessible"}
    assert statuses(report) == {**expected, original["id"]: "source_unavailable", newer["id"]: "source_unavailable"}
    assert statuses({**report, "source_id": str(uuid4())}) == {
        original["id"]: "source_unavailable", independent["id"]: "source_unavailable",
        unlinked["id"]: "ready", newer["id"]: "source_unavailable",
    }


def test_offer_authentication_immutability_and_concurrent_replay(project):
    import psycopg
    from psycopg.types.json import Jsonb

    workspace, dsn, logins = project
    workspace_id = paths.workspace_id(workspace)
    request = {"offer_id": str(uuid4()), "direction": DIRECTION}
    starting = Barrier(2, timeout=10)

    def submit(_):
        with paths.use_workspace(workspace, strict=True):
            starting.wait()
            return record(request, unexpected_resolve)

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.map(submit, range(2))
    assert left == right
    arguments = workspace_id, left["id"], Jsonb(left["direction"]), Jsonb([])
    with (
        psycopg.connect(dsn, autocommit=True) as owner,
        psycopg.connect(logins["RM_HUMAN_DSN"], autocommit=True) as human,
    ):
        inaccessible = uuid4()
        assert len(human.execute(LIST, (workspace_id, None)).fetchall()) == 1
        with pytest.raises(psycopg.errors.UniqueViolation):
            value(human, OFFER, workspace_id, left["id"], Jsonb({**left["direction"], "title": "Other"}), Jsonb([]))
        for connection in (human, owner):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(LIST, (inaccessible, None))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(OFFER, (inaccessible, *arguments[1:]))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            owner.execute(LIST, (workspace_id, None))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            human.execute("SELECT * FROM rm.research_issue_offers")
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute("DELETE FROM rm.research_issue_offers WHERE id=%s", (left["id"],))
        for group in ("AGENT", "SCHEDULER", "VERIFIER"):
            with psycopg.connect(logins[f"RM_{group}_DSN"], autocommit=True) as blocked:
                for statement, values in ((LIST, (workspace_id, None)), (OFFER, arguments),
                                          ("SELECT * FROM rm.research_issue_offers", ())):
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        blocked.execute(statement, values)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            value(human, OFFER, workspace_id, uuid4(), Jsonb(left["direction"]),
                  Jsonb([{"source_id": str(inaccessible), "reference_id": str(uuid4()), "sha256": "a" * 64}]))
        events = human.execute("SELECT data FROM rm.read_workspace_events(%s,0,200) WHERE type='workspace.changed'",
                               (workspace_id,)).fetchall()
        assert events == [({"area": "activity"},)]

        # The index can hide older rows while idempotent lookup still sees the original offer.
        human.execute("""
            SELECT rm.offer_research_issue(%s, gen_random_uuid(), %s, '[]'::jsonb) FROM generate_series(1,100)
        """, (workspace_id, Jsonb(left["direction"])))
        recent = human.execute(LIST, (workspace_id, None)).fetchall()
        assert len(recent) == 100 and all(str(row[0]) != left["id"] for row in recent)
        assert record(request, unexpected_resolve) == left


def test_offer_exact_proof_note_and_retry_after_edit(project):
    with Store() as store:
        claim = frozen_claim(store, "Offer note fixture", "True")
        node = "goal:" + claim
        saved = workbench_proof_drafts.save(store, UUID(claim), node, None, "  Private fixture note.  ")["draft"]
    direction = {key: val for key, val in DIRECTION.items() if key != "question"}
    direction["proof_note"] = {"claim_id": claim, "node_id": node, "draft_revision": str(saved["revision"])}
    request = {"offer_id": str(uuid4()), "direction": direction}
    original = record(request, unexpected_resolve)
    assert original["direction"]["question"] == "Private fixture note."
    with Store() as store:
        workbench_proof_drafts.save(store, UUID(claim), node, saved["revision"], "A revised fixture note.")
        with pytest.raises(DatabaseOperationError) as changed:
            store.query("human", OFFER, store.workspace_id, uuid4(), store.json(original["direction"]), store.json([]))
        assert changed.value.state == "23514"
    assert record(request, unexpected_resolve) == original
    with pytest.raises(issues.ResearchIssuePreviewError, match="research_issue.stale"):
        record({**request, "offer_id": str(uuid4())}, unexpected_resolve)


def test_research_offers_http_round_trip_against_upgraded_database(project):
    workspace, _, _ = project
    workspace_id = str(paths.workspace_id(workspace))
    endpoint = f"/api/v1/workspaces/{workspace_id}/research-issues"
    reference = str(uuid4())
    report = {"source_id": workspace_id, "revision": str(uuid4()), "status": "available",
              "files": [{"reference_id": reference, "status": "unchanged", "sha256": "a" * 64}]}
    pin = {"reference_id": reference, "sha256": "a" * 64}
    document = {"direction": DIRECTION, "inputs": [pin]}
    key = {"Idempotency-Key": str(uuid4())}
    with WorkbenchServer(workspace, port=0) as server:
        server.source_observer = SimpleNamespace(verified_inputs=lambda _: report, current_snapshot=lambda: report)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            linked = _request(server, "POST", endpoint, body=document, status=201, headers=key)["issue"]
            unlinked = _request(server, "POST", endpoint, body={"direction": DIRECTION}, status=201,
                                headers={"Idempotency-Key": str(uuid4())})["issue"]
            report["files"][0].update(status="changed", sha256="b" * 64)
            rows = _request(server, "GET", endpoint)["items"]
            assert {row["id"]: row["input_status"] for row in rows} == {
                linked["id"]: "needs_recheck", unlinked["id"]: "ready",
            }
            replay = _request(server, "POST", endpoint, body=document, status=201, headers=key)["issue"]
            assert replay == linked
            invalid = _request(server, "POST", endpoint, body=document, status=409,
                               headers={"Idempotency-Key": str(uuid4())})
            assert invalid["error"]["code"] == "research_issue.input_changed"
        finally:
            server.shutdown()
            thread.join()
