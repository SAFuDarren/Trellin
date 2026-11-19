import { describe, expect, it } from "vitest";
import { workspaceEventQueries } from "./workspaceEventQueries";

describe("Workspace event read models", () => {
  it("refreshes active intake drafts and theorem records when their committed area changes", () => {
    const keys = workspaceEventQueries(
      [
        { kind: "change", area: "intake", claimId: "claim" },
        { kind: "change", area: "clarification", claimId: "claim" },
      ],
      "session",
    );
    expect(keys).toEqual([
      ["project-intake-drafts", "session"],
      ["manual-theorems", "session"],
      ["library"],
      ["claims"],
      ["atlas"],
      ["inbox", "session"],
      ["graph", "claim"],
      ["impact", "claim"],
      ["proof-task", "session", "claim"],
      ["threads"],
    ]);
  });

  it("limits clock and liveness updates to runtime views and drops covered narrow keys", () => {
    expect(
      workspaceEventQueries([{ kind: "change", area: "activity", claimId: "claim" }, { kind: "runtime" }], "session"),
    ).toEqual([["threads"], ["team"], ["activity"], ["proof-task", "session"]]);
  });

  it("refreshes conversations for recorded proof passes, job changes, and graph changes", () => {
    for (const kind of ["proof-result", "job", "claim", "basis"] as const) {
      expect(workspaceEventQueries([{ kind, claimId: "claim" }], "session")).toContainEqual(["threads"]);
    }
    expect(workspaceEventQueries([{ kind: "change", area: "claim", claimId: "claim" }], "session")).toContainEqual([
      "threads",
    ]);
  });
});
