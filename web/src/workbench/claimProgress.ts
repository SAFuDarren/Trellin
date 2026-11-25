import type { ClaimAssurance, ObligationCount, ProjectClaim } from "../api/projectCanvas";
import type { InboxItem, Job } from "../api/sections";

type State = "review" | "decision" | "verifying" | "running" | "recheck" | "ready" | "queued";

export type ClaimProgress = {
  state?: State;
  obligations?: ObligationCount;
  /** #342: how the claim was established, as recorded. */
  assurance?: ClaimAssurance;
  /** #342: claims whose results the claim's current formalization relies on. */
  uses?: string[];
  /** #342: claims whose current formalization relies on this claim. */
  usedBy?: string[];
  /** #342: an open claim with every claim it relies on proved. */
  canAdvance?: boolean;
};

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
  claims = [],
  inbox = [],
  jobs = [],
  proofTask,
}: {
  claims?: ProjectClaim[];
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

  function add(claimId: string, value: ClaimProgress) {
    result.set(claimId, { ...result.get(claimId), ...value });
  }

  const statuses = new Map(claims.map((claim) => [claim.id, claim.status]));
  for (const claim of claims) {
    if (claim.obligations) add(claim.id, { obligations: claim.obligations });
    if (claim.assurance) add(claim.id, { assurance: claim.assurance });
    // Only claims on the map count; the index already leaves out withdrawn ones.
    const uses = claim.uses?.filter((id) => id !== claim.id && statuses.has(id));
    if (uses?.length) add(claim.id, { uses });
    for (const id of uses ?? []) {
      add(id, { usedBy: [...(result.get(id)?.usedBy ?? []), claim.id] });
    }
    if (uses && claim.status === "open") {
      add(claim.id, { canAdvance: uses.every((id) => statuses.get(id) === "proved") });
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
