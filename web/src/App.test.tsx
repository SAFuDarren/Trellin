import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./i18n";
import { App } from "./App";
import en from "./i18n/en.json";
import zhCN from "./i18n/zh-CN.json";

const CLAIM = "11111111-1111-4111-8111-111111111111";
const LEMMA = "lean:22222222-2222-4222-8222-222222222222";
const WORKSPACE = "33333333-3333-4333-8333-333333333333";
const EVIDENCE = "44444444-4444-4444-8444-444444444444";
const REVIEW_PATH = `/api/v1/workspaces/${WORKSPACE}/evidence/${EVIDENCE}/reviews`;
const EVENT_PATH = `/api/v1/workspaces/${WORKSPACE}/events`;
const REVIEW_ITEM = {
  kind: "review_evidence",
  item_id: EVIDENCE,
  claim_id: CLAIM,
  claim_title: "Left zero",
  waiting_since: "2026-01-01T00:00:00Z",
  priority: 2,
};
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

function renderApp(
  session: string | null,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
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
  window.history.replaceState(null, "", "#workbench");
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

  it("records an authenticated review with only the decision and reason", async () => {
    window.history.replaceState(null, "", "#review");
    let reviewed = false;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") {
        return Response.json({
          items: reviewed ? [] : [REVIEW_ITEM],
          review_identity: { workspace_id: WORKSPACE },
        });
      }
      if (input === REVIEW_PATH && init?.method === "POST") {
        reviewed = true;
        return Response.json({ id: "review", user_id: "local-reviewer" });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    const approve = await screen.findByRole("button", { name: en.review.approve });
    expect(screen.queryByLabelText(en.review.reviewer)).toBeNull();
    fireEvent.click(approve);
    expect(await screen.findByText(en.review.needReason)).toBeTruthy();
    expect(fetch.mock.calls.every(([, init]) => init?.method !== "POST")).toBe(true);
    fireEvent.change(screen.getByLabelText(en.review.reason), { target: { value: "  Exact scope verified.  " } });
    fireEvent.click(approve);
    expect(await screen.findByText(en.review.approved)).toBeTruthy();
    expect(await screen.findByText(en.review.allDone)).toBeTruthy();
    const writes = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes).toHaveLength(1);
    const [path, request] = writes[0]!;
    expect(path).toBe(REVIEW_PATH);
    expect(JSON.parse(String(request?.body))).toEqual({ decision: "approve", reason: "Exact scope verified." });
    const headers = new Headers(request?.headers);
    expect(headers.get("X-RM-Session")).toBe("token");
    expect(headers.get("Idempotency-Key")).toMatch(/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
  });

  it("retries the same authenticated review with its original key and preserves the chosen route", async () => {
    window.history.replaceState(null, "", "#review");
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") {
        return Response.json({ items: [REVIEW_ITEM], review_identity: { workspace_id: WORKSPACE } });
      }
      if (input === REVIEW_PATH && init?.method === "POST") {
        return Response.json(
          { error: { code: "review.identity_required", message: "Missing local identity." } },
          { status: 403 },
        );
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    const approve = await screen.findByRole("button", { name: en.review.approve });
    const sendAndWait = async (button: HTMLElement) => {
      fireEvent.click(button);
      expect(await screen.findByText(en.errors["review.identity_required"])).toBeTruthy();
    };
    fireEvent.change(screen.getByLabelText(en.review.reason), { target: { value: "Reviewed frozen scope." } });
    await sendAndWait(approve);
    await sendAndWait(approve);
    const returnEvidence = screen.getByRole("button", { name: en.review.return });
    await sendAndWait(returnEvidence);
    const writes = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes.map(([path]) => path)).toEqual([REVIEW_PATH, REVIEW_PATH, REVIEW_PATH]);
    const keys = writes.map(([, init]) => new Headers(init?.headers).get("Idempotency-Key"));
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
    expect(keys[2]).not.toBe(keys[0]);
  });

  it("retains the display name and original request on a legacy review inbox", async () => {
    window.history.replaceState(null, "", "#review");
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") return Response.json({ items: [REVIEW_ITEM] });
      if (input === "/api/review" && init?.method === "POST") return Response.json({ id: "legacy-review" });
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    fireEvent.change(await screen.findByLabelText(en.review.reviewer), { target: { value: "Reviewer" } });
    fireEvent.change(screen.getByLabelText(en.review.reason), { target: { value: "Reviewed exact scope." } });
    fireEvent.click(screen.getByRole("button", { name: en.review.approve }));
    expect(await screen.findByText(en.review.approved)).toBeTruthy();
    const writes = fetch.mock.calls.filter(([, init]) => init?.method === "POST");
    expect(writes).toHaveLength(1);
    const [path, request] = writes[0]!;
    expect(path).toBe("/api/review");
    expect(JSON.parse(String(request?.body))).toEqual({
      evidence_id: EVIDENCE,
      action: "approve",
      actor: "Reviewer",
      reason: "Reviewed exact scope.",
    });
    expect(new Headers(request?.headers).has("Idempotency-Key")).toBe(false);
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/events"))).toBe(false);
  });

  it("refreshes a review once for the owned event and resumes from that event instead of a later inbox cursor", async () => {
    window.history.replaceState(null, "", "#review");
    let pending = true;
    let head = "9007199254740992";
    const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") {
        return Response.json({
          items: pending ? [REVIEW_ITEM] : [],
          workspace_events: { workspace_id: WORKSPACE, cursor: head },
        });
      }
      if (input === EVENT_PATH && init?.method === "GET") {
        return new Response(new ReadableStream<Uint8Array>({ start: (stream) => streams.push(stream) }), {
          headers: { "Content-Type": "text/event-stream; charset=utf-8" },
        });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const app = renderApp("active", client);
    expect(await screen.findByRole("button", { name: en.review.approve })).toBeTruthy();
    await waitFor(() => expect(streams).toHaveLength(1));
    const opened = fetch.mock.calls.find(([path]) => path === EVENT_PATH)?.[1];
    expect(new Headers(opened?.headers).get("Last-Event-ID")).toBe(head);
    expect(new Headers(opened?.headers).get("X-RM-Session")).toBe("active");
    expect(opened).toMatchObject({ credentials: "omit", mode: "same-origin", redirect: "error" });
    const id = "9007199254740993";
    const body = {
      id,
      type: "evidence.reviewed",
      workspace_id: WORKSPACE,
      schema_version: 1,
      occurred_at: "2026-11-19T00:00:00Z",
      actor: { kind: "user", id: CLAIM },
      subject: { kind: "evidence", id: EVIDENCE },
      data: { claim_id: CLAIM, decision_id: EVIDENCE, action: "approve" },
    };
    const frame = (event: typeof body) =>
      `id: ${event.id}\nevent: evidence.reviewed\ndata: ${JSON.stringify(event)}\n\n`;
    head = "9007199254740995";
    pending = false;
    streams[0]!.enqueue(
      new TextEncoder().encode(frame(body) + frame(body) + frame({ ...body, id: head, workspace_id: CLAIM })),
    );
    expect(await screen.findByText(en.review.allDone)).toBeTruthy();
    expect(invalidate.mock.calls.map(([filter]) => filter?.queryKey)).toEqual([
      ["inbox", "active"],
      ["atlas"],
      ["graph", CLAIM],
    ]);
    streams[0]!.close();
    await waitFor(() => expect(streams).toHaveLength(2), { timeout: 2500 });
    const resumed = fetch.mock.calls.filter(([path]) => path === EVENT_PATH)[1]?.[1];
    expect(new Headers(resumed?.headers).get("Last-Event-ID")).toBe(id);
    app.unmount();
    expect(resumed?.signal?.aborted).toBe(true);
  });

  it("does not start a new session's feed from a previous session's cached inbox", async () => {
    const otherPath = `/api/v1/workspaces/${CLAIM}/events`;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      const session = new Headers(init?.headers).get("X-RM-Session");
      if (input === "/api/inbox") {
        return Response.json({
          items: [],
          workspace_events:
            session === "first" ? { workspace_id: WORKSPACE, cursor: "6" } : { workspace_id: CLAIM, cursor: "2" },
        });
      }
      if (input.endsWith("/events")) {
        return new Response(new ReadableStream<Uint8Array>(), { headers: { "Content-Type": "text/event-stream" } });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const app = renderApp("first", client);
    await waitFor(() => expect(fetch.mock.calls.some(([path]) => path === EVENT_PATH)).toBe(true));
    await act(async () => {
      app.rerender(
        <QueryClientProvider client={client}>
          <App session="second" />
        </QueryClientProvider>,
      );
    });
    await waitFor(() => expect(fetch.mock.calls.some(([path]) => path === otherPath)).toBe(true));
    const feeds = fetch.mock.calls.filter(([path]) => path.endsWith("/events"));
    expect(feeds.map(([url, init]) => [url, new Headers(init?.headers).get("Last-Event-ID")])).toEqual([
      [EVENT_PATH, "6"],
      [otherPath, "2"],
    ]);
    expect(feeds[0]?.[1]?.signal?.aborted).toBe(true);
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

  it("renames a numbered claim and opens the exact node from a previous code", async () => {
    let code = "FLOW";
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/claim-code" && init?.method === "POST") {
        code = JSON.parse(String(init.body)).code;
        return Response.json({ code });
      }
      if (input === "/api/claim-code?code=FLOW-09") return Response.json({ claim_id: CLAIM, node_id: LEMMA });
      if (input.startsWith("/api/graph?"))
        return Response.json({
          claim: { id: CLAIM, title: "Left zero", status: "open", domain: "lean-core", code },
          nodes: [
            { id: `goal:${CLAIM}`, title: "Original claim", kind: "goal", code: code + "-01" },
            { id: LEMMA, title: "A helper lemma", kind: "lemma", code: code + "-09" },
          ],
          edges: [],
        });
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.claimCodes.title }));
    const modal = within(await screen.findByRole("dialog", { name: en.claimCodes.title }));
    fireEvent.change(modal.getByLabelText(en.claimCodes.claim), { target: { value: "new" } });
    fireEvent.click(modal.getByRole("button", { name: en.claimCodes.save }));
    expect(await modal.findByText(en.claimCodes.saved)).toBeTruthy();
    expect(await screen.findAllByText("NEW-09")).not.toHaveLength(0);
    fireEvent.change(modal.getByLabelText(en.claimCodes.reference), { target: { value: "flow-09" } });
    fireEvent.click(modal.getByRole("button", { name: en.claimCodes.open }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /A helper lemma/ }).getAttribute("aria-pressed")).toBe("true"),
    );
    const post = fetch.mock.calls.find(([path, init]) => path === "/api/claim-code" && init?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ claim_id: CLAIM, code: "NEW" });
    expect(fetch.mock.calls.filter(([path]) => path === "/api/claim-code?code=FLOW-09")).toHaveLength(1);
  });

  it("opens an archived claim from an empty recent list and reports an absent historical node", async () => {
    const fetch = vi.fn(async (input: string) => {
      if (input === "/api/claims") return Response.json({ claims: [], claim_id: null, claim_codes: true });
      if (input === "/api/claim-code?code=OLD-19") {
        return Response.json({ claim_id: CLAIM, node_id: "lean:historical-node" });
      }
      if (input === "/api/graph?claim=" + CLAIM)
        return Response.json({
          claim: { id: CLAIM, title: "Archived theorem", status: "archived", domain: "lean-core", code: "OLD" },
          nodes: [{ id: `goal:${CLAIM}`, title: "Original claim", kind: "goal", code: "OLD-01" }],
          edges: [],
        });
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.claimCodes.title }));
    const modal = within(await screen.findByRole("dialog", { name: en.claimCodes.title }));
    expect(modal.queryByLabelText(en.claimCodes.claim)).toBeNull();
    fireEvent.change(modal.getByLabelText(en.claimCodes.reference), { target: { value: "OLD-19" } });
    fireEvent.click(modal.getByRole("button", { name: en.claimCodes.open }));
    expect(await screen.findByRole("heading", { level: 1, name: "Archived theorem" })).toBeTruthy();
    expect(await screen.findByText(en.claimCodes.nodeAbsent)).toBeTruthy();
    expect(screen.queryByText(en.notice.noClaims)).toBeNull();
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

  it("passes an optional code at confirmed goal intake only when supported", async () => {
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/theorems") {
        return Response.json(
          init?.method === "POST" ? { kind: "goal", claim_id: CLAIM } : { drafts: [], claim_codes: true },
        );
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    fireEvent.click(await screen.findByRole("button", { name: en.projectEntry.addTheorem }));
    const modal = within(await screen.findByRole("dialog"));
    fireEvent.change(await modal.findByLabelText(en.claimCodes.optional), { target: { value: "test1" } });
    fireEvent.change(modal.getByLabelText(en.projectEntry.title), { target: { value: "Reflexivity" } });
    fireEvent.change(modal.getByLabelText(en.projectEntry.theorem.wording), { target: { value: "Zero equals zero." } });
    fireEvent.change(modal.getByLabelText(en.projectEntry.lean), { target: { value: "(0 : Nat) = 0" } });
    fireEvent.change(modal.getByLabelText(en.review.reviewer), { target: { value: "Reviewer" } });
    fireEvent.change(modal.getByLabelText(en.review.reason), { target: { value: "Exact statement checked" } });
    fireEvent.click(modal.getByLabelText(en.projectEntry.theorem.goalReview));
    fireEvent.click(modal.getByRole("button", { name: en.projectEntry.theorem.saveGoal }));
    await waitFor(() => {
      const post = fetch.mock.calls.find(([path, init]) => path === "/api/theorems" && init?.method === "POST");
      expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ code: "TEST1", kind: "goal", confirmed: true });
    });
  });
});

describe("catalogs", () => {
  it("English and Chinese have the same keys", () => {
    expect(keys(zhCN).sort()).toEqual(keys(en).sort());
  });
});
