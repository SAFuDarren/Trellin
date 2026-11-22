import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { useWorkspaceInbox, workspaceInboxScope } from "./workspaceInbox";
import type { ActivityRow } from "./workspaceActivity";

const MAIN = "11111111-2222-4333-8444-555555555555";
const OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const ITEM: Extract<ActivityRow, { source: "review" }> = {
  source: "review",
  id: "review:review_evidence:fixture",
  state: "review",
  item: {
    kind: "review_evidence",
    item_id: "fixture",
    claim_id: null,
    claim_title: null,
    waiting_since: "2026-11-22T00:00:00Z",
    question: "Review this result.",
    detail: null,
    priority: 1,
  },
};

it("accepts only a verified, consistent workspace identity", () => {
  expect(workspaceInboxScope("one", MAIN, MAIN)).toBe(`one/${MAIN}`);
  expect(workspaceInboxScope("one", undefined, MAIN)).toBe(`one/${MAIN}`);
  expect(workspaceInboxScope("one", MAIN, OTHER)).toBeNull();
  expect(workspaceInboxScope("one", undefined, "unnamed project")).toBeNull();
});

it("scopes marks and their undo to the workspace and session", () => {
  const { result, rerender } = renderHook(({ scope, item }) => useWorkspaceInbox(scope, [item]), {
    initialProps: { scope: `one/${MAIN}`, item: ITEM },
  });
  expect(result.current.unread).toBe(1);
  act(() => result.current.mark(ITEM, "read"));
  expect(result.current.rows[0]?.mark).toBe("read");
  act(() => result.current.mark(ITEM, "archived"));
  expect(result.current.rows[0]?.mark).toBe("archived");
  act(() => result.current.undo());
  expect(result.current.rows[0]?.mark).toBe("read");
  rerender({ scope: `two/${MAIN}`, item: ITEM });
  expect(result.current.unread).toBe(1);
  expect(result.current.undoId).toBeUndefined();
  rerender({ scope: `one/${OTHER}`, item: ITEM });
  expect(result.current.unread).toBe(1);
  expect(result.current.undoId).toBeUndefined();
  rerender({ scope: `one/${MAIN}`, item: { ...ITEM, time: { kind: "updated", at: "2026-11-22T01:00:00Z" } } });
  expect(result.current.unread).toBe(1);
});
