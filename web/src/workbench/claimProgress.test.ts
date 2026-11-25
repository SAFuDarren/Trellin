import { describe, expect, it } from "vitest";
import type { ProjectClaim } from "../api/projectCanvas";
import type { InboxItem, Job } from "../api/sections";
import { buildClaimProgress } from "./claimProgress";

function claim(id: string, status = "open", obligations?: ProjectClaim["obligations"]): ProjectClaim {
  return { id, title: id, domain: "lean-core", status, obligations };
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
  it("uses exact project counts and distinguishes zero from unknown", () => {
    const progress = buildClaimProgress({
      claims: [
        claim("math", "open", { verified: 3, total: 6 }),
        claim("empty", "open", { verified: 0, total: 0 }),
        claim("missing"),
      ],
    });
    expect(progress.get("math")).toEqual({ obligations: { verified: 3, total: 6 } });
    expect(progress.get("empty")).toEqual({ obligations: { verified: 0, total: 0 } });
    expect(progress.has("missing")).toBe(false);
  });

  it("uses actionable review and decision records, ignoring unassigned items", () => {
    const progress = buildClaimProgress({
      claims: [claim("history"), claim("interpretation", "awaiting_interpretation_review")],
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
      claims: [claim("decision", "needs_recheck"), claim("recheck", "needs_recheck")],
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

  it("links each claim to the visible claims it uses and that use it, and says which open claims can advance", () => {
    const related = (id: string, status: string, uses: string[]): ProjectClaim => ({ ...claim(id, status), uses });
    const progress = buildClaimProgress({
      claims: [
        { ...related("base", "proved", []), assurance: { kind: "lean", review: "approved", supporting: [] } },
        related("middle", "open", ["base"]),
        related("top", "open", ["base", "middle", "withdrawn", "top"]),
        related("alone", "open", []),
        related("done", "proved", ["middle"]),
        claim("legacy"),
      ],
    });
    expect(progress.get("base")).toEqual({
      assurance: { kind: "lean", review: "approved", supporting: [] },
      usedBy: ["middle", "top"],
    });
    expect(progress.get("middle")).toEqual({ uses: ["base"], usedBy: ["top", "done"], canAdvance: true });
    // Claims off the map and the claim itself are not dependencies.
    expect(progress.get("top")).toEqual({ uses: ["base", "middle"], canAdvance: false });
    expect(progress.get("alone")).toEqual({ canAdvance: true });
    expect(progress.get("done")).toEqual({ uses: ["middle"] });
    // A server without the relation fields leaves the claim unclassified.
    expect(progress.has("legacy")).toBe(false);
  });
});
