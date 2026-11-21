import type { AgentWorkStatus, AgentWorkSummary } from "../api/agentWork";
import type { ConversationSummary } from "../api/conversations";
import type { InboxItem, Job } from "../api/sections";
import { isReviewItem } from "./reviewItems";

export const ACTIVITY_STATES = ["running", "queued", "review", "attention", "completed"] as const;
export type ActivityState = (typeof ACTIVITY_STATES)[number];

type ActivityTime = { at: string; kind: "started" | "queued" | "waiting" | "updated" | "finished" };
type ActivityEntry = {
  id: string;
  state: ActivityState;
  claimId?: string | null;
  nodeId?: string | null;
  time?: ActivityTime;
};
export type ActivityRow = ActivityEntry &
  (
    | { source: "job"; job: Job }
    | { source: "work"; task: AgentWorkSummary }
    | { source: "chat"; thread: ConversationSummary }
    | { source: "review"; item: InboxItem }
  );

type Followup = { id: string; title: string; status: "answered" | "ready"; waiting_since: string };
type ActivitySources = {
  jobs?: Job[];
  tasks?: AgentWorkSummary[];
  threads?: ConversationSummary[];
  items?: InboxItem[];
  followups?: Followup[];
};

const JOB_STATES: Record<string, ActivityState> = {
  running: "running",
  queued: "queued",
  waiting_retry: "queued",
  waiting_dependency: "queued",
  succeeded: "completed",
  cancelled: "completed",
  failed: "attention",
  lease_expired: "attention",
};
const WORK_STATES: Record<AgentWorkStatus, ActivityState> = {
  queued: "queued",
  running: "running",
  delivered: "review",
  blocked: "attention",
  failed: "attention",
  interrupted: "attention",
  accepted: "completed",
  returned: "completed",
};

function increment(counts: Map<string, number>, key: string) {
  counts.set(key, (counts.get(key) ?? 0) + 1);
}

function activityTime(at: string | null | undefined, kind: ActivityTime["kind"]): ActivityTime | undefined {
  return at && Number.isFinite(Date.parse(at)) ? { at, kind } : undefined;
}

export function proofActivityKey(claimId: string, nodeId: string) {
  return claimId + "|" + nodeId;
}

export function buildWorkspaceActivity({ jobs, tasks, threads, items, followups }: ActivitySources) {
  const entries = new Map<string, ActivityRow>();
  const add = (entry: ActivityRow) => entries.set(entry.id, entry);

  // The Team snapshot is the job ledger. Proof history also projects these jobs into its nodes.
  for (const job of jobs ?? []) {
    const state = JOB_STATES[job.state];
    if (!state) continue;
    add({
      id: "job:" + job.id,
      source: "job",
      job,
      state,
      claimId: job.claim_id,
      nodeId: job.node_id,
      time:
        (job.state === "running" ? activityTime(job.running_since, "started") : undefined) ??
        activityTime(job.submitted_at, "finished") ??
        activityTime(job.enqueued_at, "queued"),
    });
  }
  for (const task of tasks ?? []) {
    add({
      id: "work:" + task.id,
      source: "work",
      task,
      state: WORK_STATES[task.status],
      time:
        task.status === "queued" ? activityTime(task.created_at, "queued") : activityTime(task.updated_at, "updated"),
    });
  }
  for (const thread of threads ?? []) {
    const turn = thread.latest_turn;
    if (!thread.running && !turn) continue;
    add({
      id: "chat:" + thread.id,
      source: "chat",
      thread,
      state: thread.running ? "running" : turn?.status === "answered" ? "completed" : "attention",
      claimId: thread.claim_id,
      nodeId: thread.node_id,
      time:
        activityTime(turn?.at, thread.running ? "started" : turn?.status === "answered" ? "finished" : "updated") ??
        activityTime(thread.updated_at, "updated"),
    });
  }
  function addReview(item: InboxItem) {
    add({
      id: `review:${item.kind}:${item.item_id}`,
      source: "review",
      item,
      state: isReviewItem(item) ? "review" : "attention",
      claimId: item.claim_id,
      time: activityTime(item.waiting_since, "waiting"),
    });
  }
  for (const followup of followups ?? []) {
    addReview({
      kind: "clarify_formalization",
      item_id: followup.id,
      claim_id: null,
      claim_title: followup.title,
      waiting_since: followup.waiting_since,
      question: "",
      detail: { clarification_status: followup.status },
      priority: 1,
    });
  }
  for (const item of items ?? []) addReview(item);

  const counts: Record<ActivityState, number> = { running: 0, queued: 0, review: 0, attention: 0, completed: 0 };
  const runningByClaim = new Map<string, number>();
  const runningByProofNode = new Map<string, number>();
  const reviewItemsByClaim = new Map<string, InboxItem[]>();
  let inboxCount = 0;
  for (const row of entries.values()) {
    counts[row.state]++;
    if (row.source === "review") inboxCount++;
    if (!row.claimId) continue;
    if (row.source === "review" && row.state === "review") {
      const items = reviewItemsByClaim.get(row.claimId);
      if (items) items.push(row.item);
      else reviewItemsByClaim.set(row.claimId, [row.item]);
    }
    if (row.state !== "running") continue;
    increment(runningByClaim, row.claimId);
    if (row.source === "job") {
      // Proof history uses the goal for jobs with no recorded focus node.
      increment(runningByProofNode, proofActivityKey(row.claimId, row.nodeId || "goal:" + row.claimId));
    }
  }
  const rows = [...entries.values()].sort(
    (left, right) =>
      ACTIVITY_STATES.indexOf(left.state) - ACTIVITY_STATES.indexOf(right.state) ||
      (right.time ? Date.parse(right.time.at) : 0) - (left.time ? Date.parse(left.time.at) : 0) ||
      left.id.localeCompare(right.id),
  );
  return { rows, counts, runningByClaim, runningByProofNode, reviewItemsByClaim, inboxCount };
}

export type WorkspaceActivity = ReturnType<typeof buildWorkspaceActivity>;
