import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./i18n";
import { App } from "./App";
import en from "./i18n/en.json";
import zhCN from "./i18n/zh-CN.json";

const CLAIM = "11111111-1111-4111-8111-111111111111";
const LEMMA = "lean:22222222-2222-4222-8222-222222222222";
const PROOF_TASK = {
  claim_id: CLAIM,
  title: "Left zero",
  state: "ready",
  blocker: null,
  can_start: true,
  can_stop: false,
  scope: {
    token: "scope-1",
    proposition: "∀ n : Nat, 0 + n = n",
    basis: [{ label: "H", statement: "n is a natural number.", formal: { lean: { proposition: "n : Nat" } } }],
    omitted_basis: 0,
  },
  decisions: [],
  policy: null,
  receipt: null,
  local_model_allowed: false,
  adopted: false,
};
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

  it("shows the exact blocker and offers no authorization for an unconfirmed proof scope", async () => {
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST") return Response.json({}, { status: 405 });
      if (input === "/api/proof-task?claim=" + CLAIM) {
        return Response.json({
          ...PROOF_TASK,
          state: "blocked",
          can_start: false,
          blocker: "proof_task.scope_unconfirmed",
          scope: { token: null, proposition: null, basis: [], omitted_basis: 0 },
          decisions: [{ kind: "review_formalization", question: "Does the premise describe all natural numbers?" }],
        });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.proofTask.title }));
    const modal = within(await screen.findByRole("dialog", { name: en.proofTask.title }));
    expect(await modal.findByText(en.errors["proof_task.scope_unconfirmed"])).toBeTruthy();
    expect(modal.getByText("Does the premise describe all natural numbers?")).toBeTruthy();
    expect(modal.queryByRole("button", { name: en.proofTask.start })).toBeNull();
    expect(fetch.mock.calls.every(([, options]) => options?.method !== "POST")).toBe(true);
  });

  it("authorizes exactly the visible Lean scope with zero model calls by default", async () => {
    let started = false;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/proof-task/start" && init?.method === "POST") {
        started = true;
        return Response.json({ policy_id: "policy-1" });
      }
      if (input === "/api/proof-task?claim=" + CLAIM) {
        return Response.json({
          ...PROOF_TASK,
          ...(started && {
            state: "verified",
            can_start: false,
            receipt: { id: "receipt-1", kind: "lean", review_status: "pending" },
          }),
        });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.proofTask.title }));
    const modal = within(await screen.findByRole("dialog", { name: en.proofTask.title }));
    expect(await modal.findByText(PROOF_TASK.scope.proposition)).toBeTruthy();
    fireEvent.click(modal.getByText(en.proofTask.basis));
    expect(modal.getByText(PROOF_TASK.scope.basis[0]!.formal.lean.proposition)).toBeTruthy();
    const start = modal.getByRole<HTMLButtonElement>("button", { name: en.proofTask.start });
    expect(start.disabled).toBe(true);
    fireEvent.change(modal.getByLabelText(en.review.reason), { target: { value: "Exact scope reviewed" } });
    fireEvent.click(modal.getByLabelText(en.proofTask.authorize));
    fireEvent.click(start);
    expect(await modal.findByText(en.proofTask.states.verified)).toBeTruthy();
    expect(modal.queryByText(en.proofTask.adopted)).toBeNull();
    const post = fetch.mock.calls.find(
      ([path, options]) => path === "/api/proof-task/start" && options?.method === "POST",
    );
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      claim_id: CLAIM,
      token: PROOF_TASK.scope.token,
      model_rounds: 0,
      reason: "Exact scope reviewed",
    });
  });

  it("reads the current proof state when reopened and stops the exact authorized policy", async () => {
    let stopped = false;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/proof-task/stop" && init?.method === "POST") {
        stopped = true;
        return Response.json({ policy_id: "policy-1" });
      }
      if (input === "/api/proof-task?claim=" + CLAIM) {
        return Response.json({
          ...PROOF_TASK,
          state: stopped ? "stopped" : "running",
          can_start: false,
          can_stop: !stopped,
          policy: { id: "policy-1", fingerprint: "fingerprint", model_rounds: 0 },
          obligations: [{ proposition: "n + 0 = n", verified: false }],
        });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    const app = renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.proofTask.title }));
    const modal = within(await screen.findByRole("dialog", { name: en.proofTask.title }));
    expect(await modal.findByText(en.proofTask.states.running)).toBeTruthy();
    expect(modal.getByText("n + 0 = n")).toBeTruthy();
    fireEvent.change(modal.getByLabelText(en.proofTask.stopReason), {
      target: { value: "Pause this line of inquiry" },
    });
    fireEvent.click(modal.getByRole("button", { name: en.proofTask.stop }));
    expect(await modal.findByText(en.proofTask.states.stopped)).toBeTruthy();
    const post = fetch.mock.calls.find(
      ([path, options]) => path === "/api/proof-task/stop" && options?.method === "POST",
    );
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      claim_id: CLAIM,
      policy_id: "policy-1",
      reason: "Pause this line of inquiry",
    });
    app.unmount();
    renderApp("new-session");
    fireEvent.click(await screen.findByRole("button", { name: en.proofTask.title }));
    expect(await screen.findByText(en.proofTask.states.stopped)).toBeTruthy();
    expect(fetch.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
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
    const write = fetch.mock.calls.find(([path, init]) => path === "/api/theorems" && init?.method === "POST");
    expect(write?.[1]).toMatchObject({ mode: "same-origin", referrerPolicy: "origin", redirect: "error" });
  });
});

describe("catalogs", () => {
  it("English and Chinese have the same keys", () => {
    expect(keys(zhCN).sort()).toEqual(keys(en).sort());
  });
});
