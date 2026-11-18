# SPDX-License-Identifier: Apache-2.0
"""Serve the workbench with a sample project on Fermat's Last Theorem, for README screenshots.

Usage: python tests/fixtures/workbench_demo_fermat.py [--port 4176], then open /#session=demo

Sample data, not a real run. What it shows as verified matches Mathlib (revision pinned in lean/):
`FermatLastTheorem.of_odd_primes` reduces the theorem to odd prime exponents, and `fermatLastTheoremFour`
and `fermatLastTheoremThree` prove the cases n = 4 and n = 3. Primes p >= 5 stay open: the route through
the Frey curve rests on the modularity theorem and Ribet's theorem, recorded as one assumption because they
are not in Mathlib. Nothing here claims a proof of the general case.
"""

import argparse
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workbench_fixture as fx  # noqa: E402

at = fx.at
CLAIM = "f1000000-0000-4000-8000-000000000001"
STATEMENT = "f1000000-0000-4000-8000-000000000002"
RED1 = "f1000000-0000-4000-8000-000000000003"
RED2 = "f1000000-0000-4000-8000-000000000004"
EVID = "f1000000-0000-4000-8000-000000000005"
EVIDENCE_ID = "f1000000-0000-4000-8000-000000000006"
FLT4 = "f1000000-0000-4000-8000-000000000007"
FLT3 = "f1000000-0000-4000-8000-000000000008"
MOD = "f1000000-0000-4000-8000-000000000009"
TITLE = "Fermat's Last Theorem"
DOMAIN = "number theory"

GOAL = f"goal:{CLAIM}"
REDUCTION = f"local-lemma:{RED1}"
FREY_ROUTE = f"local-lemma:{RED2}"
P3 = f"obligation:{RED1}:1"
P5 = f"obligation:{RED1}:2"
SEMISTABLE = f"obligation:{RED2}:1"
LEVEL_TWO = f"obligation:{RED2}:2"
LEAN = f"lean:{EVID}"

