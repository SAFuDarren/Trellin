import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import "./i18n";
import { App } from "./App";
import en from "./i18n/en.json";
import zhCN from "./i18n/zh-CN.json";

const CLAIM = "11111111-1111-4111-8111-111111111111";
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
    ],
    edges: [],
  },
  "/api/activity": { claim_id: CLAIM, activity: [], guidance: [], guidance_channels: ["local"] },
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

beforeAll(() => {
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
});

describe("catalogs", () => {
  it("English and Chinese have the same keys", () => {
    expect(keys(zhCN).sort()).toEqual(keys(en).sort());
  });
});
