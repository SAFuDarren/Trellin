import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export const MODEL_PROVIDERS = ["codex-cli", "claude-code", "openai", "anthropic"] as const;
export type ModelProvider = (typeof MODEL_PROVIDERS)[number];

export type ModelConnection = {
  id: string;
  provider: ModelProvider;
  model: string;
  label: string;
  fingerprint: string | null;
  status: string;
};

export const modelConnectionsKey = (session: string) => ["model-connections", session];

export function useModelConnections(session: string, enabled = true) {
  return useQuery({
    queryKey: modelConnectionsKey(session),
    queryFn: ({ signal }) => api<{ connections: ModelConnection[] }>(session, "/api/model-connections", { signal }),
    retry: false,
    enabled,
  });
}

export function connectionLabel(connection: ModelConnection) {
  return connection.label || connection.model;
}

export function dispatchableConnection(
  connection: ModelConnection,
): connection is ModelConnection & { fingerprint: string } {
  return Boolean(connection.fingerprint && ["connected", "untested"].includes(connection.status));
}
