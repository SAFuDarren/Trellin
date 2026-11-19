import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { workspaceEventStream } from "../api/client";
import { readWorkspaceEvents, workspaceEventCursor, workspaceId } from "../api/workspaceEvents";

const RETRY_MS = 1000;
const RETRY_LIMIT_MS = 8000;

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
    let retryMs = RETRY_MS;
    let retry: ReturnType<typeof setTimeout> | undefined;

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
            const reviewed = new Set<string>();
            for (const event of events) {
              if (BigInt(event.id) <= BigInt(cursor)) continue;
              cursor = event.id;
              if (event.reviewedClaim) reviewed.add(event.reviewedClaim);
            }
            retryMs = RETRY_MS;
            if (reviewed.size) {
              void queries.invalidateQueries({ queryKey: ["inbox", session] });
              void queries.invalidateQueries({ queryKey: ["atlas"] });
              for (const claimId of reviewed) void queries.invalidateQueries({ queryKey: ["graph", claimId] });
            }
          });
        } else {
          await response.body?.cancel();
          if (response.status >= 400 && response.status < 500 && response.status !== 429) return;
        }
      } catch {
        // Existing workbench polling continues while the stream reconnects.
      }
      if (!abort.signal.aborted) {
        retry = setTimeout(() => void connect(), retryMs);
        retryMs = Math.min(retryMs * 2, RETRY_LIMIT_MS);
      }
    }

    void connect();
    return () => {
      abort.abort();
      clearTimeout(retry);
    };
  }, [session, workspace, firstCursor, queries]);
  return null;
}
