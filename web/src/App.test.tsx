import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./i18n";
import { App } from "./App";
import en from "./i18n/en.json";
import zhCN from "./i18n/zh-CN.json";

const CLAIM = "11111111-1111-4111-8111-111111111111";
const LEMMA = "lean:22222222-2222-4222-8222-222222222222";
const responses: Record<string, unknown> = {
  "/api/claims": { claim_id: CLAIM, claims: [{ id: CLAIM, title: "Left zero", status: "open", domain: "lean-core" }] },
  "/api/atlas": { claims: [] },
  "/api/graph": {
    claim: { id: CLAIM, title: "Left zero", status: "open", domain: "lean-core", statement_id: "s" },
    nodes: [
      {
        id: `goal:${CLAIM}`,
        kind: "goal",
        title: "Original claim",
        status: "open",
        formalization: "∀ n : Nat, 0 + n = n",
      },
      { id: LEMMA, kind: "lemma", title: "A helper lemma", status: "verified", formalization: "True" },
    ],
    edges: [],
  },
  "/api/activity": { claim_id: CLAIM, activity: [], guidance: [], guidance_channels: ["local"] },
  "/api/inbox": { items: [] },
  "/api/team": {
    jobs: [],
    agents: [],
    verifier: { runs: 0, last_at: null, failures: 0, average_ms: null },
    services: [],
    machine: { name: "test", system: "", arch: "", python: "" },
    budget: { policy: null, month: { period: "" }, routes: [] },
  },
};

function renderApp(session: string | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App session={session} />
    </QueryClientProvider>,
  );
}

function keys(value: object, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, child]) =>
    typeof child === "object" && child ? keys(child, `${prefix}${key}.`) : [`${prefix}${key}`],
  );
}

beforeEach(() => {
  // jsdom lacks ResizeObserver.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  vi.mocked(globalThis.fetch as never as { mockReset?: () => void })?.mockReset?.();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("asks for the launch link without a session", () => {
    renderApp(null);
    expect(screen.getByText(en.session.missing)).toBeTruthy();
  });

  it("shows the claim, its graph and its details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => new Response(JSON.stringify(responses[input.split("?")[0]!] ?? {}))),
    );
    renderApp("token");
    expect(await screen.findByRole("heading", { level: 1, name: "Left zero" })).toBeTruthy();
    const node = await screen.findByRole("button", { name: /Original claim/ });
    expect(node.getAttribute("aria-pressed")).toBe("true");
    const detail = screen.getByLabelText(en.review.detail);
    expect(within(detail).getByText("∀ n : Nat, 0 + n = n")).toBeTruthy();
  });

  it("shows the server's error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ error: "Forbidden." }), { status: 403 })),
    );
    renderApp("token");
    expect((await screen.findByText("Forbidden.")).getAttribute("data-error")).toBe("true");
  });

  it("restores the desktop node and reports subsequent workbench selections", async () => {
    const postMessage = vi.fn();
    vi.stubGlobal("trellinPosition", { claimId: CLAIM, nodeId: LEMMA });
    vi.stubGlobal("webkit", { messageHandlers: { trellinPosition: { postMessage } } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => new Response(JSON.stringify(responses[input.split("?")[0]!] ?? {}))),
    );
    renderApp("token");
    const lemma = await screen.findByRole("button", { name: /A helper lemma/ });
    expect(lemma.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /Original claim/ }));
    await waitFor(() => expect(postMessage).toHaveBeenLastCalledWith({ claimId: CLAIM, nodeId: `goal:${CLAIM}` }));
  });
});

describe("catalogs", () => {
  it("English and Chinese have the same keys", () => {
    expect(keys(zhCN).sort()).toEqual(keys(en).sort());
  });
});
