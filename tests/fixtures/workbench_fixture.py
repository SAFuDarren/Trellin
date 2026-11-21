# SPDX-License-Identifier: Apache-2.0
"""Serve the built React workbench with fixed API data for end-to-end tests and visual checks.

Usage: python tests/fixtures/workbench_fixture.py [--port 4174], then open /#session=fixture

The data is synthetic: five claims, one graph with running model work, a blocker, an assumption with an
impact preview, conversation records and guidance channels for both local models and Codex. Guidance
and synthetic update and map settings are kept in memory. No database, credentials or external network access are involved.
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

APP = Path(__file__).resolve().parents[2] / "research_multiplier" / "workbench" / "app"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png"}
CLAIM = "11111111-1111-4111-8111-111111111111"
STATEMENT = "22222222-2222-4222-8222-222222222222"
H1 = "33333333-3333-4333-8333-333333333333"
LIB = "44444444-4444-4444-8444-444444444444"
ROUTE = "55555555-5555-4555-8555-555555555555"
EVID = "66666666-6666-4666-8666-666666666666"
NOW = datetime.now(timezone.utc)
CLAIM = "11111111-1111-4111-8111-111111111111"
STATEMENT = "22222222-2222-4222-8222-222222222222"
H1 = "33333333-3333-4333-8333-333333333333"
LIB = "44444444-4444-4444-8444-444444444444"
ROUTE = "55555555-5555-4555-8555-555555555555"
EVID = "66666666-6666-4666-8666-666666666666"


def at(minutes):
    return (NOW - timedelta(minutes=minutes)).isoformat()


GRAPH = {
    "claim": {"id": CLAIM, "title": "自然数加零的对称性", "status": "open", "domain": "lean-core", "statement_id": STATEMENT},
    "focus_id": f"lean:{EVID}",
    "nodes": [
        {"id": f"goal:{CLAIM}", "kind": "goal", "title": "原命题", "status": "open", "domain": "lean-core",
         "formula": "∀ n, n = 0 + n", "formalization": "∀ n : Nat, n = 0 + n",
         "reference": {"claim_id": CLAIM, "statement_id": STATEMENT}},
        {"id": f"item:{H1}", "kind": "hypothesis", "title": "假设 H₁", "status": "confirmed", "domain": "lean-core",
         "formalization": "∀ n : Nat, 0 + n = n", "reference": {"basis_version_id": H1, "version": 1}},
        {"id": f"item:{LIB}", "kind": "library", "title": "Nat.add_zero", "citation": "Nat.add_zero", "status": "available",
         "formalization": "∀ n : Nat, n + 0 = n", "reference": {"basis_version_id": LIB, "version": 1}},
        {"id": f"local-lemma:{ROUTE}", "kind": "reduction", "title": "Checked reduction 1", "status": "planned",
         "formalization": "(∀ n, 0 + n = n) → (∀ n, n + 0 = n) → ∀ n, n = 0 + n", "reference": {"lemma_id": ROUTE}},
        {"id": f"obligation:{ROUTE}:1", "kind": "obligation", "title": "Proof obligation 1", "status": "verified",
         "proposition": "∀ n, 0 + n = n", "formalization": "∀ n : Nat, 0 + n = n",
         "reference": {"implication_id": ROUTE, "position": 1}},
        {"id": f"obligation:{ROUTE}:2", "kind": "obligation", "title": "Proof obligation 2", "status": "open",
         "proposition": "∀ n, n + 0 = n", "formalization": "∀ n : Nat, n + 0 = n",
         "reference": {"implication_id": ROUTE, "position": 2}},
        {"id": f"lean:{EVID}", "kind": "evidence", "title": "Lean goal", "status": "awaiting_review",
         "verification_status": "verified", "citation": "FirstProof.reverseZeroAdd",
         "formalization": "theorem reverseZeroAdd (h : ∀ n : Nat, 0 + n = n) : ∀ n : Nat, n = 0 + n",
         "reference": {"check_id": EVID, "evidence_id": "77777777-7777-4777-8777-777777777777", "sha256": "9c41e0aa" * 8}},
    ],
    "edges": [
        {"source": f"lean:{EVID}", "target": f"goal:{CLAIM}", "kind": "actual"},
        {"source": f"local-lemma:{ROUTE}", "target": f"goal:{CLAIM}", "kind": "planned"},
        {"source": f"obligation:{ROUTE}:1", "target": f"local-lemma:{ROUTE}", "kind": "planned"},
        {"source": f"obligation:{ROUTE}:2", "target": f"local-lemma:{ROUTE}", "kind": "planned"},
        {"source": f"item:{H1}", "target": f"lean:{EVID}", "kind": "actual"},
        {"source": f"item:{H1}", "target": f"obligation:{ROUTE}:1", "kind": "actual"},
        {"source": f"item:{LIB}", "target": f"obligation:{ROUTE}:2", "kind": "scope"},
    ],
    "blockers": [{"node_id": f"obligation:{ROUTE}:2", "kind": "open_obligation", "reason": "无法从已确认的前提推出 ∀ n, n + 0 = n"}],
}
O2 = f"obligation:{ROUTE}:2"
ACTIVITY = {
    "claim_id": CLAIM,
    "activity": [
        {"id": "job:1", "kind": "model", "state": "running", "text": "Model proposal leased to a worker.", "at": at(3),
         "node_id": O2, "fallback_node_id": f"goal:{CLAIM}", "provider": "codex-cli", "model": "gpt-5-codex"},
        {"id": "job:2", "kind": "model", "state": "running", "text": "Model proposal leased to a worker.", "at": at(2),
         "node_id": O2, "fallback_node_id": f"goal:{CLAIM}", "provider": "codex-cli", "model": "gpt-5-codex"},
        {"id": "job:3", "kind": "lean", "state": "queued", "text": "Lean verification (local lemma) queued.", "at": at(1),
         "node_id": f"local-lemma:{ROUTE}", "fallback_node_id": f"goal:{CLAIM}"},
        {"id": "model:a:method", "kind": "agent_feedback", "state": "recorded", "at": at(9), "node_id": O2,
         "fallback_node_id": f"goal:{CLAIM}", "provider": "codex-cli", "model": "gpt-5-codex",
         "text": "证明义务 2 是 ∀ n, n + 0 = n。已确认的前提只有 h₁：0 + n = n，推不出 n + 0 = n。我先直接引用 Nat.add_zero 写一份候选证明。"},
        {"id": "job:0", "kind": "lean", "state": "failed", "text": "Lean verification failed. unproved goal.", "at": at(8),
         "node_id": O2, "fallback_node_id": f"goal:{CLAIM}"},
        {"id": "model:a:reply", "kind": "agent_feedback", "state": "blocked", "at": at(6), "node_id": O2,
         "fallback_node_id": f"goal:{CLAIM}", "provider": "codex-cli", "model": "gpt-5-codex",
         "text": "没通过，因为 Nat.add_zero 不在这个课题已确认的依据里。可以把 n + 0 = n 登记为新假设；也可以把 Nat.add_zero 加入依据，再从定理库重放。"},
    ],
    "guidance": [{"id": "g1", "text": "优先复用定理库里的加法引理。", "audience": "local", "node_id": O2,
                  "created_at": at(11), "submitted_at": at(9)}],
    "guidance_channels": ["local", "codex"],
}
ATLAS = {"claims": []}
for index, (title, size) in enumerate([("自然数加零的对称性", 7), ("单射映射的复合", 40), ("连续自然数之积的奇偶性", 55),
                                        ("固定网络最大流", 30), ("Formalpedia 快照", 60)]):
    cid = CLAIM if index == 0 else f"aaaaaaaa-0000-4000-8000-00000000000{index}"
    nodes = [{"id": f"n{index}:{i}", "kind": "goal" if i == 0 else "local_lemma"} for i in range(size)]
    edges = [[f"n{index}:{i}", f"n{index}:{(i - 1) // 3}"] for i in range(1, size)]
    if index == 0:
        nodes = [{"id": n["id"], "kind": n["kind"]} for n in GRAPH["nodes"]]
        edges = [[e["source"], e["target"]] for e in GRAPH["edges"]]
    if index == 0:
        nodes = [{"id": n["id"], "kind": n["kind"], "status": n["status"], **({"title": n["citation"]} if n["kind"] == "library" else {})} for n in GRAPH["nodes"]]
    elif index == 2:
        nodes.append({"id": "lib:x", "kind": "library", "title": "Nat.add_zero"})
        nodes.append({"id": "lib:y", "kind": "library", "title": "Nat.mul_comm"})
    ATLAS["claims"].append({"awaiting_review": [1, 0, 2, 0, 0][index], "blocked": [1, 2, 0, 0, 0][index], "id": cid, "title": title, "domain": "lean-core" if index < 3 else ("flow" if index == 3 else "定理库"),
                            "status": "open", "nodes": nodes, "edges": edges})
CLAIMS = [{"id": c["id"], "title": c["title"], "status": s, "domain": c["domain"]}
          for c, s in zip(ATLAS["claims"], ["open", "open", "proved", "needs_recheck", "available"])]
IMPACT = {"basis_version_id": H1, "affected": [
    {"id": f"lean:{EVID}", "kind": "evidence", "relation": "direct", "status": "would_need_recheck", "title": "reverseZeroAdd"},
    {"id": f"obligation:{ROUTE}:1", "kind": "local_lemma", "relation": "direct", "status": "would_need_recheck", "title": "证明义务 1"}],
    "scope": [{"id": f"local-lemma:{ROUTE}", "kind": "obligation", "title": "归约 1"}]}




INBOX = {"items": [
    {"kind": "review_evidence", "item_id": "77777777-7777-4777-8777-777777777777", "claim_id": CLAIM,
     "claim_title": "自然数加零的对称性", "waiting_since": at(30), "question": "Approve or return this verified result with a reason.",
     "detail": {"kind": "lean", "sha256": "9c41e0aa" * 8,
                "declaration": "FirstProof.reverseZeroAdd", "target": "goal"}, "priority": 2},
    {"kind": "resolve_clarification", "item_id": "88888888-8888-4888-8888-888888888888", "claim_id": "aaaaaaaa-0000-4000-8000-000000000001",
     "claim_title": "单射映射的复合", "waiting_since": at(120), "question": "Which composition order is intended?",
     "detail": {"question_count": 1}, "priority": 1},
]}
LIBRARY = {"items": [
    {"id": LIB, "logical_key": "formalpedia:Nat.add_zero", "version": 1, "origin": "formalpedia", "admitted": True,
     "needs_recheck": False, "retired": False, "created_at": at(3000), "kind": "theorem", "natural": "Adding zero on the right leaves a natural number unchanged.",
     "declaration": "Nat.add_zero", "lean": "∀ n : Nat, n + 0 = n", "formula": None,
     "used_by": [{"id": CLAIM, "title": "自然数加零的对称性"}]},
    {"id": H1, "logical_key": "intake:h1", "version": 1, "origin": "human", "admitted": True, "needs_recheck": False,
     "retired": False, "created_at": at(2000), "kind": "hypothesis", "natural": "Adding zero on the left leaves a natural number unchanged.",
     "declaration": None, "lean": "∀ n : Nat, 0 + n = n", "formula": None, "used_by": [{"id": CLAIM, "title": "自然数加零的对称性"}]},
    {"id": "99999999-9999-4999-8999-999999999999", "logical_key": "formalpedia:Nat.mul_comm", "version": 2, "origin": "formalpedia",
     "admitted": True, "needs_recheck": True, "retired": False, "created_at": at(5000), "kind": "theorem",
     "natural": "Multiplication of natural numbers is commutative.", "declaration": "Nat.mul_comm", "lean": "∀ n m : Nat, n * m = m * n",
     "formula": None, "used_by": []},
]}
TEAM = {
    "jobs": [
        {"id": "j1", "claim_id": CLAIM, "claim_title": "自然数加零的对称性", "kind": "prove", "state": "running", "provider": "codex-cli",
         "model": "gpt-5-codex", "model_job": True, "target": None, "node_id": O2, "enqueued_at": at(4), "submitted_at": None,
         "running_since": at(3), "execution_ms": None, "failure_code": None},
        {"id": "j2", "claim_id": CLAIM, "claim_title": "自然数加零的对称性", "kind": "prove", "state": "running", "provider": "codex-cli",
         "model": "gpt-5-codex", "model_job": True, "target": None, "node_id": O2, "enqueued_at": at(3), "submitted_at": None,
         "running_since": at(2), "execution_ms": None, "failure_code": None},
        {"id": "j3", "claim_id": CLAIM, "claim_title": "自然数加零的对称性", "kind": "verify", "state": "queued", "provider": None,
         "model": None, "model_job": False, "target": "local_lemma", "node_id": None, "enqueued_at": at(1), "submitted_at": None,
         "running_since": None, "execution_ms": None, "failure_code": None},
        {"id": "j0", "claim_id": CLAIM, "claim_title": "自然数加零的对称性", "kind": "verify", "state": "succeeded", "provider": None,
         "model": None, "model_job": False, "target": "goal", "node_id": None, "enqueued_at": at(40), "submitted_at": at(38),
         "running_since": None, "execution_ms": 5308, "failure_code": None},
    ],
    "agents": [{"provider": "codex-cli", "model": "gpt-5-codex", "runs": 5, "last_at": at(9), "failures": 1,
                "average_ms": "252000", "input_tokens": "18734", "output_tokens": "1550", "cost_usd": "0.42"}],
    "verifier": {"runs": 9, "last_at": at(8), "failures": 2, "average_ms": "38000"},
    "services": [
        {"provider": "codex-cli", "transport": "cli", "ready": True, "via": "command", "detail": "codex", "runs": 5},
        {"provider": "anthropic", "transport": "messages", "ready": False, "via": "environment", "detail": "ANTHROPIC_API_KEY", "runs": 0},
    ],
    "machine": {"name": "fixture-host", "system": "Darwin", "arch": "arm64", "python": "3.14.4"},
    "budget": {"policy": None, "month": {"period": "2026-11-01", "reserved_microusd": 420000, "reserved_usd": "0.420000"}, "routes": []},
}
PROGRESS = {"policies": [
    {"id": "pol-1", "claim_id": CLAIM, "claim_title": "自然数加零的对称性", "state": "waiting_review",
     "policy": {"metric": "exact-closure", "max_rounds": 0, "budget_seconds": 0, "lean": {"model_rounds": 2}},
     "proposed_at": at(600), "approved_by": "Fixture reviewer", "approved_at": at(590), "stopped_at": None, "stop_reason": None,
     "last_run_at": at(30), "last_status": "awaiting_evidence_review", "last_reason": None},
]}
THREADS = {"threads": [
    {"claim_id": CLAIM, "claim_title": "自然数加零的对称性", "node_id": O2,
     "node": {"id": O2, "kind": "obligation", "title": "Proof obligation 2", "proposition": "∀ n, n + 0 = n", "status": "open"},
     "live": 2, "models": ["gpt-5-codex"], "last_at": at(2),
     "events": [e for e in ACTIVITY["activity"] if e["node_id"] == O2]},
]}
DOCTOR = {"capabilities": {"offline": {"title": "Offline briefs and replay", "ready": True}},
          "checks": [{"id": "python", "group": "Basics", "title": "Python", "status": "ok", "detail": "3.14.4", "fix": ""},
                     {"id": "lean-toolchain", "group": "Lean", "title": "Lean 4.19.0", "status": "ok", "detail": "found", "fix": ""}]}
INITIAL_RELEASE_STATE = {"installed_version": "1.9.0", "automatic": False, "last_checked_at": None,
                         "status": "not_checked", "available": None}
RELEASE_STATE = INITIAL_RELEASE_STATE.copy()
AVAILABLE_RELEASE = {"version": "1.10.0", "url": "https://github.com/SAFuDarren/Trellin/releases/tag/v1.10.0",
                     "dismissed": False}
REVIEWS = []
PROJECT_CANVAS = {"positions": {}, "links": []}
INITIAL_INBOX = [dict(item) for item in INBOX["items"]]
INITIAL_GUIDANCE = [dict(item) for item in ACTIVITY["guidance"]]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, body, kind, status=200):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = urlsplit(self.path)
        if route.path == "/" or route.path.startswith("/assets/"):
            target = APP / ("index.html" if route.path == "/" else route.path[1:])
            if target.is_file() and target.suffix in TYPES and APP in target.resolve().parents:
                self.send(target.read_bytes(), TYPES[target.suffix])
            else:
                self.send(b'{"error": "missing"}', "application/json", 404)
            return
        if route.path == "/api/release-updates":
            self.release_updates(route)
            return
        if route.path == "/api/project-canvas":
            self.send(json.dumps(PROJECT_CANVAS).encode(), "application/json")
            return
        if route.path == "/api/project-canvas/claims":
            claims = [{**claim, "obligations": {"verified": int(claim["id"] == CLAIM),
                                                "total": 2 if claim["id"] == CLAIM else 0}}
                      for claim in CLAIMS]
            self.send(json.dumps({"claims": claims}, ensure_ascii=False).encode(),
                      "application/json; charset=utf-8")
            return
        query = parse_qs(route.query)
        if route.path == "/api/proof-task" and len(query.get("claim", [])) == 1:
            identifier = query["claim"][0]
            claim = next((item for item in CLAIMS if item["id"] == identifier), None)
            task = {
                "claim_id": identifier, "title": claim["title"] if claim else "Other claim",
                "state": "waiting", "blocker": "proof_task.unresolved",
                "can_start": False, "can_stop": False, "local_model_allowed": False,
                "scope": {"token": None, "proposition": None, "basis": [], "omitted_basis": 0},
                "decisions": [], "policy": None, "receipt": None, "adopted": False,
            }
            self.send(json.dumps(task, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            return
        data = {"/api/claims": {"claims": CLAIMS, "claim_id": None}, "/api/graph": GRAPH, "/api/activity": ACTIVITY,
                "/api/atlas": ATLAS, "/api/impact": IMPACT, "/api/inbox": INBOX, "/api/library": LIBRARY,
                "/api/model-connections": {"connections": []},
                "/api/model-connections/native": {"providers": [{"provider": "codex-cli", "status": "signed_in"}]},
                "/api/team-agents": {"agents": []},
                "/api/conversations": {"threads": []}, "/api/agent-work": {"available": True, "tasks": []},
                "/api/team": TEAM, "/api/progress": PROGRESS, "/api/threads": THREADS, "/api/doctor": DOCTOR}.get(route.path)
        if data is None:
            self.send(b'{"error": "Page unavailable."}', "application/json", 404)
            return
        if route.path == "/api/graph" and query.get("claim", [CLAIM])[0] != CLAIM:
            data = {**GRAPH, "claim": {**GRAPH["claim"], "id": query["claim"][0], "title": "Other claim"}}
        if route.path == "/api/activity" and query.get("claim", [CLAIM])[0] != CLAIM:
            data = {**ACTIVITY, "claim_id": query["claim"][0]}
        self.send(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_POST(self):
        route = urlsplit(self.path)
        if route.path == "/api/release-updates":
            self.release_updates(route)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if self.path == "/__reset":  # test-only: restore the inbox between end-to-end tests
            INBOX["items"] = [dict(item) for item in INITIAL_INBOX]
            REVIEWS.clear()
            ACTIVITY["guidance"][:] = [dict(item) for item in INITIAL_GUIDANCE]
            RELEASE_STATE.update(INITIAL_RELEASE_STATE)
            PROJECT_CANVAS["positions"].clear()
            PROJECT_CANVAS["links"].clear()
            self.send(b'{"ok": true}', "application/json")
            return
        if route.path == "/api/project-canvas":
            action = body["action"]
            if action == "move":
                PROJECT_CANVAS["positions"][body["id"]] = {"x": body["x"], "y": body["y"]}
            elif action in {"link", "unlink"}:
                source, target = sorted((body["source"], body["target"]))
                link = {"source": source, "target": target}
                if action == "link" and link not in PROJECT_CANVAS["links"]:
                    PROJECT_CANVAS["links"].append(link)
                elif action == "unlink" and link in PROJECT_CANVAS["links"]:
                    PROJECT_CANVAS["links"].remove(link)
            else:
                self.send(b'{"error": "project_canvas.invalid"}', "application/json", 400)
                return
            self.send(json.dumps(PROJECT_CANVAS).encode(), "application/json")
            return
        if self.path == "/api/review":
            REVIEWS.append(body)
            INBOX["items"] = [item for item in INBOX["items"] if item["item_id"] != body["evidence_id"]]
            self.send(b'{"id": "review-1"}', "application/json")
            return
        ACTIVITY["guidance"].append({"id": "g" + str(len(ACTIVITY["guidance"]) + 1), "text": body["text"],
                                     "audience": body.get("audience", "local"), "node_id": body["node_id"],
                                     "created_at": datetime.now(timezone.utc).isoformat()})
        self.send(b'{"ok": true}', "application/json")

    def release_updates(self, route):
        host, port = self.server.server_address
        local_host = f"{host}:{port}"
        origin = [f"http://{local_host}"]
        allowed_origins = (origin,) if self.command == "POST" else (None, origin)
        if (self.headers.get_all("Host") != [local_host] or self.headers.get_all("Origin") not in allowed_origins
                or self.headers.get_all("X-RM-Session") != ["fixture"]):
            self.send(b'{"error": "Open the local fixture session."}', "application/json", 403)
            return
        if route.query:
            self.send(b'{"error": "This endpoint does not take parameters."}', "application/json", 400)
            return
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            action = body.get("action")
            if action == "check" or (action == "auto_check" and RELEASE_STATE["automatic"]
                                     and RELEASE_STATE["last_checked_at"] is None):
                release = RELEASE_STATE["available"] or AVAILABLE_RELEASE.copy()
                RELEASE_STATE.update(last_checked_at=int(NOW.timestamp()), status="available", available=release)
            elif action == "automatic":
                RELEASE_STATE["automatic"] = body["enabled"]
            elif action == "dismiss" and RELEASE_STATE["available"] and body["version"] == AVAILABLE_RELEASE["version"]:
                RELEASE_STATE["available"]["dismissed"] = True
        self.send(json.dumps(RELEASE_STATE).encode(), "application/json; charset=utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=4174)
    port = parser.parse_args().port
    print(f"Workbench fixture: http://127.0.0.1:{port}/#session=fixture", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
