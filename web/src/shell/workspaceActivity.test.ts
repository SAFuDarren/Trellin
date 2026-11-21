import { describe, expect, it } from "vitest";
import type { AgentWorkSummary } from "../api/agentWork";
import type { ConversationSummary } from "../api/conversations";
import type { InboxItem, Job } from "../api/sections";
import { buildWorkspaceActivity, proofActivityKey } from "./workspaceActivity";

const at = "2026-11-21T12:00:00Z";
const job: Job = {
  id: "shared",
  kind: "verify",
  state: "running",
  claim_id: "claim",
  claim_title: "Claim title",
  node_id: "proof-node",
  provider: "codex-cli",
  model: "gpt-fixture",
  model_job: true,
  target: "Fixture check",
  enqueued_at: at,
  submitted_at: null,
  running_since: at,
  execution_ms: null,
  failure_code: null,
};
const thread: ConversationSummary = {
  id: job.id,
  title: "A conversation about the proof",
  claim_id: job.claim_id,
  node_id: job.node_id,
  statement_id: null,
  created_at: at,
  updated_at: at,
  running: true,
  revision: 1,
};
const task: AgentWorkSummary = {
  id: job.id,
  title: "An Agent assignment",
  agent_name: "Reviewer",
  mode: "review",
  status: "running",
  created_at: at,
  updated_at: at,
  delivery_sha256: null,
  predecessor_id: null,
  error: null,
};
const item: InboxItem = {
  kind: "review_evidence",
  item_id: "result",
  claim_id: job.claim_id,
  claim_title: job.claim_title,
  waiting_since: at,
  question: "Verify the result?",
  detail: null,
  priority: 1,
};

describe("workspace activity", () => {
  it("counts each recorded run once and keeps the claim and proof-node totals distinct", () => {
    const activity = buildWorkspaceActivity({
      jobs: [job, job, { ...job, id: "retry", state: "waiting_retry", running_since: null }],
      tasks: [task, { ...task, id: "delivery", status: "delivered" }],
      threads: [thread],
      items: [item, item, { ...item, item_id: "question", kind: "resolve_clarification" }],
    });
    expect(activity.counts).toEqual({ running: 3, queued: 1, review: 2, attention: 1, completed: 0 });
    expect(activity.runningByClaim.get("claim")).toBe(2);
    expect(activity.runningByProofNode.get(proofActivityKey("claim", "proof-node"))).toBe(1);
    expect(activity.reviewItemsByClaim.get("claim")).toEqual([item]);
    expect(activity.inboxCount).toBe(2);
  });
});
