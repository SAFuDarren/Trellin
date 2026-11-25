import { describe, expect, it } from "vitest";
import type { ActivityRow } from "./workspaceActivity";
import { resolveWaiting, stepTarget, waitingItems, waitingKind } from "./workContext";

const at = (minute: number) => ({
  at: `2026-11-25T10:${String(minute).padStart(2, "0")}:00Z`,
  kind: "updated" as const,
});

function review(id: string, state: "review" | "attention", minute: number): ActivityRow {
  return {
    id,
    source: "review",
    state,
    time: at(minute),
    item: {
      kind: "review_evidence",
      item_id: id,
      claim_id: null,
      claim_title: null,
      waiting_since: "",
      question: "",
      detail: null,
      priority: 1,
    },
  } as ActivityRow;
}
function job(id: string, state: string, minute: number): ActivityRow {
  return { id, source: "job", state: "attention", time: at(minute), job: { id, state } } as unknown as ActivityRow;
}
function work(id: string, status: string, minute: number): ActivityRow {
  return { id, source: "work", state: "review", time: at(minute), task: { id, status } } as unknown as ActivityRow;
}

describe("waiting items", () => {
  it("keeps reviews, questions, failed runs and Agent deliveries that need a decision", () => {
    expect(waitingKind(review("a", "review", 1))).toBe("review");
    expect(waitingKind(review("b", "attention", 1))).toBe("question");
    expect(waitingKind(job("c", "failed", 1))).toBe("failed");
    expect(waitingKind(job("d", "succeeded", 1))).toBeNull();
    expect(waitingKind(work("e", "delivered", 1))).toBe("delivery");
    expect(waitingKind(work("f", "blocked", 1))).toBe("failed");
    expect(waitingKind(work("g", "accepted", 1))).toBeNull();
  });

  it("lists the newest first and leaves out archived records", () => {
    const rows = [review("old", "review", 1), job("new", "failed", 9), work("mid", "delivered", 5)];
    expect(waitingItems(rows).map(({ id }) => id)).toEqual(["new", "mid", "old"]);
    expect(waitingItems(rows, new Set(["mid"])).map(({ id }) => id)).toEqual(["new", "old"]);
  });
});

describe("the side panel's place in the list", () => {
  const items = waitingItems([review("a", "review", 3), review("b", "review", 2), review("c", "review", 1)]);

  it("shows the item that took a decided item's place, or the last one", () => {
    expect(resolveWaiting(items, { id: "b", index: 1 })?.item.id).toBe("b");
    const afterDeciding = items.filter(({ id }) => id !== "b");
    expect(resolveWaiting(afterDeciding, { id: "b", index: 1 })?.item.id).toBe("c");
    const lastDecided = items.filter(({ id }) => id !== "c");
    expect(resolveWaiting(lastDecided, { id: "c", index: 2 })?.item.id).toBe("b");
    expect(resolveWaiting([], { id: "c", index: 2 })).toBeNull();
  });

  it("steps through waiting items and through the board's claims", () => {
    expect(stepTarget({ kind: "waiting", id: "a", index: 0 }, items, 1)).toEqual({
      kind: "waiting",
      id: "b",
      index: 1,
    });
    expect(stepTarget({ kind: "waiting", id: "a", index: 0 }, items, -1)).toBeNull();
    const claim = { kind: "claim" as const, id: "y", order: ["x", "y", "z"] };
    expect(stepTarget(claim, items, 1)).toEqual({ ...claim, id: "z" });
    expect(stepTarget(claim, items, -1)).toEqual({ ...claim, id: "x" });
    expect(stepTarget({ ...claim, id: "z" }, items, 1)).toBeNull();
  });
});
