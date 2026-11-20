import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useReleaseUpdates, type ReleaseUpdateState } from "./releaseUpdates";

const DAY_MS = 86_400_000;

function openWorkbench() {
  const client = new QueryClient();
  return renderHook(() => useReleaseUpdates("local-app"), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

async function advance(milliseconds: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("waits for consent and the next daily check, and resumes from the saved time after reopening", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-19T00:00:00Z"));
  let state: ReleaseUpdateState = {
    installed_version: "1.9.0",
    status: "current",
    automatic: false,
    last_checked_at: (Date.now() - DAY_MS + 10_000) / 1000,
    available: null,
  };
  const actions: string[] = [];
  const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      actions.push(body.action);
      if (body.action === "automatic") state = { ...state, automatic: body.enabled };
      if (body.action === "auto_check") {
        state = { ...state, status: "unavailable", last_checked_at: Math.floor(Date.now() / 1000) };
      }
    }
    return Response.json(state);
  });
  vi.stubGlobal("fetch", fetch);
  const first = openWorkbench();
  await advance(1_000);
  expect(first.result.current.state?.automatic).toBe(false);
  expect(actions).toEqual([]);
  act(() => first.result.current.submit({ action: "automatic", enabled: true }));
  await advance(8_000);
  expect(first.result.current.state?.automatic).toBe(true);
  expect(actions).toEqual(["automatic"]);
  await advance(2_000);
  expect(actions).toEqual(["automatic", "auto_check"]);
  expect(first.result.current.state?.status).toBe("unavailable");
  first.unmount();

  const reopened = openWorkbench();
  await advance(DAY_MS - 5_000);
  expect(actions).toEqual(["automatic", "auto_check"]);
  await advance(10_000);
  expect(actions).toEqual(["automatic", "auto_check", "auto_check"]);
  reopened.unmount();
  await advance(DAY_MS);
  expect(actions).toHaveLength(3);
});
