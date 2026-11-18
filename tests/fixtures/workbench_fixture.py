# SPDX-License-Identifier: Apache-2.0
"""Serve the built React workbench with fixed API data for end-to-end tests and visual checks.

Usage: python tests/fixtures/workbench_fixture.py [--port 4174], then open /#session=fixture

The data is synthetic: five claims, one graph with running model work, a blocker, an assumption with an
impact preview, conversation records and guidance channels for both local models and Codex. Guidance
POSTs are kept in memory. No database, credentials or network access are involved.
"""

import argparse
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

APP = Path(__file__).resolve().parents[2] / "research_multiplier" / "workbench" / "app"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}
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




class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, body, kind, status=200):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'")
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
        query = parse_qs(route.query)
        data = {"/api/claims": {"claims": CLAIMS, "claim_id": CLAIM}, "/api/graph": GRAPH, "/api/activity": ACTIVITY,
                "/api/atlas": ATLAS, "/api/impact": IMPACT}.get(route.path)
        if data is None:
            self.send(b'{"error": "Page unavailable."}', "application/json", 404)
            return
        if route.path == "/api/graph" and query.get("claim", [CLAIM])[0] != CLAIM:
            data = {**GRAPH, "claim": {**GRAPH["claim"], "id": query["claim"][0], "title": "Other claim"}}
        if route.path == "/api/activity" and query.get("claim", [CLAIM])[0] != CLAIM:
            data = {**ACTIVITY, "claim_id": query["claim"][0]}
        self.send(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        ACTIVITY["guidance"].append({"id": "g" + str(len(ACTIVITY["guidance"]) + 1), "text": body["text"],
                                     "audience": body.get("audience", "local"), "node_id": body["node_id"],
                                     "created_at": datetime.now(timezone.utc).isoformat()})
        self.send(b'{"ok": true}', "application/json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=4174)
    port = parser.parse_args().port
    print(f"Workbench fixture: http://127.0.0.1:{port}/#session=fixture", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
