import type { InboxItem, Job } from "../api/sections";
import { nodeTone } from "./text";
import type { AtlasClaim } from "./types";

type State = "review" | "decision" | "verifying" | "running" | "recheck" | "ready" | "queued";

export type ClaimProgress = {
  state?: State;
  /** Count of recorded proof graph nodes, including the goal. */
  nodes?: { done: number; total: number };
};

const PROOF_NODES = new Set(["goal", "obligation", "local_lemma", "reduction", "evidence"]);
const REVIEW_ITEMS = new Set([
  "confirm_formalization",
  "confirm_goal",
  "confirm_lean_statement",
  "confirm_literature_source",
  "review_evidence",
  "review_formalpedia_basis",
  "review_lean_scope_change",
]);
const DECISION_ITEMS = new Set(["resolve_clarification", "choose_conjecture", "decide_condition"]);
const PRECEDENCE: State[] = ["decision", "review", "recheck", "verifying", "running", "queued", "ready"];

export function buildClaimProgress({
  atlas = [],
  inbox = [],
  jobs = [],
  proofTask,
}: {
  atlas?: AtlasClaim[];
  inbox?: InboxItem[];
  jobs?: Job[];
  proofTask?: { claim_id: string; state: string; blocker?: string | null; can_start?: boolean };
}): ReadonlyMap<string, ClaimProgress> {
  const result = new Map<string, ClaimProgress>();

  function addState(claimId: string, state: State) {
    const current = result.get(claimId);
    if (!current?.state || PRECEDENCE.indexOf(state) < PRECEDENCE.indexOf(current.state)) {
      result.set(claimId, { ...current, state });
    }
  }

  for (const claim of atlas) {
    const nodes = claim.nodes.filter(({ kind }) => PROOF_NODES.has(kind));
    if (nodes.length) {
      const done = nodes.filter(
        (node) => nodeTone(node, false) === "ok" || (node.kind === "reduction" && node.status === "goal_checked"),
      ).length;
      result.set(claim.id, { nodes: { done, total: nodes.length } });
    }
    if (claim.status === "needs_recheck") addState(claim.id, "recheck");
    if (claim.status === "awaiting_interpretation_review") addState(claim.id, "decision");
  }

  for (const { claim_id, kind } of inbox) {
    if (!claim_id) continue;
    if (DECISION_ITEMS.has(kind)) addState(claim_id, "decision");
    else if (REVIEW_ITEMS.has(kind)) addState(claim_id, "review");
  }

  for (const job of jobs) {
    if (!job.claim_id) continue;
    const model = job.model_job === true || Boolean(job.model);
    const verification = job.kind === "verify" && !model;
    if (!model && !verification) continue;
    if (job.state === "running") addState(job.claim_id, verification ? "verifying" : "running");
    if (job.state === "queued") addState(job.claim_id, "queued");
  }

  if (proofTask) {
    const { claim_id, state, blocker, can_start } = proofTask;
    if (state === "blocked" && blocker === "proof_task.decision_needed") addState(claim_id, "decision");
    if (state === "running") addState(claim_id, "running");
    // An idle authorized policy can start another pass when the task snapshot explicitly allows it.
    if ((state === "ready" && can_start !== false) || (state === "authorized" && can_start === true)) {
      addState(claim_id, "ready");
    }
  }

  return result;
}
