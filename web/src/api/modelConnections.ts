import { useQuery } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { api } from "./client";

export const API_MODEL_PROVIDERS = ["openai", "anthropic"] as const;
export type NativeModelProvider = "codex-cli" | "claude-code";
export type ModelProvider = NativeModelProvider | (typeof API_MODEL_PROVIDERS)[number];
export const NATIVE_DEFAULT_MODEL = "native-default";

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

export function connectionModelLabel(t: TFunction, { provider, model }: { provider: string; model: string }) {
  return (provider === "codex-cli" || provider === "claude-code") && model === NATIVE_DEFAULT_MODEL
    ? t("modelConnections.defaultModel")
    : model;
}

export function dispatchableConnection(
  connection: ModelConnection,
): connection is ModelConnection & { fingerprint: string } {
  return Boolean(connection.fingerprint && ["connected", "untested"].includes(connection.status));
}
