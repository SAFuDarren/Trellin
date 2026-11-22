"""Workbench request checks that run before any database access (docs/interfaces.md section 4)."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import http.client
import json
import threading
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workbench import server as workbench_server
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import release_updates, workbench_agent_work, workbench_project_canvas

pytestmark = pytest.mark.offline


@pytest.fixture
def server(tmp_path):
    instance = WorkbenchServer(tmp_path, port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()


@pytest.fixture
def agent_work_polling(server, monkeypatch):
    store = SimpleNamespace()
    monkeypatch.setattr(paths, "use_workspace", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(workbench_server, "Store", lambda: nullcontext(store))
    return server, store


@pytest.mark.parametrize("error", [
    OSError("Synthetic I/O fault."),
    DatabaseOperationError("scheduler", "08006", retryable=True),
])
def test_agent_work_poll_recovers_without_another_wake(agent_work_polling, monkeypatch, error):
    instance, store = agent_work_polling
    polls = []
    delays = []

    def query(role, statement, worker, seconds):
        assert role == "scheduler" and "rm.claim_agent_work" in statement
        polls.append((worker, seconds))
        if len(polls) == 1:
            raise error
        return [{"result": None}]

    store.query = query
    monkeypatch.setattr(instance.closing, "wait", lambda timeout: delays.append(timeout))
    instance._run_agent_work()

    assert delays == [0.25]
    assert len(polls) == 2 and polls[0] == polls[1]
    assert instance._agent_work_thread is None


def test_agent_work_poll_exhaustion_preserves_diagnosis_and_can_reopen(agent_work_polling, monkeypatch, caplog):
    instance, store = agent_work_polling
    polls = []
    delays = []

    def offline(role, statement, worker, seconds):
        polls.append(worker)
        raise OSError("Synthetic unavailable database address.")

    store.query = offline
    monkeypatch.setattr(instance.closing, "wait", lambda timeout: delays.append(timeout))
    instance._run_agent_work()

    assert delays == [0.25, 0.5, 1.0]
    assert len(polls) == 4 and len(set(polls)) == 1
    assert instance._agent_work_thread is None
    assert "paused after 3 retries (OSError)" in caplog.text
    assert "Synthetic unavailable database address" not in caplog.text

    store.query = lambda role, statement, worker, seconds: polls.append(worker) or [{"result": None}]
    instance._run_agent_work()
    assert len(polls) == 5 and polls[-1] != polls[0]


@pytest.mark.parametrize("error", [
    ValueError("Synthetic invalid configuration."),
    DatabaseOperationError("scheduler", "42501"),
])
def test_agent_work_poll_stops_on_permanent_errors(agent_work_polling, monkeypatch, error):
    instance, store = agent_work_polling

    def invalid(*args):
        raise error

    store.query = invalid
    monkeypatch.setattr(instance.closing, "wait", lambda timeout: pytest.fail("Retried a permanent error."))
    instance._run_agent_work()
    assert instance._agent_work_thread is None


def test_agent_work_shutdown_interrupts_retry_wait(agent_work_polling, monkeypatch):
    instance, store = agent_work_polling
    waiting = threading.Event()
    wait = instance.closing.wait
    polls = []

    def offline(*args):
        polls.append(1)
        raise OSError("Synthetic I/O fault.")

    def track_wait(timeout):
        waiting.set()
        return wait(timeout)

    store.query = offline
    monkeypatch.setattr(instance.closing, "wait", track_wait)
    monkeypatch.setattr(workbench_server, "_AGENT_WORK_RETRY_DELAYS", (60,), raising=False)
    worker = threading.Thread(target=instance._run_agent_work, daemon=True)
    worker.start()
    try:
        assert waiting.wait(2)
    finally:
        instance.server_close()
        worker.join(2)

    assert not worker.is_alive()
    assert len(polls) == 1


def test_agent_work_poll_does_not_repeat_external_attempt_after_lost_finish_receipt(
    agent_work_polling, monkeypatch,
):
    instance, store = agent_work_polling
    task = {"id": str(uuid4()), "generation": 1}
    digest = "e" * 64
    performed = []
    steps = []

    def query(role, statement, *values):
        assert role == "scheduler"
        if "rm.claim_agent_work" in statement:
            steps.append("claim")
            return [{"result": task if len(steps) == 1 else None}]
        assert "rm.finish_agent_work" in statement and values[3:] == (digest, None)
        steps.append("finish")
        raise DatabaseOperationError("scheduler", "08006", retryable=True)

    store.query = query
    monkeypatch.setattr(instance.agent_work, "_execute", lambda item: performed.append(item) or digest)
    monkeypatch.setattr(instance.closing, "wait", lambda timeout: False)
    instance._run_agent_work()

    assert steps == ["claim", "finish", "claim"]
    assert performed == [task]


def test_agent_work_human_database_errors_keep_the_public_mapping():
    def unavailable(*args):
        raise DatabaseOperationError("human", "08006", retryable=True)

    store = SimpleNamespace(query=unavailable)
    with pytest.raises(workbench_agent_work.AgentWorkError, match="agentWork.unavailable"):
        workbench_agent_work._query(store, "human", "SELECT fixture")


def request(server, method, path, *, body=None, headers=None, session=True, origin=True):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    sent = dict(headers or {})
    if session:
        sent.setdefault("X-RM-Session", server.session)
    if origin and method == "POST":
        sent.setdefault("Origin", server.origin)
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        sent.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    content = response.read()
    connection.close()
    return response, content


@contextmanager
def _profile_server(workspace, connections):
    with WorkbenchServer(workspace, port=0) as instance:
        instance.connections = SimpleNamespace(list=lambda: list(connections))
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            thread.join()


def test_team_agent_settings_are_scoped_to_the_local_project(tmp_path, monkeypatch):
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "app"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    first = paths.initialize_workspace(tmp_path / "first")
    second = paths.initialize_workspace(tmp_path / "second")
    connection = {"id": str(uuid4()), "status": "connected", "fingerprint": "fixture"}
    connections = [connection]
    profile = {
        "id": str(uuid4()), "revision": 0, "connection_id": connection["id"], "name": "Paper reviewer",
        "description": "Check evidence", "avatar": "book", "instructions": "Review the stated assumptions.",
        "starters": ["Which assumptions matter?"],
    }
    route = "/api/team-agents"
    save = route + "/save"

    def inspect(instance, method, path, *, expected=200, **options):
        response, content = request(instance, method, path, **options)
        assert response.status == expected
        return json.loads(content)

    with _profile_server(first, connections) as instance:
        assert request(instance, "GET", route, session=False)[0].status == 403
        assert request(instance, "POST", save, body=profile, origin=False)[0].status == 403
        assert request(instance, "POST", save, body=profile, headers={"Origin": "https://foreign.test"})[0].status == 403
        assert inspect(instance, "GET", route) == {"agents": []}
        for invalid in ({"name": "   "}, {"starters": [""]}, {"avatar": "image.png"}, {"workspace_id": str(uuid4())}):
            assert inspect(instance, "POST", save, body={**profile, **invalid}, expected=400) == {
                "error": "team_agents.invalid",
            }
        assert inspect(instance, "POST", save, body={**profile, "connection_id": str(uuid4())}, expected=409) == {
            "error": "team_agents.connection_unavailable",
        }
        with ThreadPoolExecutor(max_workers=2) as requests:
            saved, repeated = [
                result["agent"] for result in requests.map(
                    lambda _: inspect(instance, "POST", save, body=profile), range(2)
                )
            ]
        assert saved == {**profile, "revision": 1}
        assert repeated == saved
        assert inspect(instance, "GET", route) == {"agents": [saved]}
        edited = {**saved, "name": "Method reviewer"}
        updated = inspect(instance, "POST", save, body=edited)["agent"]
        assert updated == {**edited, "revision": 2}
        assert inspect(instance, "POST", save, body=edited) == {"agent": updated}
        assert inspect(instance, "POST", save, body={**saved, "name": "Stale editor"}, expected=409) == {
            "error": "team_agents.conflict",
        }
        original_session = instance.session

    with _profile_server(second, connections) as instance:
        assert request(instance, "GET", route, headers={"X-RM-Session": original_session})[0].status == 403
        assert inspect(instance, "GET", route) == {"agents": []}
        assert inspect(instance, "POST", save, body=updated, expected=409) == {"error": "team_agents.conflict"}

    connections.clear()
    with _profile_server(first, connections) as instance:
        assert inspect(instance, "GET", route) == {"agents": [updated]}
        assert inspect(instance, "POST", save, body=edited) == {"agent": updated}
        assert inspect(instance, "POST", save, body={**updated, "name": "Unavailable"}, expected=409) == {
            "error": "team_agents.connection_unavailable",
        }
        replacement = {"id": str(uuid4()), "status": "untested", "fingerprint": "replacement"}
        connections.append(replacement)
        relink = {**updated, "connection_id": replacement["id"]}
        assert inspect(instance, "POST", save, body=relink) == {"agent": {**relink, "revision": 3}}


def test_canvas_layout_and_links_persist_without_crossing_workspace_boundaries(tmp_path, monkeypatch):
    first_workspace = paths.initialize_workspace(tmp_path / "first")
    second_workspace = paths.initialize_workspace(tmp_path / "second")
    settings = tmp_path / "settings" / "canvas.sqlite3"
    first, second = sorted((str(uuid4()), str(uuid4())))
    foreign = str(uuid4())
    visible = {first, second}
    endpoint = "/api/project-canvas"
    move = {"action": "move", "id": first, "x": 174, "y": -23}
    link = {"action": "link", "source": second, "target": first}
    unlink = {**link, "action": "unlink"}
    empty = {"positions": {}, "links": []}

    def configure(instance, identifiers):
        monkeypatch.setattr(instance, "project_canvas",
                            workbench_project_canvas.ProjectCanvas(instance.workspace, settings_file=settings))
        monkeypatch.setattr(instance, "read", lambda workflow, *args: workflow(identifiers, *args))
        monkeypatch.setattr(instance, "write", lambda *args: pytest.fail("Canvas metadata changes the proof database"))

    def post(instance, body, status=200):
        response, payload = request(instance, "POST", endpoint, body=body)
        assert response.status == status, payload
        return json.loads(payload)

    def snapshot(instance):
        response, payload = request(instance, "GET", endpoint)
        assert response.status == 200, payload
        return json.loads(payload)

    monkeypatch.setattr(workbench_project_canvas.proof_graph, "visible_claim_ids",
                        lambda visible, identifiers: visible.intersection(identifiers))
    monkeypatch.setattr(workbench_project_canvas.proof_graph, "canvas_claim_index",
                        lambda visible: {"claims": [{"id": identifier} for identifier in sorted(visible)]})
    with _profile_server(first_workspace, ()) as instance:
        configure(instance, visible)
        assert request(instance, "GET", endpoint, session=False)[0].status == 403
        assert request(instance, "GET", endpoint + "/claims", session=False)[0].status == 403
        assert request(instance, "GET", endpoint + "?project=other")[0].status == 400
        response, payload = request(instance, "GET", endpoint + "/claims")
        assert response.status == 200
        assert json.loads(payload) == {"claims": [{"id": first}, {"id": second}]}
        assert request(instance, "POST", endpoint, body=move, session=False)[0].status == 403
        assert request(instance, "POST", endpoint, body=move, origin=False)[0].status == 403
        assert request(instance, "POST", endpoint, body=move, headers={"Origin": "https://foreign.test"})[0].status == 403
        assert snapshot(instance) == empty
        for body in ({**move, "x": True}, {**move, "y": float("nan")}, {**move, "y": 10**20},
                     {**move, "project": foreign}, {**link, "source": first}):
            assert post(instance, body, 400) == {"error": "project_canvas.invalid"}
        assert post(instance, {**link, "source": foreign}, 409) == {"error": "project_canvas.missing"}
        assert not settings.exists()

        other_position = {**move, "id": second, "x": 100}
        with ThreadPoolExecutor(max_workers=3) as executor:
            list(executor.map(lambda body: post(instance, body), (move, other_position, link)))
        expected = {
            "positions": {first: {"x": 174, "y": -23}, second: {"x": 100, "y": -23}},
            "links": [{"source": first, "target": second}],
        }
        assert post(instance, {**link, "source": first, "target": second}) == expected

    with _profile_server(second_workspace, ()) as instance:
        configure(instance, {foreign})
        assert snapshot(instance) == empty
        assert post(instance, move, 409) == {"error": "project_canvas.missing"}
        assert post(instance, unlink) == empty

    visible.remove(second)
    with _profile_server(first_workspace, ()) as instance:
        configure(instance, visible)
        assert snapshot(instance) == expected
        unlinked = {**expected, "links": []}
        assert post(instance, unlink) == unlinked
        assert post(instance, unlink) == unlinked


def test_page_is_served_with_strict_security_headers(server):
    response, content = request(server, "GET", "/", session=False)
    assert response.status == 200
    assert b'<div id="root"></div>' in content
    policy = response.getheader("Content-Security-Policy")
    assert "default-src 'none'" in policy and "script-src 'self'" in policy
    # Same-origin images only, so the packaged favicon loads and nothing else does.
    assert "img-src 'self'" in policy and "img-src *" not in policy
    assert response.getheader("Cache-Control") == "no-store"
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("X-Frame-Options") == "DENY"


def test_api_requires_the_session_token(server):
    response, _ = request(server, "GET", "/api/claims", session=False)
    assert response.status == 403
    response, _ = request(server, "GET", "/api/claims", headers={"X-RM-Session": "wrong"})
    assert response.status == 403


def test_foreign_host_and_origin_are_refused(server):
    response, _ = request(server, "GET", "/", session=False, headers={"Host": "evil.example"})
    assert response.status == 403
    response, _ = request(server, "GET", "/api/claims", headers={"Origin": "http://evil.example"})
    assert response.status == 403


def test_release_updates_are_local_and_separate_from_research(server, tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("An unauthorized or read-only settings request must not use the research DB or network.")

    settings = tmp_path / "local-config" / "release-updates.sqlite3"
    monkeypatch.setattr(server, "release_updates", release_updates.ReleaseUpdates(
        settings, installed_version=lambda: "1.0.18",
    ))
    monkeypatch.setattr(server, "read", unexpected)
    monkeypatch.setattr(server, "write", unexpected)
    monkeypatch.setattr(release_updates, "HTTPSConnection", unexpected)
    path = "/api/release-updates"
    body = {"action": "check"}
    assert request(server, "GET", path, session=False)[0].status == 403
    assert request(server, "POST", path, body=body, session=False)[0].status == 403
    assert request(server, "POST", path, body=body, origin=False)[0].status == 403
    assert request(server, "POST", path, body=body, headers={"Origin": "https://elsewhere.test"})[0].status == 403
    assert request(server, "GET", path + "?action=check")[0].status == 400
    assert request(server, "POST", path, body={**body, "workspace_id": "project"})[0].status == 400
    assert request(server, "POST", path, body={**body, "url": "https://elsewhere.test"})[0].status == 400
    response, content = request(server, "GET", path)
    assert response.status == 200 and json.loads(content)["installed_version"] == "1.0.18"
    assert request(server, "POST", path, body={"action": "auto_check"})[0].status == 200
    assert not settings.exists()
    response, content = request(server, "POST", path, body={"action": "automatic", "enabled": True})
    assert response.status == 200 and json.loads(content)["automatic"] is True
    assert settings.is_file()


def test_model_connections_are_session_bound_and_do_not_read_the_project(server, tmp_path, monkeypatch):
    from research_multiplier.models.connections import Registry

    registry = Registry(tmp_path / "connections.sqlite3")
    monkeypatch.setattr(server, "connections", registry)
    monkeypatch.setattr(server, "read", lambda *args: pytest.fail("Unexpected project access"))
    path = "/api/model-connections"
    body = {"model": "example-model", "provider": "codex-cli"}
    assert request(server, "GET", path, session=False)[0].status == 403
    assert request(server, "POST", path + "/add", body=body, session=False)[0].status == 403
    assert request(server, "POST", path + "/add", body=body, origin=False)[0].status == 403
    assert request(server, "POST", path + "/add", body=body, headers={"Origin": "https://foreign.test"})[0].status == 403
    assert json.loads(request(server, "GET", path)[1]) == {"connections": []}
    response, content = request(server, "POST", path + "/add", body=body)
    assert response.status == 200
    connection = json.loads(content)
    assert set(connection) == {"id", "provider", "model", "label", "status", "fingerprint"}
    assert json.loads(request(server, "GET", path)[1]) == {"connections": [connection]}
    assert request(server, "POST", path + "/remove", body={"id": connection["id"]})[0].status == 200
    assert json.loads(request(server, "GET", path)[1]) == {"connections": []}


def test_native_connections_are_discovered_and_selected_only_in_the_bound_session(server, tmp_path, monkeypatch):
    from research_multiplier.models import cli_executor, connections

    executable = tmp_path / "claude"
    executable.write_text("fixture")
    login = []

    def find_binary(provider, selected):
        if provider == "claude-code" and selected in {None, str(executable)}:
            return str(executable)

    def unexpected(*args, **kwargs):
        pytest.fail("An unauthorized or native connection request must not read a project or invoke inference.")

    monkeypatch.setattr(server, "connections", connections.Registry(tmp_path / "connections.sqlite3"))
    monkeypatch.setattr(server, "read", unexpected)
    monkeypatch.setattr(server, "write", unexpected)
    monkeypatch.setattr(connections, "_adapter", unexpected)
    monkeypatch.setattr(connections, "_binary", find_binary)
    monkeypatch.setattr(cli_executor, "require_native_login", lambda *args: login.append(args))

    discovery = "/api/model-connections/native"
    connect = "/api/model-connections/add-native"
    body = {"provider": "claude-code"}
    assert request(server, "GET", discovery, session=False)[0].status == 403
    assert request(server, "GET", discovery, headers={"Origin": "https://foreign.test"})[0].status == 403
    assert request(server, "GET", discovery + "?provider=claude-code")[0].status == 400
    assert request(server, "POST", connect, body=body, session=False)[0].status == 403
    assert request(server, "POST", connect, body=body, origin=False)[0].status == 403
    assert request(server, "POST", connect, body={**body, "binary": str(executable)})[0].status == 409
    assert not login

    response, content = request(server, "GET", discovery)
    assert response.status == 200
    assert json.loads(content) == {"providers": [{"provider": "claude-code", "status": "signed_in", "models": [
        {"id": "sonnet", "label": "Sonnet"}, {"id": "opus", "label": "Opus"},
        {"id": "haiku", "label": "Haiku"}, {"id": "fable", "label": "Fable"},
    ]}]}
    assert server.connections.list() == []

    response, content = request(server, "POST", connect, body={**body, "model": "opus"})
    assert response.status == 200
    saved = json.loads(content)
    assert saved["provider"] == "claude-code" and saved["model"] == "opus"
    assert server.connections.list() == [saved]
    assert login == [("claude-code", str(executable))] * 2


def test_versioned_claims_rejects_unauthorized_and_invalid_requests(server, monkeypatch, capsys):
    workspace_id = "d42ca2f7-9288-47af-b7e5-1150e6df5304"
    path = f"/api/v1/workspaces/{workspace_id}/claims"

    def unexpected_read(*args):
        pytest.fail("A malformed or unauthorized request must not read the project's records.")

    monkeypatch.setattr(server, "read", unexpected_read)
    requests = (
        (path, 401, "request.unauthenticated", {"session": False}),
        (path, 401, "request.unauthenticated", {"headers": {"X-RM-Session": "wrong"}}),
        (path, 403, "request.forbidden", {"headers": {"Host": "foreign.example"}}),
        (path, 403, "request.forbidden", {"headers": {"Origin": "http://foreign.example"}}),
        (path + "?limit=1", 400, "claims.invalid_query", {}),
        (path + "?", 400, "claims.invalid_query", {}),
        (path.replace(workspace_id, "bad-address"), 404, "claims.not_found", {}),
    )
    request_ids = set()
    for target, status, code, options in requests:
        response, content = request(server, "GET", target, **options)
        identifier = response.getheader("X-Request-Id")
        request_ids.add(UUID(identifier))
        assert response.status == status
        assert response.getheader("Cache-Control") == "no-store"
        assert json.loads(content)["error"]["code"] == code
        assert json.loads(content)["error"]["request_id"] == identifier
        record = json.loads(capsys.readouterr().err)
        assert record.pop("duration_ms") >= 0
        assert record == {
            "route": "/api/v1/workspaces/{workspace_id}/claims", "status": status, "request_id": identifier,
        }
    assert len(request_ids) == len(requests)


@pytest.mark.parametrize(("error", "status", "code"), [
    (DatabaseOperationError("human", "08001", "private DSN fixture"), 503, "database.unavailable"),
    (ValueError("private DSN fixture"), 503, "database.unavailable"),
    (RuntimeError("private DSN fixture"), 500, "request.failed"),
])
def test_versioned_claims_errors_redact_diagnostics(server, monkeypatch, capsys, error, status, code):
    def unavailable(*args):
        raise error

    monkeypatch.setattr(server, "read", unavailable)
    path = "/api/v1/workspaces/d42ca2f7-9288-47af-b7e5-1150e6df5304/claims"
    response, content = request(server, "GET", path)
    assert response.status == status and b"private DSN fixture" not in content
    assert json.loads(content)["error"]["code"] == code
    assert "private DSN fixture" not in capsys.readouterr().err


@pytest.mark.parametrize("path", [
    "/api/graph?claim=not-a-uuid",
    "/api/graph",
    "/api/impact?claim=0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60",
    "/api/inbox?limit=5",
    "/api/team?x=1",
    "/api/doctor?fresh=yes",
    "/api/proof-task?claim=not-a-uuid",
    "/api/claim-code?code=FLOW&code=ABC",
])
def test_invalid_queries_are_rejected_before_reading(server, path):
    response, content = request(server, "GET", path)
    assert response.status == 400
    assert "error" in json.loads(content)


def test_unknown_paths_are_not_found(server):
    response, _ = request(server, "GET", "/secret.txt")
    assert response.status == 404
    response, _ = request(server, "POST", "/api/unknown", body={})
    assert response.status == 404


@pytest.mark.parametrize("body", [
    {"evidence_id": "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60", "action": "maybe", "actor": "A", "reason": "r"},
    {"evidence_id": "not-a-uuid", "action": "approve", "actor": "A", "reason": "r"},
    {"evidence_id": "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60", "action": "approve", "actor": " ", "reason": "r"},
    {"evidence_id": "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60", "action": "approve", "actor": "A", "reason": "r",
     "extra": True},
])
def test_review_decisions_are_validated(server, body):
    response, _ = request(server, "POST", "/api/review", body=body)
    assert response.status == 400


def test_versioned_review_requires_local_session_and_idempotency_key(server, monkeypatch):
    path = "/api/v1/workspaces/d42ca2f7-9288-47af-b7e5-1150e6df5304/evidence/0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60/reviews"
    review = {"decision": "approve", "reason": "Review complete."}

    def unexpected_write(*args):
        pytest.fail("A malformed or unauthorized request must not write.")

    monkeypatch.setattr(server, "write", unexpected_write)
    for status, code, options in (
        (401, "request.unauthenticated", {"session": False}),
        (403, "request.forbidden", {"origin": False}),
        (400, "request.invalid", {}),
    ):
        response, content = request(server, "POST", path, body=review, **options)
        assert response.status == status
        error = json.loads(content)["error"]
        assert error["code"] == code
        assert error["request_id"] == response.getheader("X-Request-Id")


def test_workspace_event_stream_requires_a_session_and_valid_cursor(server):
    path = "/api/v1/workspaces/d42ca2f7-9288-47af-b7e5-1150e6df5304/events"
    response, payload = request(server, "GET", path, session=False)
    assert response.status == 401 and json.loads(payload)["error"]["code"] == "request.unauthenticated"
    for cursor in ("-1", "1e3", "9223372036854775808"):
        response, payload = request(server, "GET", path, headers={"Last-Event-ID": cursor})
        assert response.status == 400 and json.loads(payload)["error"]["code"] == "events.invalid_cursor"


def test_writes_need_json_a_session_and_the_local_origin(server):
    valid = {"evidence_id": "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60", "action": "approve", "actor": "A", "reason": "r"}
    response, _ = request(server, "POST", "/api/review", body=valid, origin=False)
    assert response.status == 403
    response, _ = request(server, "POST", "/api/review", body=valid, session=False)
    assert response.status == 403
    response, _ = request(server, "POST", "/api/review", body=b"{}", headers={"Content-Type": "text/plain"})
    assert response.status == 400


@pytest.mark.parametrize("action", ["start", "stop"])
def test_proof_task_writes_require_a_local_session(server, action):
    path = f"/api/proof-task/{action}"
    response, _ = request(server, "POST", path, body={}, session=False)
    assert response.status == 403
    response, _ = request(server, "POST", path, body={}, origin=False)
    assert response.status == 403


def test_claim_code_routes_require_session_and_exact_payload(server, monkeypatch):
    def refuse_bad_references(operation, document):
        return operation(None, document)

    monkeypatch.setattr(server, "write", refuse_bad_references)
    response, _ = request(server, "GET", "/api/claim-code?code=FLOW-02", session=False)
    assert response.status == 403
    response, _ = request(server, "POST", "/api/claim-code", body={"code": "NOT-A-PREFIX"}, origin=False)
    assert response.status == 403
    response, content = request(server, "POST", "/api/claim-code", body={"code": "NOT-A-PREFIX"})
    assert response.status == 400
    assert json.loads(content) == {"error": "claim_code.invalid"}


@pytest.mark.parametrize("connected_model", [False, True])
def test_proof_task_starts_only_the_committed_policy(server, monkeypatch, connected_model):
    claim = "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60"
    policy = "b34a1bdf-8c98-497b-b33f-2be548ca9b7f"
    started, reached_policy, release = [], threading.Event(), threading.Event()
    document = {"claim_id": claim, "token": "a" * 64, "model_rounds": 0, "reason": "Reviewed"}
    if connected_model:
        document.update(model_rounds=1, public_model={"id": str(UUID(int=1)), "fingerprint": "b" * 64},
                        confirm_external=True)

    def write(action, supplied):
        assert action.__name__ == "start" and supplied == document
        started.append("committed")
        return {"policy_id": policy}

    def advance(stage, limit, identifiers, parallel, *, lake_watch, public_model_policies):
        started.append((stage, limit, identifiers, parallel, public_model_policies))
        reached_policy.set()
        release.wait(5)
        return {"errors": [], "lean_runs": []}

    monkeypatch.setattr(server, "write", write)
    monkeypatch.setattr("research_multiplier.workbench.server.paths.use_workspace", lambda *_, **__: nullcontext())
    monkeypatch.setattr("research_multiplier.workbench.server.research_progress.advance_cycle", advance)
    try:
        response, content = request(server, "POST", "/api/proof-task/start", body=document)
        assert response.status == 200
        assert json.loads(content) == {"policy_id": policy}
        assert reached_policy.wait(5)
        assert started == ["committed", ("combined", 1, [policy], 1, connected_model)]
        response, content = request(server, "POST", "/api/proof-task/start", body=document)
        assert response.status == 409
        assert json.loads(content) == {"error": "proof_task.busy"}
    finally:
        release.set()


def test_malformed_guidance_is_rejected(server):
    response, _ = request(server, "POST", "/api/guidance", body={"text": "hello"})
    assert response.status == 400


@pytest.mark.parametrize("path", [
    "/api/project-intake", "/api/project-intake/confirm", "/api/theorems", "/api/theorems/confirm",
])
def test_project_entry_writes_require_the_local_session_and_a_json_object(server, path):
    response, _ = request(server, "POST", path, body={}, origin=False)
    assert response.status == 403
    response, _ = request(server, "POST", path, body={}, session=False)
    assert response.status == 403
    response, _ = request(server, "POST", path, body=[])
    assert response.status == 400


def test_parallel_intake_is_refused_before_starting_a_second_model_call(server):
    with server._intake_lock:
        response, content = request(server, "POST", "/api/project-intake", body={})
    assert response.status == 409
    assert json.loads(content) == {"error": "intake.busy"}


def test_local_model_inventory_requires_project_session(server, monkeypatch):
    from research_multiplier.workflows import workbench_intake

    monkeypatch.setattr(workbench_intake, "models", lambda: {"models": ["test-local:4b"]})
    route = "/api/project-intake/models"
    assert request(server, "GET", route, session=False)[0].status == 403
    assert request(server, "GET", route + "?endpoint=invalid")[0].status == 400
    response, content = request(server, "GET", route)
    assert response.status == 200
    assert json.loads(content) == {"models": ["test-local:4b"]}


def test_built_interface_is_served(server):
    built = [path for path in server.assets if path.startswith("/assets/")]
    assert built, "the built interface ships its script and styles"
    response, content = request(server, "GET", "/", session=False)
    assert response.status == 200
    assert b'<div id="root"></div>' in content
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    assert b"<script>" not in content, "inline scripts would be blocked by the content security policy"
    for path in built:
        response, _ = request(server, "GET", path, session=False)
        assert response.status == 200
        assert response.getheader("Content-Type").startswith(("text/javascript", "text/css", "image/", "font/"))


def test_unknown_interface_files_are_not_found(server):
    response, _ = request(server, "GET", "/assets/missing.js", session=False)
    assert response.status == 404
    response, _ = request(server, "GET", "/assets/../server.py", session=False)
    assert response.status in {400, 404}


def test_local_lemma_plan_requires_trusted_write_and_bounds_request(server, monkeypatch):
    calls = []

    def verify(document):
        calls.append(document)
        return {"verified": 1}

    monkeypatch.setattr(server, "verify_lemma_plan", verify)
    path = "/api/lemma-plans"
    document = {"plan": {"format_version": 1, "nodes": []}}
    assert request(server, "POST", path, body=document, session=False)[0].status == 403
    assert request(server, "POST", path, body=document, origin=False)[0].status == 403
    assert request(server, "POST", path, body=document, headers={"Origin": "http://elsewhere.test"})[0].status == 403
    assert request(server, "POST", path, body=document, headers={"Host": "elsewhere.test"})[0].status == 403
    assert request(server, "POST", path, body={"plan": "x" * 64_000})[0].status == 400
    assert not calls

    response, content = request(server, "POST", path, body=document)
    assert response.status == 200 and json.loads(content) == {"verified": 1}
    assert calls == [document]


def test_proof_draft_routes_require_session_origin_and_exact_target(server, monkeypatch):
    def unexpected(*_):
        pytest.fail("An invalid or cross-origin draft request must not access the project.")

    monkeypatch.setattr(server, "read", unexpected)
    monkeypatch.setattr(server, "write", unexpected)
    claim = "13f1a082-cd6b-436c-a601-61cef26eb943"
    route = "/api/proof-drafts"
    target = f"{route}?claim_id={claim}&node_id=goal:{claim}"
    fields = {"claim_id": claim, "node_id": f"goal:{claim}", "expected_revision": None}
    assert request(server, "GET", target, session=False)[0].status == 403
    assert request(server, "GET", target + "&node_id=wrong")[0].status == 400
    assert request(server, "POST", route, body={**fields, "text": "draft"}, origin=False)[0].status == 403
    assert request(server, "POST", route, body={**fields, "text": "draft"}, session=False)[0].status == 403
    assert request(server, "POST", route, body={**fields, "text": "draft", "audience": "codex"})[0].status == 400
    assert request(server, "POST", route + "/discard", body=fields)[0].status == 400


def test_goal_related_search_stays_on_the_authenticated_read_path(server, monkeypatch):
    from research_multiplier.workflows import workbench_goal_review

    reads = []

    def read(action, document):
        assert action is workbench_goal_review.related
        reads.append(document)
        return {"matches": []}

    def write(*_):
        pytest.fail("Searching a goal's local context must use a read transaction.")

    monkeypatch.setattr(server, "read", read)
    monkeypatch.setattr(server, "write", write)
    route = "/api/goal-review/related"
    body = {"goal_id": "13f1a082-cd6b-436c-a601-61cef26eb943", "query": "A synthetic local search"}
    assert request(server, "POST", route, body=body, session=False)[0].status == 403
    assert request(server, "POST", route, body=body, origin=False)[0].status == 403
    assert request(server, "POST", route, body=body, headers={"Origin": "https://foreign.test"})[0].status == 403
    assert request(server, "GET", route)[0].status == 404
    assert not reads
    response, content = request(server, "POST", route, body=body)
    assert response.status == 200
    assert response.getheader("Cache-Control") == "no-store"
    assert json.loads(content) == {"matches": []}
    assert reads == [body]
