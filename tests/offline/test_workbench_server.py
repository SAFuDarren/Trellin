"""Workbench request checks that run before any database access (docs/interfaces.md section 4)."""

import http.client
import json
import threading

import pytest

from research_multiplier.workbench.server import WorkbenchServer

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
    assert b"<html" in content
    policy = response.getheader("Content-Security-Policy")
    assert "default-src 'none'" in policy and "script-src 'self'" in policy
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


@pytest.mark.parametrize("path", [
    "/api/graph?claim=not-a-uuid",
    "/api/graph",
    "/api/impact?claim=0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60",
    "/api/inbox?limit=5",
    "/api/team?x=1",
    "/api/doctor?fresh=yes",
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


def test_writes_need_json_a_session_and_the_local_origin(server):
    valid = {"evidence_id": "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60", "action": "approve", "actor": "A", "reason": "r"}
    response, _ = request(server, "POST", "/api/review", body=valid, origin=False)
    assert response.status == 403
    response, _ = request(server, "POST", "/api/review", body=valid, session=False)
    assert response.status == 403
    response, _ = request(server, "POST", "/api/review", body=b"{}", headers={"Content-Type": "text/plain"})
    assert response.status == 400


def test_malformed_guidance_is_rejected(server):
    response, _ = request(server, "POST", "/api/guidance", body={"text": "hello"})
    assert response.status == 400
