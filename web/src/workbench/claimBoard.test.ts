import { describe, expect, it } from "vitest";
import {
  boardColumns,
  laneGroups,
  matchesSearch,
  obligationSegments,
  segmentSummary,
  visibleCards,
} from "./claimBoard";

const node = (status: string, kind = "obligation") => ({ id: status + kind, kind, status, title: "" });

describe("obligationSegments", () => {
  it("reads each obligation's status from the atlas and ignores other nodes", () => {
    const nodes = [node("verified"), node("failed"), node("open"), node("verified", "goal")];
    expect(obligationSegments(nodes, undefined, false)).toEqual(["done", "failed", "open"]);
  });

  it("falls back to the verified and total counts", () => {
    expect(obligationSegments([], { verified: 2, total: 4 }, false)).toEqual(["done", "done", "open", "open"]);
    expect(obligationSegments(undefined, undefined, false)).toEqual([]);
  });

  it("marks the first unfinished obligation live while work runs", () => {
    expect(obligationSegments([], { verified: 1, total: 3 }, true)).toEqual(["done", "live", "open"]);
    expect(obligationSegments([], { verified: 2, total: 2 }, true)).toEqual(["done", "done"]);
  });

  it("summarises done, total and failed", () => {
    expect(segmentSummary(["done", "failed", "live", "open"])).toEqual({ done: 1, total: 4, failed: 1 });
  });
});

describe("board layout", () => {
  it("keeps the known columns in order and adds unknown statuses", () => {
    expect(boardColumns(["proved", "open"])).toEqual([
      "open",
      "awaiting_interpretation_review",
      "needs_recheck",
      "proved",
    ]);
    expect(boardColumns(["refuted", "custom", "custom"])).toEqual([
      "open",
      "awaiting_interpretation_review",
      "needs_recheck",
      "proved",
      "refuted",
      "custom",
    ]);
  });

  it("groups claims by domain in name order with the unnamed group last", () => {
    const claims = [{ domain: "topology" }, { domain: null }, { domain: "algebra" }, { domain: "topology" }];
    expect(laneGroups(claims, "en").map(({ key, claims }) => [key, claims.length])).toEqual([
      ["algebra", 1],
      ["topology", 2],
      ["", 1],
    ]);
  });

  it("matches search on code, title and domain", () => {
    const claim = { title: "Lagrange theorem", domain: "groups" };
    expect(matchesSearch(claim, "C112", " c112 ")).toBe(true);
    expect(matchesSearch(claim, "C112", "lagrange")).toBe(true);
    expect(matchesSearch(claim, "C112", "GROUP")).toBe(true);
    expect(matchesSearch(claim, "C112", "prime")).toBe(false);
    expect(matchesSearch(claim, "C112", "  ")).toBe(true);
  });

  it("limits each cell until it is expanded", () => {
    expect(visibleCards([1, 2, 3, 4, 5], 3, false)).toEqual({ shown: [1, 2, 3], hidden: 2 });
    expect(visibleCards([1, 2, 3, 4, 5], 3, true)).toEqual({ shown: [1, 2, 3, 4, 5], hidden: 0 });
    expect(visibleCards([1, 2], 3, false)).toEqual({ shown: [1, 2], hidden: 0 });
    expect(visibleCards([1, 2, 3, 4], 0, false)).toEqual({ shown: [1, 2, 3, 4], hidden: 0 });
  });
});