GRAPH = {
    "claim": {"id": CLAIM, "title": TITLE, "status": "open", "domain": DOMAIN, "statement_id": STATEMENT},
    "focus_id": SEMISTABLE,
    "nodes": [
        {"id": GOAL, "kind": "goal", "title": TITLE, "status": "open", "domain": DOMAIN,
         "formula": "∀ n ≥ 3, aⁿ + bⁿ ≠ cⁿ for nonzero naturals a, b, c",
         "formalization": "FermatLastTheorem  -- ∀ n ≥ 3, FermatLastTheoremFor n",
         "reference": {"claim_id": CLAIM, "statement_id": STATEMENT}},
        {"id": REDUCTION, "kind": "reduction", "title": "Reduce to odd prime exponents", "status": "route_checked",
         "formalization": "(∀ p : ℕ, p.Prime → Odd p → FermatLastTheoremFor p) → FermatLastTheorem",
         "reference": {"lemma_id": RED1}},
        {"id": LEAN, "kind": "evidence", "title": "Lean check", "status": "awaiting_review",
         "verification_status": "verified", "citation": "FermatLastTheorem.of_odd_primes",
         "formalization": "theorem FermatLastTheorem.of_odd_primes\n    (hprimes : ∀ p : ℕ, Nat.Prime p → Odd p → FermatLastTheoremFor p) :\n    FermatLastTheorem",
         "reference": {"check_id": EVID, "evidence_id": EVIDENCE_ID, "sha256": "4e1f9b07" * 8}},
        {"id": f"item:{FLT4}", "kind": "library", "title": "fermatLastTheoremFour", "citation": "fermatLastTheoremFour",
         "status": "available", "formalization": "FermatLastTheoremFor 4", "reference": {"basis_version_id": FLT4, "version": 1}},
        {"id": P3, "kind": "obligation", "title": "Exponent 3", "status": "verified",
         "proposition": "a³ + b³ ≠ c³", "formalization": "FermatLastTheoremFor 3",
         "reference": {"implication_id": RED1, "position": 1}},
        {"id": f"item:{FLT3}", "kind": "library", "title": "fermatLastTheoremThree", "citation": "fermatLastTheoremThree",
         "status": "available", "formalization": "FermatLastTheoremFor 3", "reference": {"basis_version_id": FLT3, "version": 1}},
        {"id": P5, "kind": "obligation", "title": "Primes p ≥ 5", "status": "awaiting_obligations",
         "proposition": "∀ p prime, p ≥ 5 → aᵖ + bᵖ ≠ cᵖ",
         "formalization": "∀ p : ℕ, p.Prime → 5 ≤ p → FermatLastTheoremFor p",
         "reference": {"implication_id": RED1, "position": 2}},
        {"id": FREY_ROUTE, "kind": "reduction", "title": "Frey curve route", "status": "planned",
         "formalization": "(semistable Frey curve) → (no weight-2 newform of level 2) → FermatLastTheoremFor p",
         "reference": {"lemma_id": RED2}},
        {"id": SEMISTABLE, "kind": "obligation", "title": "The Frey curve is semistable", "status": "open",
         "proposition": "E : y² = x(x − aᵖ)(x + bᵖ) has multiplicative reduction at every bad prime",
         "formalization": "∀ a b c : ℤ, IsPrimitiveSolution p a b c → (freyCurve p a b c).IsSemistable",
         "reference": {"implication_id": RED2, "position": 1}},
        {"id": LEVEL_TWO, "kind": "obligation", "title": "No newform of level 2", "status": "open",
         "proposition": "dim S₂(Γ₀(2)) = 0", "formalization": "Module.finrank ℂ (CuspForm (Gamma0 2) 2) = 0",
         "reference": {"implication_id": RED2, "position": 2}},
        {"id": f"item:{MOD}", "kind": "hypothesis", "title": "Modularity and level lowering", "status": "confirmed",
         "formalization": "(∀ E : EllipticCurve ℚ, E.IsSemistable → E.IsModular) ∧\n"
                          "  ∀ p a b c, IsPrimitiveSolution p a b c →\n"
                          "    ∃ f : Newform (Gamma0 2) 2, f.Congruent (freyCurve p a b c) p",
         "reference": {"basis_version_id": MOD, "version": 1}},
    ],
    "edges": [
        {"source": REDUCTION, "target": GOAL, "kind": "planned"},
        {"source": LEAN, "target": REDUCTION, "kind": "actual"},
        {"source": f"item:{FLT4}", "target": LEAN, "kind": "actual"},
        {"source": P3, "target": REDUCTION, "kind": "planned"},
        {"source": f"item:{FLT3}", "target": P3, "kind": "actual"},
        {"source": P5, "target": REDUCTION, "kind": "planned"},
        {"source": FREY_ROUTE, "target": P5, "kind": "planned"},
        {"source": SEMISTABLE, "target": FREY_ROUTE, "kind": "planned"},
        {"source": LEVEL_TWO, "target": FREY_ROUTE, "kind": "planned"},
        {"source": f"item:{MOD}", "target": FREY_ROUTE, "kind": "scope"},
    ],
    "blockers": [{"node_id": P5, "kind": "open_obligation",
                  "reason": "Waits on the Frey curve route; the modularity and level-lowering steps are not in Mathlib."}],
}

ACTIVITY = {
    "claim_id": CLAIM,
    "activity": [
        {"id": "job:1", "kind": "model", "state": "running", "text": "Model proposal leased to a worker.", "at": at(3),
         "node_id": SEMISTABLE, "fallback_node_id": GOAL, "provider": "codex-cli", "model": "gpt-5-codex"},
        {"id": "job:2", "kind": "model", "state": "running", "text": "Model proposal leased to a worker.", "at": at(2),
         "node_id": SEMISTABLE, "fallback_node_id": GOAL, "provider": "codex-cli", "model": "gpt-5-codex"},
        {"id": "job:3", "kind": "lean", "state": "queued", "text": "Lean verification (local lemma) queued.", "at": at(1),
         "node_id": LEVEL_TWO, "fallback_node_id": GOAL},
        {"id": "model:a:method", "kind": "agent_feedback", "state": "recorded", "at": at(14), "node_id": SEMISTABLE,
         "fallback_node_id": GOAL, "provider": "codex-cli", "model": "gpt-5-codex",
         "text": "Plan: normalise the solution so that a ≡ −1 (mod 4) and b is even. Then the minimal model of "
                 "y² = x(x − aᵖ)(x + bᵖ) has discriminant 2⁻⁸(abc)²ᵖ, and every odd bad prime divides abc "
                 "exactly once in the conductor."},
        {"id": "job:0", "kind": "lean", "state": "failed", "at": at(11), "node_id": SEMISTABLE, "fallback_node_id": GOAL,
         "text": "Lean verification failed: `Nat.Coprime a b` is not available at this step."},
        {"id": "model:a:reply", "kind": "agent_feedback", "state": "recorded", "at": at(8), "node_id": SEMISTABLE,
         "fallback_node_id": GOAL, "provider": "codex-cli", "model": "gpt-5-codex",
         "text": "The check failed because the solution was not yet primitive. I added a step that divides out "
                 "gcd(a, b, c) first, and two attempts on the reduction at 2 are running now."},
    ],
    "guidance": [{"id": "g2", "text": "Keep this as an assumption until the Lean FLT project (Imperial College London) "
                                       "proves modularity and level lowering; then replace it with the library theorems.",
                   "audience": "local", "node_id": f"item:{MOD}", "created_at": at(40), "submitted_at": at(40)},
                  {"id": "g1", "text": "Reduce to a primitive solution before building the curve; the descent for n = 4 does the same.",
                  "audience": "local", "node_id": SEMISTABLE, "created_at": at(13), "submitted_at": at(12)}],
    "guidance_channels": ["local", "codex"],
}

