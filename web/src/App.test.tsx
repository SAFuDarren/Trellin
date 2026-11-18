import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

beforeAll(() => {
  // jsdom lacks ResizeObserver.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  // jsdom does not implement modal dialogs.
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new Event("close"));
  };
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

  it("organizes and reviews research notes before any claim exists", async () => {
    const wording = "Every natural number equals itself.";
    let submitted: unknown;
    let decision: unknown;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input === "/api/claims") return Response.json({ claim_id: null, claims: [] });
        if (input === "/api/project-intake/models") return Response.json({ models: ["local-text"] });
        if (input === "/api/project-intake" && init?.method === "POST") {
          submitted = JSON.parse(String(init.body));
          return Response.json({
            id: "draft",
            sha256: "exact-draft-hash",
            title: "Research notes",
            confirmed: false,
            items: [
              {
                id: "i1",
                kind: "theorem",
                natural: wording,
                formal: { lean: { proposition: "∀ n : Nat, n = n" } },
                status: "new",
              },
            ],
          });
        }
        if (input === "/api/project-intake/confirm") {
          decision = JSON.parse(String(init?.body));
          return Response.json({ confirmed: true });
        }
        if (input === "/api/project-intake") return Response.json({ drafts: [] });
        return Response.json(responses[input.split("?")[0]!] ?? {});
      }),
    );
    renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.projectEntry.organize }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(await dialog.findByRole("option", { name: "local-text" })).toBeTruthy();
    fireEvent.change(dialog.getByLabelText(en.projectEntry.intake.notes), { target: { value: wording } });
    fireEvent.click(dialog.getByRole("button", { name: en.projectEntry.intake.analyze }));
    expect(await dialog.findByText(wording)).toBeTruthy();
    expect(dialog.getByText("∀ n : Nat, n = n")).toBeTruthy();
    expect(submitted).toMatchObject({ notes: wording, model: "local-text" });
    expect(decision).toBeUndefined();
    fireEvent.change(dialog.getByRole("combobox", { name: "Decision for i1" }), { target: { value: "accept" } });
    fireEvent.change(dialog.getByLabelText(en.review.reviewer), { target: { value: "Reviewer" } });
    fireEvent.change(dialog.getByLabelText(en.review.reason), { target: { value: "Checked against the original" } });
    fireEvent.click(dialog.getByRole("button", { name: en.projectEntry.intake.confirm }));
    expect(await dialog.findByText(en.projectEntry.intake.reviewed)).toBeTruthy();
    expect(decision).toMatchObject({
      id: "draft",
      sha256: "exact-draft-hash",
      actor: "Reviewer",
      decisions: { i1: { action: "accept", reason: "Checked against the original" } },
    });
  });

  it("saves and confirms a manual library theorem without a model", async () => {
    let submission: unknown;
    let confirmation: unknown;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/claims") return Response.json({ claim_id: null, claims: [] });
      if (input === "/api/theorems" && init?.method === "POST") {
        submission = JSON.parse(String(init.body));
        return Response.json({ kind: "library", status: "draft", id: "draft", sha256: "exact-theorem-hash" });
      }
      if (input === "/api/theorems/confirm") {
        confirmation = JSON.parse(String(init?.body));
        return Response.json({ kind: "library", status: "confirmed", id: "draft", sha256: "exact-theorem-hash" });
      }
      if (input === "/api/theorems") return Response.json({ drafts: [] });
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.projectEntry.addTheorem }));
    const dialog = within(await screen.findByRole("dialog"));
    fireEvent.change(dialog.getByLabelText(en.projectEntry.theorem.kind), { target: { value: "library" } });
    fireEvent.change(dialog.getByLabelText(en.projectEntry.title), { target: { value: "Reflexivity over Nat" } });
    fireEvent.change(dialog.getByLabelText(en.projectEntry.theorem.wording), {
      target: { value: "Each natural number equals itself." },
    });
    fireEvent.change(dialog.getByLabelText(en.projectEntry.lean), { target: { value: "∀ n : Nat, n = n" } });
    fireEvent.click(dialog.getByRole("button", { name: en.projectEntry.theorem.saveDraft }));
    expect(await dialog.findByText(en.projectEntry.theorem.saved)).toBeTruthy();
    expect(dialog.getByText("∀ n : Nat, n = n")).toBeTruthy();
    expect(confirmation).toBeUndefined();
    fireEvent.change(dialog.getByLabelText(en.review.reviewer), { target: { value: "Reviewer" } });
    fireEvent.change(dialog.getByLabelText(en.review.reason), { target: { value: "Checked the exact statement" } });
    fireEvent.click(dialog.getByRole("button", { name: en.projectEntry.theorem.confirm }));
    await waitFor(() => expect(confirmation).toMatchObject({ id: "draft", sha256: "exact-theorem-hash" }));
    expect(await dialog.findByText(en.projectEntry.theorem.confirmed)).toBeTruthy();
    expect(submission).toMatchObject({ kind: "library", title: "Reflexivity over Nat", lean: "∀ n : Nat, n = n" });
    expect(fetch.mock.calls.every(([path]) => !path.startsWith("/api/project-intake"))).toBe(true);
  });
});

describe("catalogs", () => {
  it("English and Chinese have the same keys", () => {
    expect(keys(zhCN).sort()).toEqual(keys(en).sort());
  });
});
