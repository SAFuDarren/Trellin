import type { WorkspaceRefresh } from "../api/workspaceEvents";

/** Return only the live read models affected by a batch of committed events. */
export function workspaceEventQueries(events: readonly WorkspaceRefresh[], session: string) {
  const keys = new Map<string, string[]>();
  const add = (...key: string[]) => keys.set(JSON.stringify(key), key);
  const inbox = () => add("inbox", session);

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
        break;
      case "basis":
      case "claim":
        inbox();
        add("claims");
        add("atlas");
        add("graph", event.claimId);
        add("impact", event.claimId);
        add("proof-task", session, event.claimId);
        if (event.kind === "basis") add("library");
        break;
      case "job":
        add("team");
        add("progress");
        add("proof-task", session, event.claimId);
        add("activity", event.claimId);
        break;
      case "proof":
      case "proof-result":
      case "evidence":
        inbox();
        if (event.kind !== "proof") {
          add("atlas");
          add("graph", event.claimId);
        }
        add("progress");
        add("proof-task", session, event.claimId);
        add("activity", event.claimId);
        break;
    }
  }
  // Global withdrawals cover the same active queries as any narrower claim hints.
  return [...keys.values()].filter(
    (key) => !key.some((_part, index) => index > 0 && keys.has(JSON.stringify(key.slice(0, index)))),
  );
}