OTHER = [
    ("f2000000-0000-4000-8000-000000000001", "Regular primes (Kummer)", DOMAIN, "open", 34, 0, 1),
    ("f2000000-0000-4000-8000-000000000002", "Sums of two squares", DOMAIN, "proved", 22, 0, 0),
    ("f2000000-0000-4000-8000-000000000003", "Mason–Stothers theorem", "polynomials", "proved", 28, 0, 0),
    ("f2000000-0000-4000-8000-000000000004", "Polynomial Fermat", "polynomials", "needs_recheck", 18, 1, 0),
]
ATLAS = {"claims": [{
    "id": CLAIM, "title": TITLE, "domain": DOMAIN, "status": "open", "awaiting_review": 1, "blocked": 1,
    "nodes": [{"id": n["id"], "kind": n["kind"], "status": n["status"],
               **({"title": n["citation"]} if n["kind"] == "library" else {})} for n in GRAPH["nodes"]],
    "edges": [[e["source"], e["target"]] for e in GRAPH["edges"]],
}]}
for cid, title, domain, status, size, review, blocked in OTHER:
    nodes = [{"id": f"{cid}:{i}", "kind": "goal" if i == 0 else "local_lemma"} for i in range(size)]
    edges = [[f"{cid}:{i}", f"{cid}:{(i - 1) // 3}"] for i in range(1, size)]
    ATLAS["claims"].append({"id": cid, "title": title, "domain": domain, "status": status, "awaiting_review": review,
                            "blocked": blocked, "nodes": nodes, "edges": edges})
CLAIMS = [{"id": c["id"], "title": c["title"], "status": c["status"], "domain": c["domain"]} for c in ATLAS["claims"]]

IMPACT = {"basis_version_id": MOD, "affected": [
    {"id": FREY_ROUTE, "kind": "local_lemma", "relation": "direct", "status": "would_need_recheck", "title": "Frey curve route"},
    {"id": P5, "kind": "obligation", "relation": "indirect", "status": "would_need_recheck", "title": "Primes p ≥ 5"}],
    "scope": [{"id": REDUCTION, "kind": "obligation", "title": "Reduce to odd prime exponents"}]}

INBOX = {"items": [
    {"kind": "review_evidence", "item_id": EVIDENCE_ID, "claim_id": CLAIM, "claim_title": TITLE, "waiting_since": at(25),
     "question": "Approve or return this verified result with a reason.",
     "detail": {"kind": "lean", "sha256": "4e1f9b07" * 8}, "priority": 2},
    {"kind": "resolve_clarification", "item_id": "f3000000-0000-4000-8000-000000000001", "claim_id": OTHER[0][0],
     "claim_title": OTHER[0][1], "waiting_since": at(95),
     "question": "Should “regular” follow Kummer's definition: p does not divide the class number of ℚ(ζₚ)?",
     "detail": {"question_count": 1}, "priority": 1},
]}

