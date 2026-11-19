import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { workspaceEventStream } from "../api/client";
import { WORKSPACE_WRITES } from "../api/liveUpdates";
import { readWorkspaceEvents, workspaceEventCursor, workspaceId, type WorkspaceRefresh } from "../api/workspaceEvents";
import { refreshWorkspaceQuery, workspaceEventQueries } from "./workspaceEventQueries";

const RETRY_MS = 1000;
const RETRY_LIMIT_MS = 8000;
const REFRESH_MS = 25;
const STALL_MS = 25_000;

export type WorkspaceConnection = { session: string; workspace: string; live: boolean; cursor: string };

/** Keeps one authenticated subscription for the mounted session and workspace. */
export function WorkspaceEventFeed({
  session,
  workspace,
  bootstrapCursor,
  comprehensive,
  onConnection,
}: {
  session: string;
  workspace: string;
  bootstrapCursor: string;
  comprehensive: boolean;
  onConnection: (connection: WorkspaceConnection) => void;
}) {
  const queries = useQueryClient();
  // An inbox refresh can see a newer head before we read its event. Keep the original cursor until we apply frames.
  const [firstCursor] = useState(bootstrapCursor);
  useEffect(() => {
    if (!workspaceId(workspace) || !workspaceEventCursor(firstCursor)) return;
    let disposed = false;
    let connection: AbortController | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let live = false;
    let bootstrap = comprehensive;
    let cursor = firstCursor;
    let received = firstCursor;
    let refreshes: WorkspaceRefresh[] = [];
    let refresh: ReturnType<typeof setTimeout> | undefined;
    let retryMs = RETRY_MS;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function apply() {
      clearTimeout(refresh);
      refresh = undefined;
      for (const queryKey of workspaceEventQueries(refreshes, session)) {
        refreshWorkspaceQuery(queries, queryKey);
      }
      refreshes = [];
      cursor = received;
    }

    function report(healthy: boolean) {
      if (!comprehensive || live === healthy) return;
      live = healthy;
      onConnection({ session, workspace, live, cursor });
    }

    async function connect() {
      const controller = new AbortController();
      connection = controller;
      const { signal } = controller;
      let terminal = false;
      const watch = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => {
          apply();
          report(false);
          controller.abort();
        }, STALL_MS);
      };
      const activity = () => {
        if (signal.aborted) return;
        watch();
        retryMs = RETRY_MS;
        if (bootstrap) {
          // Independently mounted reads may have finished before the inbox's cursor snapshot.
          bootstrap = false;
          refreshes.push({ kind: "change", area: "workspace" });
          refresh ??= setTimeout(apply, REFRESH_MS);
        }
        report(true);
      };
      watch();
      try {
        const response = await workspaceEventStream(
          session,
          workspace,
          cursor,
          signal,
          comprehensive ? WORKSPACE_WRITES : undefined,
        );
        if (signal.aborted) return;
        if (
          response.ok &&
          response.headers.get("Content-Type")?.split(";", 1)[0]?.trim() === "text/event-stream" &&
          response.body
        ) {
          await readWorkspaceEvents(
            response.body,
            workspace,
            signal,
            (events) => {
              activity();
              for (const event of events) {
                if (BigInt(event.id) <= BigInt(received)) continue;
                received = event.id;
                if (event.refresh) refreshes.push(event.refresh);
              }
              if (refreshes.length) refresh ??= setTimeout(apply, REFRESH_MS);
              else cursor = received;
            },
            (kind) => {
              activity();
              if (comprehensive && kind === "runtime-changed") {
                refreshes.push({ kind: "runtime" });
                refresh ??= setTimeout(apply, REFRESH_MS);
              }
            },
          );
        } else {
          terminal = response.status >= 400 && response.status < 500 && response.status !== 429;
          await response.body?.cancel();
        }
      } catch {
        // Existing workbench polling continues while the stream reconnects.
      } finally {
        clearTimeout(watchdog);
        if (!disposed) {
          apply();
          report(false);
          if (!terminal) {
            retry = setTimeout(() => void connect(), retryMs);
            retryMs = Math.min(retryMs * 2, RETRY_LIMIT_MS);
          }
        }
      }
    }

    void connect();
    return () => {
      disposed = true;
      report(false);
      connection?.abort();
      clearTimeout(watchdog);
      clearTimeout(refresh);
      clearTimeout(retry);
    };
  }, [session, workspace, firstCursor, comprehensive, onConnection, queries]);
  return null;
}
