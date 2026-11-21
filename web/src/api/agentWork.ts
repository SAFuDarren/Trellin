import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./client";
import { useFallbackInterval } from "./liveUpdates";

export const AGENT_WORK_MODES = ["implement", "review", "draft"] as const;
export type AgentWorkMode = (typeof AGENT_WORK_MODES)[number];
export type AgentWorkStatus =
  | "queued"
  | "running"
  | "blocked"
  | "delivered"
  | "failed"
  | "interrupted"
  | "accepted"
  | "returned";

export type AgentWorkSummary = {
  id: string;
  title: string;
  agent_id?: string | null;
  agent_name: string;
  mode: AgentWorkMode;
  status: AgentWorkStatus;
  created_at: string;
  updated_at: string;
  delivery_sha256: string | null;
  predecessor_id: string | null;
  error: string | null;
};

export type AgentWorkTask = AgentWorkSummary & {
  delivery: { kind: string; summary: string; body: string; checks: string[] } | null;
  instructions?: string;
  criteria?: string;
  checkout?: string | null;
  base_revision?: string | null;
  predecessor_sha256?: string | null;
  review?: { decision: AgentWorkDecision; reason: string; reviewed_at: string } | null;
};

export type AgentWorkRequest = {
  task_id: string;
  agent_id: string;
  mode: AgentWorkMode;
  title: string;
  instructions: string;
  criteria: string;
  checkout?: string;
  revision?: string;
  files?: string[];
  predecessor_id?: string;
};

export type AgentWorkPreview = {
  token: string;
  workspace_id: string;
  agent_name: string;
  provider: string;
  model: string;
  external: boolean;
  outgoing: string;
  tools: string[];
  base_revision?: string | null;
  predecessor_sha256?: string | null;
  limits?: { wall_seconds: number; max_output_tokens: number; output_token_limit_enforced: boolean };
};

export type AgentWorkRetryPreview = AgentWorkPreview & { generation: number };
export type AgentWorkDecision = "accept" | "return";
type AgentWorkRetry = { task_id: string; action_id: string };
type AgentWorkReview = {
  task_id: string;
  action_id: string;
  delivery_sha256: string;
  decision: AgentWorkDecision;
  reason: string;
};

const ROOT = "/api/agent-work";
const key = (session: string) => ["agent-work", session];
const detailKey = (session: string, id: string) => [...key(session), id];
const active = (task?: AgentWorkSummary) => task?.status === "queued" || task?.status === "running";

export function useAgentWork(session: string, streamHealthy = false) {
  const fallback = useFallbackInterval(8000, streamHealthy);
  return useQuery({
    queryKey: key(session),
    queryFn: ({ signal }) => api<{ tasks: AgentWorkSummary[]; available: boolean }>(session, ROOT, { signal }),
    refetchInterval: (query) =>
      query.state.data?.available ? (query.state.data.tasks.some(active) ? 2500 : fallback) : false,
    retry: false,
  });
}

export function useAgentWorkTask(session: string, id: string) {
  const fallback = useFallbackInterval(8000);
  return useQuery({
    queryKey: detailKey(session, id),
    queryFn: async ({ signal }) => {
      const result = await api<{ task: AgentWorkTask }>(session, `${ROOT}?task=${encodeURIComponent(id)}`, { signal });
      if (result.task.id !== id) throw new ApiError(409, "agent_work.stale");
      return result.task;
    },
    refetchInterval: (query) => (active(query.state.data) ? 2500 : fallback),
    retry: false,
  });
}

export function useAgentWorkActions(session: string) {
  const queries = useQueryClient();
  async function record<T extends { task_id: string }>(action: "start" | "review" | "retry", data: T) {
    const { task } = await api<{ task: AgentWorkTask }>(session, `${ROOT}/${action}`, { data });
    if (task.id !== data.task_id) throw new ApiError(409, "agent_work.stale");
    await queries.cancelQueries({ queryKey: detailKey(session, task.id), exact: true });
    queries.setQueryData(detailKey(session, task.id), task);
    void queries.invalidateQueries({ queryKey: key(session), exact: true });
    return task;
  }
  return {
    preview(request: AgentWorkRequest) {
      return api<AgentWorkPreview>(session, `${ROOT}/preview`, { data: request });
    },
    start(request: AgentWorkRequest, approval: AgentWorkPreview) {
      return record("start", { ...request, token: approval.token, confirm_external: approval.external });
    },
    retryPreview(request: AgentWorkRetry) {
      return api<AgentWorkRetryPreview>(session, `${ROOT}/retry-preview`, { data: request });
    },
    retry(request: AgentWorkRetry, approval: AgentWorkRetryPreview) {
      return record("retry", {
        ...request,
        generation: approval.generation,
        token: approval.token,
        confirm_external: approval.external,
      });
    },
    review(request: AgentWorkReview) {
      return record("review", request);
    },
  };
}
