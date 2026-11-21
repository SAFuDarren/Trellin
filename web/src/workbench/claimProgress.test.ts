import { describe, expect, it } from "vitest";
import type { InboxItem, Job } from "../api/sections";
import { buildClaimProgress } from "./claimProgress";
import type { AtlasClaim } from "./types";

function claim(id: string, status = "open", nodes: AtlasClaim["nodes"] = []): AtlasClaim {
  return { id, title: id, domain: "lean-core", status, nodes, edges: [] };
}

function inbox(claim_id: string | null, kind: string, detail: InboxItem["detail"] = null): InboxItem {
  return { claim_id, kind, detail, item_id: kind, claim_title: null, question: "", waiting_since: "", priority: 1 };
}

function job(claim_id: string | null, kind: string, state: string, model_job = false): Job {
  return {
    claim_id,
    kind,
    state,
    model_job,
    id: kind,
    claim_title: null,
    provider: null,
    model: null,
    target: null,
    node_id: null,
    enqueued_at: "",
    submitted_at: null,
    running_since: null,
    execution_ms: null,
    failure_code: null,
  };
}

describe("claim progress", () => {
  it("counts recorded proof nodes once and never scores an input or stale proof as verified", () => {
    const atlas = [
      claim("math", "open", [
        { id: "g", kind: "goal", status: "open" },
        { id: "h", kind: "hypothesis", status: "approved" },
        { id: "f", kind: "library", status: "approved" },
        { id: "l1", kind: "local_lemma", status: "verified" },
        { id: "l2", kind: "local_lemma", status: "needs_recheck" },
        { id: "r", kind: "reduction", status: "goal_checked" },
        { id: "o", kind: "obligation", status: "open" },
        { id: "e", kind: "evidence", status: "approved" },
      ]),
    ];
    expect(buildClaimProgress({ atlas }).get("math")).toEqual({ nodes: { done: 3, total: 6 } });
    expect(buildClaimProgress({}).get("math")).toBeUndefined();
  });

  it("uses actionable review and decision records, ignoring unassigned items and graph placeholders", () => {
    const progress = buildClaimProgress({
      atlas: [
        { ...claim("history"), blocked: 3, awaiting_review: 2 },
        claim("interpretation", "awaiting_interpretation_review"),
      ],
      inbox: [
        inbox("evidence", "review_evidence", { kind: "counterexample" }),
        inbox("scope", "confirm_lean_statement"),
        inbox("changed-scope", "review_lean_scope_change"),
        inbox("premise", "decide_condition"),
        inbox("unknown", "other_review"),
        inbox(null, "checker_hold"),
        inbox(null, "clarify_formalization"),
        inbox(null, "confirm_intake"),
      ],
    });
    expect([...progress]).toEqual([
      ["interpretation", { state: "decision" }],
      ["evidence", { state: "review" }],
      ["scope", { state: "review" }],
      ["changed-scope", { state: "review" }],
      ["premise", { state: "decision" }],
    ]);
  });

  it("prioritizes action and recheck over active work and only names recorded proof work", () => {
    const progress = buildClaimProgress({
      atlas: [claim("decision", "needs_recheck"), claim("recheck", "needs_recheck")],
      inbox: [inbox("decision", "choose_conjecture"), inbox("review", "confirm_formalization")],
      jobs: [
        job("decision", "verify", "running"),
        job("review", "prove", "running", true),
        job("recheck", "verify", "running"),
        job("check", "verify", "running"),
        job("model", "prove", "running", true),
        job("queue", "verify", "queued"),
        job("expired", "verify", "lease_expired"),
        job("other", "unrelated", "running"),
        job(null, "verify", "running"),
      ],
    });
    expect([...progress]).toEqual([
      ["decision", { state: "decision" }],
      ["recheck", { state: "recheck" }],
      ["review", { state: "review" }],
      ["check", { state: "verifying" }],
      ["model", { state: "running" }],
      ["queue", { state: "queued" }],
    ]);
  });

  it("reports exact readiness, running and decision gates only from current proof task responses", () => {
    const state = (proofTask: NonNullable<Parameters<typeof buildClaimProgress>[0]["proofTask"]>) =>
      buildClaimProgress({ proofTask }).get(proofTask.claim_id)?.state;
    expect(state({ claim_id: "c", state: "ready", can_start: true })).toBe("ready");
    expect(state({ claim_id: "c", state: "authorized", can_start: true })).toBe("ready");
    expect(state({ claim_id: "c", state: "running" })).toBe("running");
    expect(state({ claim_id: "c", state: "blocked", blocker: "proof_task.decision_needed" })).toBe("decision");
    expect(state({ claim_id: "c", state: "ready", can_start: false })).toBeUndefined();
    expect(state({ claim_id: "c", state: "authorized" })).toBeUndefined();
    expect(state({ claim_id: "c", state: "blocked", blocker: "proof_task.lean_unconfirmed" })).toBeUndefined();
    expect(state({ claim_id: "c", state: "stopped", can_start: true })).toBeUndefined();
  });
});
