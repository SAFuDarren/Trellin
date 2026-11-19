"""Workbench request checks that run before any database access (docs/interfaces.md section 4)."""

from contextlib import nullcontext
import http.client
import json
import threading
from uuid import UUID

import pytest

from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workbench.server import WorkbenchServer
from research_multiplier.workflows import release_updates

pytestmark = pytest.mark.offline


@pytest.fixture
def server(tmp_path):
    instance = WorkbenchServer(tmp_path, port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()


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


def test_proof_task_starts_only_the_committed_policy(server, monkeypatch):
    claim = "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60"
    policy = "b34a1bdf-8c98-497b-b33f-2be548ca9b7f"
    started, reached_policy, release = [], threading.Event(), threading.Event()
    document = {"claim_id": claim, "token": "a" * 64, "model_rounds": 0, "reason": "Reviewed"}

    def write(action, supplied):
        assert action.__name__ == "start" and supplied == document
        started.append("committed")
        return {"policy_id": policy}

    def advance(stage, limit, identifiers, parallel, *, lake_watch):
        started.append((stage, limit, identifiers, parallel))
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
        assert started == ["committed", ("combined", 1, [policy], 1)]
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
