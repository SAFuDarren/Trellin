// Workspace-wide read models behind the section pages (research_multiplier/workflows/workbench_sections.py).
import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { useFallbackInterval } from "./liveUpdates";
import type { ActivityRecord, Atlas, GraphNode } from "../workbench/types";
import type { ClaimsResponse } from "./client";

export type InboxItem = {
  kind: string;
  item_id: string;
  claim_id: string | null;
  claim_title: string | null;
  waiting_since: string;
  question: string;
  detail: Record<string, unknown> | null;
  priority: number;
};
export type LibraryItem = {
  id: string;
  logical_key: string;
  version: number;
  statement_revision?: number;
  origin: string;
  admitted: boolean;
  needs_recheck: boolean;
  retired: boolean;
  created_at: string;
  kind: string | null;
  natural: string | null;
  declaration: string | null;
  lean: string | null;
  formula: string | null;
  used_by: { id: string; title: string | null }[];
};
export type Job = {
  id: string;
  claim_id: string | null;
  claim_title: string | null;
  kind: string;
  state: string;
  provider: string | null;
  model: string | null;
  model_job: boolean | null;
  target: string | null;
  node_id: string | null;
  enqueued_at: string;
  submitted_at: string | null;
  running_since: string | null;
  execution_ms: number | null;
  failure_code: string | null;
};
export type AgentRow = {
  provider: string;
  model: string | null;
  runs: number;
  last_at: string | null;
  failures: number;
  average_ms: string | number | null;
  input_tokens: string | number | null;
  output_tokens: string | number | null;
  cost_usd: string | number | null;
};
export type Service = {
  provider: string;
  transport: string;
  ready: boolean | null;
  via: "command" | "environment" | "local" | "endpoint";
  detail: string;
  runs: number;
};
export type Team = {
  jobs: Job[];
  agents: AgentRow[];
  verifier: { runs: number; last_at: string | null; failures: number; average_ms: string | number | null };
  services: Service[];
  machine: { name: string; system: string; arch: string; python: string };
  budget: {
    policy: Record<string, unknown> | null;
    month: { period: string; reserved_usd?: string | null; reserved_microusd?: number };
    routes: Record<string, unknown>[];
  };
};
export type Policy = {
  id: string;
  claim_id: string;
  claim_title: string | null;
  state: string | null;
  policy: { metric?: string; max_rounds?: number; budget_seconds?: number; lean?: { model_rounds?: number } } | null;
  proposed_at: string;
  approved_by: string | null;
  approved_at: string | null;
  stopped_at: string | null;
  stop_reason: string | null;
  last_run_at: string | null;
  last_status: string | null;
  last_reason: string | null;
};
export type Thread = {
  claim_id: string;
  claim_title: string | null;
  node_id: string;
  node: GraphNode;
  live: number;
  models: string[];
  last_at: string | null;
  events: ActivityRecord[];
};
export type Doctor = {
  capabilities: Record<string, { title: string; ready: boolean }>;
  checks: {
    id: string;
    group: string;
    title: string;
    status: "ok" | "warn" | "fail" | "skip";
    detail: string;
    fix: string;
  }[];
};

const LIVE_MS = 8000;

export function useClaims(session: string) {
  return useQuery({
    queryKey: ["claims"],
    queryFn: ({ signal }) => api<ClaimsResponse>(session, "/api/claims", { signal }),
  });
}
export function useAtlas(session: string) {
  return useQuery({
    queryKey: ["atlas"],
    queryFn: ({ signal }) => api<Atlas>(session, "/api/atlas", { signal }),
    retry: false,
  });
}
export function useInbox(session: string, streamHealthy = false) {
  return useQuery({
    queryKey: ["inbox", session],
    refetchInterval: useFallbackInterval(LIVE_MS, streamHealthy),
    queryFn: ({ signal }) =>
      api<{
        items: InboxItem[];
        open_research_goals?: { id: string; title: string }[];
        review_identity?: { workspace_id: string };
        clarification_review?: {
          workspace_id: string;
          followups: { id: string; title: string; status: "answered" | "ready"; waiting_since: string }[];
        };
        workspace_events?: { workspace_id: string; cursor: string; coverage?: string };
      }>(session, "/api/inbox", { signal }),
  });
}
export function useLibrary(session: string, enabled = true) {
  return useQuery({
    queryKey: ["library"],
    enabled,
    queryFn: ({ signal }) => api<{ items: LibraryItem[] }>(session, "/api/library", { signal }),
  });
}
export function useTeam(session: string, streamHealthy = false) {
  return useQuery({
    queryKey: ["team"],
    refetchInterval: useFallbackInterval(LIVE_MS, streamHealthy),
    queryFn: ({ signal }) => api<Team>(session, "/api/team", { signal }),
  });
}
export function useProgress(session: string) {
  return useQuery({
    queryKey: ["progress"],
    queryFn: ({ signal }) => api<{ policies: Policy[] }>(session, "/api/progress", { signal }),
  });
}
export function useThreads(session: string) {
  return useQuery({
    queryKey: ["threads"],
    refetchInterval: useFallbackInterval(LIVE_MS),
    queryFn: ({ signal }) => api<{ threads: Thread[] }>(session, "/api/threads", { signal }),
  });
}