USED = [{"id": CLAIM, "title": TITLE}]
LIBRARY = {"items": [
    {"id": FLT4, "logical_key": "mathlib:fermatLastTheoremFour", "version": 1, "origin": "formalpedia", "admitted": True,
     "needs_recheck": False, "retired": False, "created_at": at(9000), "kind": "theorem",
     "natural": "a⁴ + b⁴ = c⁴ has no solution in nonzero naturals (Fermat's infinite descent).",
     "declaration": "fermatLastTheoremFour", "lean": "FermatLastTheoremFor 4", "formula": None, "used_by": USED},
    {"id": FLT3, "logical_key": "mathlib:fermatLastTheoremThree", "version": 1, "origin": "formalpedia", "admitted": True,
     "needs_recheck": False, "retired": False, "created_at": at(9000), "kind": "theorem",
     "natural": "a³ + b³ = c³ has no solution in nonzero naturals (Euler, via the Eisenstein integers).",
     "declaration": "fermatLastTheoremThree", "lean": "FermatLastTheoremFor 3", "formula": None, "used_by": USED},
    {"id": MOD, "logical_key": "intake:modularity-and-level-lowering", "version": 1, "origin": "human", "admitted": True,
     "needs_recheck": False, "retired": False, "created_at": at(4000), "kind": "hypothesis",
     "natural": "Semistable elliptic curves over ℚ are modular (Wiles; Taylor–Wiles, 1995), and a modular Frey curve "
                "gives a weight-2 newform of level 2 (Ribet, 1990). Recorded as an assumption: not in Mathlib.",
     "declaration": None, "lean": None, "formula": None, "used_by": USED},
    {"id": "f4000000-0000-4000-8000-000000000001", "logical_key": "mathlib:Polynomial.flt", "version": 1,
     "origin": "formalpedia", "admitted": True, "needs_recheck": True, "retired": False, "created_at": at(9000),
     "kind": "theorem", "natural": "Fermat's Last Theorem for polynomials, from the Mason–Stothers theorem.",
     "declaration": "Polynomial.flt", "lean": "Polynomial.flt", "formula": None,
     "used_by": [{"id": OTHER[3][0], "title": OTHER[3][1]}]},
]}


def job(ident, kind, state, node, minutes, model=True, **extra):
    return {"id": ident, "claim_id": CLAIM, "claim_title": TITLE, "kind": kind, "state": state,
            "provider": "codex-cli" if model else None, "model": "gpt-5-codex" if model else None, "model_job": model,
            "target": None if model else "local_lemma", "node_id": node, "enqueued_at": at(minutes + 1),
            "submitted_at": None, "running_since": at(minutes) if state == "running" else None,
            "execution_ms": None, "failure_code": None, **extra}


TEAM = {**fx.TEAM, "jobs": [
    job("j1", "prove", "running", SEMISTABLE, 3), job("j2", "prove", "running", SEMISTABLE, 2),
    job("j3", "verify", "queued", LEVEL_TWO, 1, model=False),
    job("j0", "verify", "succeeded", REDUCTION, 25, model=False, execution_ms=41200, submitted_at=at(24)),
]}
PROGRESS = {"policies": [
    {"id": "pol-1", "claim_id": CLAIM, "claim_title": TITLE, "state": "running",
     "policy": {"metric": "exact-closure", "max_rounds": 0, "budget_seconds": 0, "lean": {"model_rounds": 6}},
     "proposed_at": at(600), "approved_by": "Demo researcher", "approved_at": at(590), "stopped_at": None,
     "stop_reason": None, "last_run_at": at(3), "last_status": "running", "last_reason": None},
]}
THREADS = {"threads": [
    {"claim_id": CLAIM, "claim_title": TITLE, "node_id": SEMISTABLE,
     "node": {"id": SEMISTABLE, "kind": "obligation", "title": "The Frey curve is semistable",
              "proposition": GRAPH["nodes"][8]["proposition"], "status": "open"},
     "live": 2, "models": ["gpt-5-codex"], "last_at": at(2),
     "events": [e for e in ACTIVITY["activity"] if e["node_id"] == SEMISTABLE]},
]}

for name in ("CLAIM", "GRAPH", "ACTIVITY", "ATLAS", "CLAIMS", "IMPACT", "INBOX", "LIBRARY", "TEAM", "PROGRESS", "THREADS"):
    setattr(fx, name, globals()[name])
fx.INITIAL_INBOX = [dict(item) for item in INBOX["items"]]
fx.INITIAL_GUIDANCE = [dict(item) for item in ACTIVITY["guidance"]]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=4176)
    port = parser.parse_args().port
    print(f"Fermat demo: http://127.0.0.1:{port}/#session=demo", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), fx.Handler).serve_forever()
