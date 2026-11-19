import type { QueryClient } from "@tanstack/react-query";
import type { WorkspaceRefresh } from "../api/workspaceEvents";

export function refreshWorkspaceQuery(queries: QueryClient, queryKey: readonly string[]) {
  // Refetch cancels existing reads with cached data, but reuses an unfinished first read.
  void queries.cancelQueries({
    queryKey,
    type: "active",
    predicate: ({ state }) => state.data === undefined && state.fetchStatus !== "idle",
  });
  void queries.invalidateQueries({ queryKey });
}

/** Return only the live read models affected by a batch of committed events. */
export function workspaceEventQueries(events: readonly WorkspaceRefresh[], session: string) {
  const keys = new Map<string, string[]>();
  const add = (...key: string[]) => keys.set(JSON.stringify(key), key);
  const inbox = () => add("inbox", session);
  const intake = () => {
    add("project-intake-drafts", session);
    add("manual-theorems", session);
  };
  const runtime = () => {
    add("team");
    add("threads");
    add("activity");
    add("proof-task", session);
  };

  for (const event of events) {
    switch (event.kind) {
      case "library":
      case "basis-item":
        inbox();
        add("library");
        if (event.kind === "basis-item") {
          add("atlas");
          add("graph");
          add("proof-task", session);
          add("threads");
        }
        break;
      case "recheck":
        inbox();
        add("claims");
        add("atlas");
        add("library");
        add("progress");
        add("graph");
        add("impact");
        add("proof-task", session);
        add("threads");
        break;
      case "basis":
      case "claim":
        inbox();
        add("claims");
        add("atlas");
        add("graph", event.claimId);
        add("impact", event.claimId);
        add("proof-task", session, event.claimId);
        add("threads");
        if (event.kind === "basis") add("library");
        break;
      case "job":
        add("team");
        add("progress");
        add("proof-task", session, event.claimId);
        add("activity", event.claimId);
        add("threads");
        break;
      case "proof":
      case "proof-result":
      case "evidence":
        inbox();
        if (event.kind !== "proof") {
          add("atlas");
          add("graph", event.claimId);
          add("threads");
        }
        add("progress");
        add("proof-task", session, event.claimId);
        add("activity", event.claimId);
        break;
      case "runtime":
        runtime();
        break;
      case "change": {
        const claim = event.claimId ? [event.claimId] : [];
        const proofTask = () => add("proof-task", session, ...claim);
        const graph = () => {
          add("graph", ...claim);
          add("impact", ...claim);
          proofTask();
          add("threads");
        };
        const claims = () => {
          add("claims");
          add("atlas");
          inbox();
          graph();
        };
        switch (event.area) {
          case "activity":
            add("activity", ...claim);
            add("threads");
            add("team");
            break;
          case "operations":
            add("team");
            add("progress");
            proofTask();
            inbox();
            break;
          case "proof":
            add("atlas");
            add("progress");
            add("activity", ...claim);
            add("team");
            inbox();
            graph();
            break;
          case "claim":
            claims();
            break;
          case "clarification":
            claims();
            break;
          case "intake":
          case "library":
            intake();
            add("library");
            claims();
            break;
          case "workspace":
            intake();
            runtime();
            add("library");
            add("progress");
            add("graph");
            add("impact");
            add("claims");
            add("atlas");
            inbox();
            break;
        }
        break;
      }
    }
  }
  // Global withdrawals cover the same active queries as any narrower claim hints.
  return [...keys.values()].filter(
    (key) => !key.some((_part, index) => index > 0 && keys.has(JSON.stringify(key.slice(0, index)))),
  );
}
