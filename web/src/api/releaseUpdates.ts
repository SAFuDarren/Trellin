import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

const PATH = "/api/release-updates";
const DAY_MS = 24 * 60 * 60 * 1000;

export type ReleaseUpdateState = {
  installed_version: string | null;
  automatic: boolean;
  last_checked_at: number | null;
  status: "not_checked" | "current" | "available" | "no_release" | "unavailable";
  available: { version: string; url: string; dismissed: boolean } | null;
};

type UpdateAction =
  | { action: "check" | "auto_check" }
  | { action: "automatic"; enabled: boolean }
  | { action: "dismiss"; version: string };

function queryKey(session: string) {
  return ["release-updates", session] as const;
}

export function useReleaseUpdates(session: string) {
  const queries = useQueryClient();
  const { mutate, isPending, isError, variables } = useMutation({
    mutationFn: ({ owner, action }: { owner: string; action: UpdateAction }) =>
      api<ReleaseUpdateState>(owner, PATH, { data: action }),
    onMutate: async ({ owner }) => {
      await queries.cancelQueries({ queryKey: queryKey(owner), exact: true });
    },
    onSuccess: (result, { owner }) => queries.setQueryData(queryKey(owner), result),
    // The server is local and can give an offline result even when the device has no Internet.
    networkMode: "always",
    retry: false,
  });
  const { data } = useQuery({
    queryKey: queryKey(session),
    queryFn: ({ signal }) => api<ReleaseUpdateState>(session, PATH, { signal }),
    networkMode: "always",
    retry: false,
    refetchOnWindowFocus: !isPending,
  });
  const lastAttempt = useRef<{ session: string; at: number } | null>(null);
  const automatic = data?.automatic === true;
  const lastChecked = data?.last_checked_at;
  useEffect(() => {
    if (!automatic || isPending) return;
    // The local timer only decides when to ask. The server checks consent and cooldown across workspaces.
    const attemptedAt = lastAttempt.current?.session === session ? lastAttempt.current.at : 0;
    const due = Math.max((lastChecked ?? 0) * 1000, attemptedAt) + DAY_MS;
    const timer = setTimeout(
      () => {
        lastAttempt.current = { session, at: Date.now() };
        mutate({ owner: session, action: { action: "auto_check" } });
      },
      Math.max(0, Math.min(DAY_MS, due - Date.now())),
    );
    return () => clearTimeout(timer);
  }, [automatic, isPending, lastChecked, mutate, session]);

  const sameSession = variables?.owner === session;
  return {
    state: typeof data?.automatic === "boolean" ? data : undefined,
    pending: sameSession && isPending ? variables.action.action : undefined,
    error: sameSession && isError,
    submit: (action: UpdateAction) => mutate({ owner: session, action }),
  };
}

export type ReleaseUpdates = ReturnType<typeof useReleaseUpdates>;
