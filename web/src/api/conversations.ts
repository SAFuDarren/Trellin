import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { api, ApiError } from "./client";
import { useFallbackInterval } from "./liveUpdates";
import { connectionModelLabel } from "./modelConnections";

export type ConversationAnchor = { claim_id?: string; statement_id?: string; node_id?: string };

export type ConversationSummary = {
  id: string;
  title: string;
  agent_id?: string | null;
  claim_id: string | null;
  statement_id: string | null;
  node_id: string | null;
  updated_at: string;
  running: boolean;
  revision: number;
  created_at: string;
  latest_turn?: {
    provider: string;
    requested_model: string;
    model: string | null;
    preview: string;
    at: string;
    status: "running" | "answered" | "failed";
  } | null;
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: string;
  provider?: string;
  model?: string | null;
  requested_model?: string;
};

export type Conversation = ConversationSummary & { messages: ConversationMessage[]; latest_error?: string | null };
export type ConversationRequest = {
  thread_id: string;
  connection_id: string;
  text: string;
  request_id: string;
  include_history?: boolean;
};
export type ConversationPreview = {
  token: string;
  provider: string;
  model: string;
  outgoing: string;
  external: boolean;
};

const ROOT = "/api/conversations";
export const conversationKey = (session: string) => ["conversations", session];
const detailKey = (session: string, id: string) => [...conversationKey(session), "thread", id];

export function conversationErrorText(t: TFunction, error: unknown): string {
  const code = error instanceof ApiError ? (error.code ?? error.message) : "";
  return t(`errors.${code}`, { defaultValue: t("conversations.failed") });
}

export function conversationRecipientLabel(t: TFunction, provider: string, model: string): string {
  if (provider === "ollama-native") return t("conversations.localModel", { name: model });
  const name = t(`team.providers.${provider}`, { defaultValue: provider });
  return name + " · " + connectionModelLabel(t, { provider, model });
}

export function useConversations(session: string, claimId?: string, streamHealthy = false) {
  const fallback = useFallbackInterval(8000, streamHealthy);
  return useQuery({
    queryKey: [...conversationKey(session), "list", claimId ?? ""],
    queryFn: ({ signal }) =>
      api<{ threads: ConversationSummary[] }>(
        session,
        claimId ? `${ROOT}?claim_id=${encodeURIComponent(claimId)}` : ROOT,
        { signal },
      ),
    // A running turn can reach its deadline without a notification.
    refetchInterval: (query) => (query.state.data?.threads?.some((thread) => thread.running) ? 8000 : fallback),
    retry: false,
  });
}

export function useConversation(session: string, id: string) {
  const fallback = useFallbackInterval(8000);
  return useQuery({
    queryKey: detailKey(session, id),
    queryFn: ({ signal }) =>
      api<{ thread: Conversation }>(session, `${ROOT}/thread?thread_id=${encodeURIComponent(id)}`, { signal }),
    // A dropped worker can expire by database time without emitting a workspace event.
    refetchInterval: (query) => (query.state.data?.thread.running ? 4000 : fallback),
    retry: false,
  });
}

export function useConversationActions(session: string) {
  const queries = useQueryClient();
  return {
    async create(title: string, anchor: ConversationAnchor, agentId?: string, requestId?: string) {
      const { thread } = await api<{ thread: ConversationSummary }>(session, `${ROOT}/create`, {
        data: {
          title,
          ...anchor,
          ...(agentId ? { agent_id: agentId, request_id: requestId ?? crypto.randomUUID() } : {}),
        },
      });
      await queries.invalidateQueries({ queryKey: conversationKey(session) });
      return thread;
    },
    async archive(id: string) {
      await api(session, `${ROOT}/archive`, { data: { thread_id: id } });
      queries.removeQueries({ queryKey: detailKey(session, id), exact: true });
      await queries.invalidateQueries({ queryKey: conversationKey(session) });
    },
    preview(request: ConversationRequest) {
      return api<ConversationPreview>(session, `${ROOT}/preview`, { data: request });
    },
    async send(request: ConversationRequest, token: string, external: boolean) {
      const detail = await api<{ thread: Conversation }>(session, `${ROOT}/send`, {
        data: { ...request, token, confirm_external: external },
      });
      await queries.cancelQueries({ queryKey: detailKey(session, request.thread_id), exact: true });
      queries.setQueryData<{ thread: Conversation }>(detailKey(session, request.thread_id), (current) =>
        current && current.thread.revision > detail.thread.revision ? current : detail,
      );
      await queries.invalidateQueries({ queryKey: conversationKey(session) });
    },
  };
}
