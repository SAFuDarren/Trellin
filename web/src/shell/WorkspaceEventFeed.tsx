import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { workspaceEventStream } from "../api/client";
import { readWorkspaceEvents, workspaceEventCursor, workspaceId, type WorkspaceRefresh } from "../api/workspaceEvents";
import { workspaceEventQueries } from "./workspaceEventQueries";

const RETRY_MS = 1000;
const RETRY_LIMIT_MS = 8000;
const REFRESH_MS = 25;

/** Keeps one authenticated subscription for the mounted session and workspace. */
export function WorkspaceEventFeed({
  session,
  workspace,
  bootstrapCursor,
}: {
  session: string;
  workspace: string;
  bootstrapCursor: string;
}) {
  const queries = useQueryClient();
  // An inbox refresh can see a newer head before we read its event. Keep the original cursor until we apply frames.
  const [firstCursor] = useState(bootstrapCursor);
  useEffect(() => {
    if (!workspaceId(workspace) || !workspaceEventCursor(firstCursor)) return;
    const abort = new AbortController();
    let cursor = firstCursor;
    let received = firstCursor;
    let refreshes: WorkspaceRefresh[] = [];
    let refresh: ReturnType<typeof setTimeout> | undefined;
    let retryMs = RETRY_MS;
    let retry: ReturnType<typeof setTimeout> | undefined;

    function apply() {
      clearTimeout(refresh);
      refresh = undefined;
      for (const queryKey of workspaceEventQueries(refreshes, session)) void queries.invalidateQueries({ queryKey });
      refreshes = [];
      cursor = received;
    }

    async function connect() {
      try {
        const response = await workspaceEventStream(session, workspace, cursor, abort.signal);
        if (abort.signal.aborted) return;
        if (
          response.ok &&
          response.headers.get("Content-Type")?.split(";", 1)[0]?.trim() === "text/event-stream" &&
          response.body
        ) {
          await readWorkspaceEvents(response.body, workspace, abort.signal, (events) => {
            for (const event of events) {
              if (BigInt(event.id) <= BigInt(received)) continue;
              received = event.id;
              if (event.refresh) refreshes.push(event.refresh);
            }
            retryMs = RETRY_MS;
            if (refreshes.length) refresh ??= setTimeout(apply, REFRESH_MS);
            else cursor = received;
          });
        } else {
          await response.body?.cancel();
          if (response.status >= 400 && response.status < 500 && response.status !== 429) return;
        }
      } catch {
        // Existing workbench polling continues while the stream reconnects.
      }
      if (!abort.signal.aborted) {
        apply();
        retry = setTimeout(() => void connect(), retryMs);
        retryMs = Math.min(retryMs * 2, RETRY_LIMIT_MS);
      }
    }

    void connect();
    return () => {
      abort.abort();
      clearTimeout(refresh);
      clearTimeout(retry);
    };
  }, [session, workspace, firstCursor, queries]);
  return null;
}
