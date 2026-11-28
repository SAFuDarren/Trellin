# SPDX-License-Identifier: Apache-2.0
"""Verify batched workbench reads against disposable PostgreSQL databases."""

from contextlib import contextmanager
import os
from threading import Thread

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store, bind_workspace
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import proof_graph
from tests._native_db import disposable_project_logins
from tests.postgres.test_claim_withdrawal import frozen_claim
from tests.postgres.test_workbench_routes import ACTOR, REASON, _approved_statement, _request


pytestmark = pytest.mark.postgres
ROUTES = ("/api/threads", "/api/atlas")


@contextmanager
def project(tmp_path, monkeypatch, *, revision="head"):
    with disposable_project_logins(revision=revision) as logins, monkeypatch.context() as local:
        for setting in tuple(os.environ):
            if setting.startswith("RM_") and setting != "RM_TEST_DSN":
                local.delenv(setting)
        for setting, value in logins.items():
            local.setenv(setting, value)
        local.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        local.setenv("RM_TRELLIN_HOME", str(tmp_path / "library"))
        workspace = paths.initialize_workspace(tmp_path / "workspace")
        with paths.use_workspace(workspace, strict=True):
            bind_workspace(workspace)
        yield workspace, logins


@contextmanager
def running_server(workspace):
    with WorkbenchServer(workspace, port=0) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def add_notes(store, claim, statement, node, count):
    store.query("human", """
        SELECT rm.add_workbench_guidance(gen_random_uuid(), %s, %s, %s, 'Batch fixture note ' || index)
        FROM generate_series(1, %s) AS series(index)
    """, claim, statement, node, count)


def test_batch_project_reads_keep_limits_projection_and_access(tmp_path, monkeypatch):
    with project(tmp_path, monkeypatch) as (workspace, logins):
        with paths.use_workspace(workspace, strict=True):
            old, old_statement = _approved_statement(title="Old batch fixture", premise="Old fixture assumes True.")
            with Store() as store:
                # Keep a populated claim older than both endpoint limits.
                claims = store.query("human", """
                    SELECT rm.create_claim(source.contract_snapshot || jsonb_build_object(
                        'claim_id', gen_random_uuid(), 'title', 'Unfocussed batch fixture ' || n.index
                    )) AS id FROM rm.claims source CROSS JOIN generate_series(1, 40) AS n(index)
                    WHERE source.id = %s
                """, old)
                store.query("human", """
                    SELECT rm.freeze_claim(id, %s, %s) FROM unnest(%s::uuid[]) AS copies(id)
                """, ACTOR, REASON, [row["id"] for row in claims])
                add_notes(store, old, old_statement, f"goal:{old}", 1)
            left, left_statement = _approved_statement(title="Left batch fixture", premise="Left fixture assumes True.")
            right, right_statement = _approved_statement(title="Right batch fixture", premise="Right fixture assumes True.")
            with Store() as store:
                add_notes(store, left, left_statement, f"goal:{left}", 42)
                add_notes(store, right, right_statement, "other:missing-node", 42)
                visible = proof_graph.claims(store)
                graphs = {claim: proof_graph.snapshot(store, claim, references=False) for claim in (left, right)}

        assert len(visible) == 43
        with running_server(workspace) as server:
            queries = []
            original_query = Store.query

            def counted(store, role, statement, *values):
                if not queries:
                    assert original_query(store, "human", "SHOW transaction_read_only") == [
                        {"transaction_read_only": "on"},
                    ]
                queries.append(role)
                return original_query(store, role, statement, *values)

            monkeypatch.setattr(Store, "query", counted)
            threads = _request(server, "GET", "/api/threads")["threads"]
            assert 1 <= len(queries) <= 5 and set(queries) == {"human"}
            queries.clear()
            atlas = _request(server, "GET", "/api/atlas")["claims"]
            assert len(queries) == 2 and set(queries) == {"human"}
            assert [row["id"] for row in atlas] == [str(row["id"]) for row in visible[:40]]
            assert old not in {row["id"] for row in atlas}
            assert [thread["claim_id"] for thread in threads] == [right, left]
            for thread in threads:
                assert {event["text"] for event in thread["events"]} == {
                    f"Batch fixture note {index}" for index in range(3, 43)
                }
                assert all(event["audience"] == "local" for event in thread["events"])
            assert threads[0]["node"] == {"id": "other:missing-node", "kind": "other"}
            assert threads[1]["node"]["id"] == f"goal:{left}"
            for row in atlas[:2]:
                reference = graphs[row["id"]]
                assert [node["id"] for node in row["nodes"]] == [node["id"] for node in reference["nodes"]]
                assert row["edges"] == [[edge["source"], edge["target"]] for edge in reference["edges"]]

            for route in ROUTES:
                queries.clear()
                for header in ({"Host": "foreign.example"}, {"X-RM-Session": "wrong"}):
                    _request(server, "GET", route, headers=header, status=403)
                assert not queries

            with monkeypatch.context() as denied:
                queries.clear()
                denied.setenv("RM_HUMAN_DSN", logins["RM_AGENT_DSN"])
                for route in ROUTES:
                    failure = _request(server, "GET", route, status=404)
                    assert set(failure) == {"error"}
                assert not queries

        wrong = paths.initialize_workspace(tmp_path / "wrong")
        paths.bind_database(wrong, paths.database_id(workspace))
        with running_server(wrong) as server:
            for route in ROUTES:
                failure = _request(server, "GET", route, status=404)
                assert set(failure) == {"error"}


@pytest.mark.parametrize("revision", ("0069", "0070"))
def test_batch_project_reads_support_pre_audience_databases(tmp_path, monkeypatch, revision):
    with project(tmp_path, monkeypatch, revision=revision) as (workspace, _):
        with paths.use_workspace(workspace, strict=True):
            if revision == "0069":
                with Store() as store:
                    claim = frozen_claim(store, "Legacy atlas fixture", "True ∧ True")
            else:
                claim, statement = _approved_statement(title="Legacy batch fixture")
                with Store() as store:
                    add_notes(store, claim, statement, f"goal:{claim}", 1)
        with running_server(workspace) as server:
            atlas = _request(server, "GET", "/api/atlas")["claims"]
            assert [row["id"] for row in atlas] == [claim]
            threads = _request(server, "GET", "/api/threads")["threads"]
            if revision == "0069":
                assert threads == []
            else:
                thread, = threads
                event, = thread["events"]
                assert (thread["claim_id"], thread["node_id"], event["text"], event["audience"]) == (
                    claim, f"goal:{claim}", "Batch fixture note 1", "local",
                )
