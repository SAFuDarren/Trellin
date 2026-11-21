import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import "./i18n";
import type { ReleaseUpdateState } from "./api/releaseUpdates";
import { App } from "./App";
import en from "./i18n/en.json";
import zhCN from "./i18n/zh-CN.json";
import type { GraphSnapshot } from "./workbench/types";

const CLAIM = "11111111-1111-4111-8111-111111111111";
const NATIVE_CONNECTIONS = [
  { provider: "codex-cli", id: "codex", label: "Codex", login: "codex login", model: "gpt-fixture" },
  { provider: "claude-code", id: "claude", label: "Claude Code", login: "claude auth login", model: "sonnet" },
] as const;
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
  "/api/project-canvas": { positions: {}, links: [] },
  "/api/project-canvas/claims": { claims: [{ id: CLAIM, title: "Left zero", status: "open", domain: "lean-core" }] },
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

async function openResearchAction(name: string) {
  fireEvent.click(await screen.findByRole("button", { name: en.canvasShell.researchActions }));
  fireEvent.click(await screen.findByRole("menuitem", { name }));
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

  it("compares prepared conjectures with the full basis and confirms only the explicit choice", async () => {
    const goal = "55555555-5555-4555-8555-555555555555";
    const first = "66666666-6666-4666-8666-666666666666";
    const second = "77777777-7777-4777-8777-777777777777";
    const legacy = "88888888-8888-4888-8888-888888888888";
    const basis = "99999999-9999-4999-8999-999999999999";
    const frameDigest = "a".repeat(64);
    const candidateDigest = "b".repeat(64);
    const reason = "This exact target matches my intended scope.";
    const proposal = {
      goal_id: goal,
      title: "Compare two equality statements",
      objective: "Discuss which equality follows from the approved basis.",
      domain: "lean-core",
      sensitivity: "restricted",
      frame_sha256: frameDigest,
      status: "ready",
      claim_id: null,
      basis: [
        {
          id: basis,
          version: 2,
          sha256: "c".repeat(64),
          kind: "hypothesis",
          wording: "The approved equality on naturals.",
          formal: { lean: { proposition: "n = n", parameters: [{ name: "n", type: "Nat" }] } },
          source: { note: "Recorded equality source" },
          stale: false,
        },
      ],
      discussion: {
        state: "available",
        alternatives: [{ wording: "A broader archived idea.", reason: "Keep it for discussion.", basis_ids: [basis] }],
      },
      candidates: [
        {
          id: first,
          sha256: "d".repeat(64),
          wording: "Formal alternative one.",
          formal: { lean: { proposition: "True" } },
          provenance_kind: "human",
        },
        {
          id: second,
          sha256: candidateDigest,
          wording: "Formal alternative two.",
          formal: { lean: { proposition: "n = 0 + n", parameters: [{ name: "n", type: "Nat" }] } },
          provenance_kind: "ai",
        },
        {
          id: legacy,
          sha256: "e".repeat(64),
          wording: "Older record.",
          formal: { formula: "x = x" },
          provenance_kind: "program",
        },
      ],
    };
    let selected = false;
    const requests: unknown[] = [];
    const inboxItem = {
      kind: "choose_conjecture",
      item_id: goal,
      claim_id: null,
      claim_title: null,
      detail: { title: proposal.title },
      waiting_since: "2026-01-01T00:00:00Z",
      priority: 2,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path === "/api/inbox") return Response.json({ items: selected ? [] : [inboxItem] });
        if (path === `/api/goal-review?goal=${goal}`) {
          return Response.json(
            selected ? { ...proposal, status: "selected", claim_id: CLAIM, selected_candidate_id: second } : proposal,
          );
        }
        if (path === "/api/goal-review/select" && init?.method === "POST") {
          requests.push(JSON.parse(String(init.body)));
          selected = true;
          return Response.json({ claim_id: CLAIM });
        }
        return Response.json(responses[path.split("?")[0]!] ?? {});
      }),
    );
    window.history.replaceState(null, "", "#review");
    renderApp("token");
    expect(await screen.findByRole("link", { name: new RegExp(proposal.title) })).toHaveProperty(
      "hash",
      `#review/goal%3A${goal}`,
    );
    expect(await screen.findByText("Version 2", { exact: false })).toBeTruthy();
    expect(screen.getByText("Recorded equality source", { exact: false })).toBeTruthy();
    expect(screen.getByText("A broader archived idea.")).toBeTruthy();
    expect(screen.getByText("n = 0 + n")).toBeTruthy();
    expect(screen.getAllByText(en.detail.parameters)).toHaveLength(2);
    expect(screen.queryByRole("radio", { name: "Older record." })).toBeNull();
    expect(screen.getByText("x = x", { exact: false })).toBeTruthy();
    const confirm = screen.getByRole("button", { name: en.goalReview.confirm });
    expect(confirm).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("radio", { name: "Formal alternative two." }));
    fireEvent.submit(confirm.closest("form")!);
    expect(requests).toHaveLength(0);
    fireEvent.change(screen.getByRole("textbox", { name: en.review.reason }), { target: { value: reason } });
    fireEvent.click(confirm);
    expect(await screen.findByRole("heading", { level: 2, name: "Left zero" })).toBeTruthy();
    expect(requests).toEqual([
      { goal_id: goal, frame_sha256: frameDigest, candidate_id: second, candidate_sha256: candidateDigest, reason },
    ]);
  });

  it("finds local related work from an approved open goal without changing the waiting count", async () => {
    const first = { id: "55555555-5555-4555-8555-555555555555", title: "First open research goal" };
    const second = { id: "66666666-6666-4666-8666-666666666666", title: "Second open research goal" };
    const basis = "77777777-7777-4777-8777-777777777777";
    const source = "88888888-8888-4888-8888-888888888888";
    const needle = "doi:10.9999/fixture";
    const matches = [
      {
        kind: "source",
        id: source,
        claim_id: CLAIM,
        exact_key: needle,
        title: "Recorded source",
        state: "unconfirmed",
      },
      {
        kind: "basis",
        id: basis,
        claim_id: null,
        exact_key: "fixture.eq",
        title: "Admitted equality",
        state: "admitted",
      },
    ];
    const searches: { params: unknown; signal: AbortSignal | null | undefined }[] = [];
    let completeLate: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path === "/api/inbox") return Response.json({ items: [], open_research_goals: [first, second] });
        if (path.startsWith("/api/goal-review?goal=")) {
          const frame = [first, second].find((item) => path.endsWith(item.id));
          if (frame)
            return Response.json({
              goal_id: frame.id,
              title: frame.title,
              objective: "Use the approved frame to explore simple examples.",
              domain: "lean-core",
              sensitivity: "restricted",
              frame_sha256: "a".repeat(64),
              status: "ready",
              claim_id: null,
              basis: [],
              discussion: { state: "none", alternatives: [] },
              candidates: [],
            });
        }
        if (path === "/api/goal-review/related") {
          expect(init?.method).toBe("POST");
          const params = JSON.parse(String(init?.body));
          searches.push({ params, signal: init?.signal });
          if (params.query === needle) return Response.json({ matches });
          if (params.query === "nothing") return Response.json({ matches: [] });
          if (params.query === "failed") return Response.json({ error: "fixture-error" }, { status: 503 });
          return await new Promise<Response>((resolve) => {
            completeLate = resolve;
          });
        }
        if (path === "/api/library") return Response.json({ items: [{ id: basis }] });
        return Response.json(responses[path.split("?")[0]!] ?? {});
      }),
    );
    window.history.replaceState(null, "", "#review");
    renderApp("token");
    const goalLink = await screen.findByRole("link", { name: first.title });
    expect(goalLink).toHaveProperty("hash", `#review/goal%3A${first.id}`);
    expect(screen.getByText(en.review.openGoals)).toBeTruthy();
    expect(screen.getByText(en.review.waiting.replace("{{count}}", "0"))).toBeTruthy();
    expect(searches).toHaveLength(0);
    fireEvent.click(goalLink);
    expect(await screen.findByRole("heading", { name: first.title })).toBeTruthy();
    expect(screen.getByText(en.goalReview.status.open)).toBeTruthy();
    expect(screen.queryByRole("button", { name: en.goalReview.confirm })).toBeNull();

    function search(query: string) {
      const field = screen.getByRole("searchbox", { name: en.goalReview.related.query });
      fireEvent.change(field, { target: { value: query } });
      fireEvent.click(screen.getByRole("button", { name: en.goalReview.related.search }));
    }
    search(" " + needle + " ");
    const results = await screen.findByRole("list", { name: en.goalReview.related.results });
    expect(within(results).getByText(needle)).toBeTruthy();
    expect(within(results).getByText(en.goalReview.related.kind.source)).toBeTruthy();
    expect(within(results).getByText(en.goalReview.related.state.unconfirmed)).toBeTruthy();
    expect(within(results).getByText(en.goalReview.related.kind.basis)).toBeTruthy();
    expect(within(results).getByRole("button", { name: en.goalReview.related.openClaim })).toBeTruthy();
    expect(await within(results).findByRole("link", { name: en.goalReview.related.openLibrary })).toHaveProperty(
      "hash",
      `#library/~${basis}`,
    );
    expect(searches[0]?.params).toEqual({ goal_id: first.id, query: needle });
    expect(window.location.hash).toBe(`#review/goal%3A${first.id}`);

    search("nothing");
    expect(await screen.findByText(en.goalReview.related.empty)).toBeTruthy();
    search("failed");
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", en.goalReview.related.failed);
    search("delayed result");
    expect(screen.getByText(en.goalReview.related.searching)).toBeTruthy();
    expect(completeLate).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: second.title }));
    expect(await screen.findByRole("heading", { name: second.title })).toBeTruthy();
    expect(searches[3]?.signal?.aborted).toBe(true);
    await act(async () => completeLate!(Response.json({ matches })));
    expect(screen.queryByText("Recorded source")).toBeNull();
    expect(screen.getByRole("searchbox", { name: en.goalReview.related.query })).toHaveProperty("value", "");
  });

  it("reopens a stale goal review by its saved link after it leaves the inbox", async () => {
    const goal = "55555555-5555-4555-8555-555555555555";
    const goalReview = {
      goal_id: goal,
      title: "Prior research scope",
      objective: "A basis entry was withdrawn.",
      domain: "lean-core",
      sensitivity: "public",
      frame_sha256: "a".repeat(64),
      status: "stale",
      claim_id: null,
      basis: [
        {
          id: "66666666-6666-4666-8666-666666666666",
          version: 2,
          sha256: "b".repeat(64),
          kind: "hypothesis",
          wording: "A retained withdrawn premise.",
          formal: { lean: { proposition: "True" } },
          stale: true,
        },
      ],
      discussion: { state: "unavailable", alternatives: [] },
      candidates: [
        {
          id: "77777777-7777-4777-8777-777777777777",
          sha256: "c".repeat(64),
          wording: "An archived formalization.",
          formal: { lean: { proposition: "True" } },
          provenance_kind: "human",
        },
      ],
    };
    window.history.replaceState(null, "", `#review/goal:${goal}`);
    const fetch = vi.fn(async (path: string) => {
      if (path === `/api/goal-review?goal=${goal}`) return Response.json(goalReview);
      return Response.json(responses[path.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    expect(await screen.findByRole("heading", { name: goalReview.title })).toBeTruthy();
    expect(screen.getByText(en.goalReview.statusHelp.stale)).toBeTruthy();
    expect(screen.getByText(en.goalReview.basisStale, { exact: false })).toBeTruthy();
    expect(screen.getByText("An archived formalization.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: en.goalReview.confirm })).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
    expect(fetch.mock.calls.some(([path]) => path === "/api/goal-review/select")).toBe(false);
  });

  it("shows the claim, its graph and its details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => new Response(JSON.stringify(responses[input.split("?")[0]!] ?? {}))),
    );
    renderApp("token");
    expect(await screen.findByRole("heading", { level: 2, name: "Left zero" })).toBeTruthy();
    const node = await screen.findByRole("button", { name: /^Original claim/ });
    expect(node.getAttribute("aria-pressed")).toBe("true");
    const detail = screen.getByLabelText(en.review.detail);
    expect(within(detail).getByText("∀ n : Nat, 0 + n = n")).toBeTruthy();
    expect(screen.queryByRole("button", { name: en.lemmaPlan.title })).toBeNull();
  });

  it("offers local lemmas for the confirmed statement and opens the recorded graph result", async () => {
    const initial = responses["/api/graph"] as GraphSnapshot;
    const goal = initial.nodes[0]!;
    const statement = { statement_id: "s", statement_version: 2, statement_sha256: "a".repeat(64) };
    let verified = false;
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/api/lemma-plans" && init?.method === "POST") {
        verified = true;
        return Response.json({ verified: 1 });
      }
      if (path.startsWith("/api/graph?")) {
        const nodes = [{ ...goal, formalization_status: "approved", reference: statement }, ...initial.nodes.slice(1)];
        if (verified) nodes.push({ id: "local-lemma:checked", kind: "local_lemma", title: "Checked helper" });
        return Response.json({ ...initial, claim: { ...initial.claim, lemma_plan_ready: true }, nodes });
      }
      return Response.json(responses[path.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    await openResearchAction(en.lemmaPlan.title);
    const dialog = within(screen.getByRole("dialog", { name: en.lemmaPlan.title }));
    expect(dialog.getByText(goal.formalization!)).toBeTruthy();
    const plan = { format_version: 1, nodes: [{ name: "Helper", proposition: "True", proof: { trivial: true } }] };
    fireEvent.change(dialog.getByLabelText(en.lemmaPlan.input), { target: { value: JSON.stringify(plan) } });
    fireEvent.click(dialog.getByRole("button", { name: en.lemmaPlan.verify }));
    await dialog.findByText("1 lemma verified. See the proof graph.");
    const post = fetch.mock.calls.find(([path]) => path === "/api/lemma-plans");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      claim_id: CLAIM,
      statement_id: statement.statement_id,
      statement_sha256: statement.statement_sha256,
      plan,
    });
    fireEvent.click(dialog.getByRole("button", { name: en.projectEntry.close }));
    expect(await screen.findByRole("button", { name: /^Checked helper/ })).toBeTruthy();
  });

  it("proposes and reviews an isolated premise without leaving the original proof", async () => {
    const original = responses["/api/graph"] as GraphSnapshot;
    const other = { id: WORKSPACE, title: "Another project claim", status: "open", domain: "lean-core" };
    const revised = { id: EVIDENCE, title: "Revised left zero", status: "open", domain: "lean-core" };
    const frame = "a".repeat(64);
    const premise = {
      id: "55555555-5555-4555-8555-555555555555",
      sha256: "b".repeat(64),
      wording: "Addition commutes.",
      lean: "∀ a b : Nat, a + b = b + a",
      question: "Should the starting basis assume commutativity?",
      impact: "The revised proof could use commutativity.",
    };
    const reason = "I reviewed the exact premise and frozen target.";
    let saved = false;
    let adopted = false;
    let decisionAttempts = 0;
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/api/premises/propose" && init?.method === "POST") {
        saved = true;
        return Response.json({ id: premise.id });
      }
      if (path === "/api/premises/decide" && init?.method === "POST") {
        decisionAttempts += 1;
        if (decisionAttempts === 1) return Response.json({ error: "premise.conflicts" }, { status: 409 });
        adopted = true;
        return Response.json({ id: premise.id, revised_claim_id: revised.id });
      }
      if (path === "/api/claims")
        return Response.json({
          claim_id: adopted ? other.id : CLAIM,
          claims: adopted ? [other, revised] : [original.claim, other],
        });
      if (path.startsWith("/api/graph?")) {
        if (path.endsWith(revised.id))
          return Response.json({
            claim: revised,
            nodes: [{ id: `goal:${revised.id}`, kind: "goal", title: "Revised result" }],
            edges: [],
          });
        return Response.json({
          claim: {
            ...original.claim,
            premise_review: {
              available: !adopted,
              frame_sha256: frame,
              target: { wording: "Zero is a left identity.", lean: original.nodes[0]!.formalization },
              basis: [
                { id: "basis-1", kind: "hypothesis", wording: "The operands are natural numbers.", lean: "True" },
              ],
              proposals: saved
                ? [
                    {
                      ...premise,
                      state: adopted ? "revised" : "pending",
                      ...(adopted ? { reason, revised_claim_id: revised.id } : {}),
                    },
                  ]
                : [],
            },
          },
          nodes: [
            ...original.nodes,
            ...(saved && !adopted
              ? [
                  {
                    id: `proposed-premise:${premise.id}`,
                    kind: "proposed_premise",
                    status: "pending",
                    title: premise.wording,
                    formalization: premise.lean,
                    reference: { condition_id: premise.id },
                  },
                ]
              : []),
          ],
          edges: [{ source: LEMMA, target: original.nodes[0]!.id, kind: "actual" }],
        });
      }
      if (path.startsWith("/api/activity?")) return Response.json({ claim_id: path.split("=")[1], activity: [] });
      return Response.json(responses[path.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    const { container } = renderApp("token");
    await openResearchAction(en.premise.title);
    const dialog = within(screen.getByRole("dialog", { name: en.premise.title }));
    fireEvent.click(dialog.getByRole("button", { name: en.premise.propose }));
    expect(dialog.getByText(en.premise.hold)).toBeTruthy();
    fireEvent.change(dialog.getByLabelText(en.premise.wording), { target: { value: premise.wording } });
    fireEvent.change(dialog.getByLabelText(en.projectEntry.lean), { target: { value: premise.lean } });
    fireEvent.change(dialog.getByLabelText(en.premise.question), { target: { value: premise.question } });
    fireEvent.change(dialog.getByLabelText(en.premise.impact), { target: { value: premise.impact } });
    fireEvent.click(dialog.getByRole("button", { name: en.premise.save }));
    expect(await dialog.findByText(en.premise.saved)).toBeTruthy();
    const proposalPost = fetch.mock.calls.find(([path, init]) => path === "/api/premises/propose" && init?.body);
    expect(JSON.parse(String(proposalPost?.[1]?.body))).toEqual({
      claim_id: CLAIM,
      frame_sha256: frame,
      wording: premise.wording,
      lean: premise.lean,
      question: premise.question,
      impact: premise.impact,
    });
    fireEvent.click(dialog.getByRole("button", { name: en.projectEntry.close }));
    const graph = within(screen.getByRole("region", { name: en.graph.label }));
    const node = await graph.findByRole("button", { name: /^Addition commutes/ });
    expect(graph.getByRole("button", { name: /^Original claim/ }).getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelectorAll(".graph-edge[data-kind='actual']")).toHaveLength(1);
    fireEvent.click(node);
    fireEvent.click(screen.getByText(en.conversations.proofActivity, { selector: "summary" }));
    expect(await screen.findByText(en.composer.chooseNode)).toBeTruthy();
    const details = within(screen.getByLabelText(en.review.detail));
    fireEvent.click(details.getByRole("button", { name: en.premise.review }));
    expect(dialog.getByText("Zero is a left identity.")).toBeTruthy();
    expect(dialog.getByText(/The operands are natural numbers/)).toBeTruthy();
    expect(dialog.getByText(premise.lean)).toBeTruthy();
    fireEvent.change(dialog.getByLabelText(en.review.reason), { target: { value: reason } });
    fireEvent.click(dialog.getByRole("button", { name: en.premise.revise }));
    expect((await dialog.findByRole("alert")).textContent).toBe(en.premise.conflicts);
    expect((dialog.getByLabelText(en.review.reason) as HTMLTextAreaElement).value).toBe(reason);
    fireEvent.click(dialog.getByRole("button", { name: en.premise.revise }));
    expect(await dialog.findByText(en.premise.revised)).toBeTruthy();
    const decisionPost = fetch.mock.calls.find(([path, init]) => path === "/api/premises/decide" && init?.body);
    expect(JSON.parse(String(decisionPost?.[1]?.body))).toEqual({
      claim_id: CLAIM,
      frame_sha256: frame,
      id: premise.id,
      sha256: premise.sha256,
      decision: "revise",
      reason,
    });
    expect(screen.getByRole("heading", { level: 2, name: "Left zero" })).toBeTruthy();
    expect(container.querySelectorAll(".graph-edge[data-kind='actual']")).toHaveLength(1);
    expect(graph.queryByRole("button", { name: /^Addition commutes/ })).toBeNull();
    fireEvent.click(dialog.getByRole("button", { name: en.premise.openRevised }));
    expect(await screen.findByRole("heading", { level: 2, name: revised.title })).toBeTruthy();
    expect(screen.getByRole("application", { name: en.projectCanvas.label })).toBeTruthy();
  });

  it("checks for releases by request, opens official notes, and remembers the dismissed notice", async () => {
    window.history.replaceState(null, "", "#settings");
    const path = "/api/release-updates";
    const url = "https://github.com/SAFuDarren/research-multiplier/releases/tag/v1.10.0";
    let state: ReleaseUpdateState = {
      installed_version: "1.9.0",
      status: "not_checked",
      automatic: false,
      last_checked_at: null,
      available: null,
    };
    const actions: unknown[] = [];
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input !== path) return Response.json(responses[input.split("?")[0]!] ?? {});
      if (init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        actions.push(body);
        if (body.action === "check") {
          state = {
            ...state,
            status: "available",
            last_checked_at: Math.floor(Date.now() / 1000),
            available: { version: "1.10.0", url, dismissed: false },
          };
        } else if (body.action === "dismiss" && state.available?.version === body.version) {
          state = { ...state, available: { ...state.available!, dismissed: true } };
        } else if (body.action === "automatic") {
          state = { ...state, automatic: body.enabled };
        }
      }
      return Response.json(state);
    });
    vi.stubGlobal("fetch", fetch);
    const { unmount } = renderApp("token");
    const settings = await screen.findByRole("region", { name: en.settings.updates.title });
    expect(within(settings).getByText("Installed version 1.9.0")).toBeTruthy();
    const automatic = within(settings).getByRole<HTMLInputElement>("checkbox", { name: en.settings.updates.automatic });
    expect(automatic.checked).toBe(false);
    expect(actions).toEqual([]);
    fireEvent.click(within(settings).getByRole("button", { name: en.settings.updates.check }));
    const notice = await screen.findByRole("complementary", { name: en.settings.updates.title });
    expect(within(notice).getByText("Version 1.10.0 is available.")).toBeTruthy();
    const link = within(notice).getByRole("link", { name: en.settings.updates.notes });
    expect(link.getAttribute("href")).toBe(url);
    expect(link.getAttribute("rel")).toContain("noreferrer");
    fireEvent.click(within(notice).getByRole("button", { name: en.settings.updates.dismiss }));
    await waitFor(() => expect(screen.queryByRole("complementary", { name: en.settings.updates.title })).toBeNull());
    expect(within(settings).getByRole("link", { name: en.settings.updates.notes })).toBeTruthy();
    fireEvent.click(automatic);
    await waitFor(() => expect(automatic.checked).toBe(true));
    expect(actions).toEqual([
      { action: "check" },
      { action: "dismiss", version: "1.10.0" },
      { action: "automatic", enabled: true },
    ]);
    unmount();
    renderApp("other-local-session");
    expect(await screen.findByText("Installed version 1.9.0")).toBeTruthy();
    expect(screen.queryByRole("complementary", { name: en.settings.updates.title })).toBeNull();
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

  it("saves answers, resumes the archived proposal after reopening, and separately confirms its exact token", async () => {
    window.history.replaceState(null, "", "#review");
    const id = "55555555-5555-4555-8555-555555555555";
    const route = `/api/v1/workspaces/${WORKSPACE}/clarifications/${id}`;
    const question = "Which carrier is intended?";
    const original = "Every element equals itself.";
    const proposal = {
      token: "archived-result",
      proposition: "∀ x : T, x = x",
      parameters: [{ name: "T", type: "Type" }],
    };
    let status = "waiting";
    const actions: [string, unknown][] = [];
    const details = () => ({
      id,
      title: "Public clarification",
      statement: original,
      questions: [question],
      answers: status === "waiting" ? null : ["An arbitrary type."],
      reason: status === "waiting" ? null : "This is the original scope.",
      kind: "target",
      sensitivity: "public",
      status,
      next_id: null,
      proposal: status === "ready" || status === "confirmed" ? proposal : null,
      accepted: null,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string, init?: RequestInit) => {
        if (input === "/api/inbox") {
          return Response.json({
            items:
              status === "waiting"
                ? [
                    {
                      kind: "clarify_formalization",
                      item_id: id,
                      waiting_since: REVIEW_ITEM.waiting_since,
                      detail: { title: "Public clarification" },
                    },
                  ]
                : [],
            clarification_review: {
              workspace_id: WORKSPACE,
              followups:
                status === "answered" || status === "ready"
                  ? [{ id, title: "Public clarification", status, waiting_since: REVIEW_ITEM.waiting_since }]
                  : [],
            },
          });
        }
        if (input.startsWith(route)) {
          if (input === route) return Response.json(details());
          const path = input.slice(route.length + 1);
          actions.push([path, JSON.parse(String(init?.body))]);
          status = { answer: "answered", prepare: "ready", confirm: "confirmed" }[path]!;
          return Response.json(details());
        }
        return Response.json(responses[input] ?? {});
      }),
    );
    const page = renderApp("token");
    expect(await screen.findByText(original)).toBeTruthy();
    fireEvent.change(screen.getByLabelText(question), { target: { value: "  An arbitrary type.  " } });
    fireEvent.change(screen.getByLabelText(en.review.reason), { target: { value: "This is the original scope." } });
    fireEvent.click(screen.getByRole("button", { name: en.clarifications.saveAnswers }));
    const prepare = await screen.findByRole("button", { name: en.clarifications.prepare });
    expect(actions).toEqual([["answer", { answers: ["An arbitrary type."], reason: "This is the original scope." }]]);
    expect(screen.queryByText(proposal.proposition)).toBeNull();

    fireEvent.click(screen.getByLabelText(en.clarifications.confirmPublic));
    fireEvent.click(prepare);
    expect(await screen.findByText(proposal.proposition)).toBeTruthy();
    expect(screen.getByText("T : Type")).toBeTruthy();
    expect(actions[1]).toEqual(["prepare", { scope: "public" }]);
    page.unmount();

    renderApp("token");
    expect(await screen.findByText(proposal.proposition)).toBeTruthy();
    const confirm = screen.getByRole("button", { name: en.clarifications.confirm.target });
    fireEvent.change(screen.getByLabelText(en.review.reason), { target: { value: "The updated statement matches." } });
    fireEvent.click(confirm);
    await waitFor(() => expect(actions).toHaveLength(3));
    expect(actions[2]).toEqual(["confirm", { token: proposal.token, reason: "The updated statement matches." }]);
    expect(await screen.findByText(en.clarifications.confirmed.target)).toBeTruthy();
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
      ["project-canvas-claims", "active"],
      ["graph", CLAIM],
      ["threads"],
      ["progress"],
      ["proof-task", "active", CLAIM],
      ["activity", CLAIM],
    ]);
    streams[0]!.close();
    await waitFor(() => expect(streams).toHaveLength(2), { timeout: 2500 });
    const resumed = fetch.mock.calls.filter(([path]) => path === EVENT_PATH)[1]?.[1];
    expect(new Headers(resumed?.headers).get("Last-Event-ID")).toBe(id);
    app.unmount();
    expect(resumed?.signal?.aborted).toBe(true);
  });

  it("re-reads proof state and the graph once after overlapping events and resumes from the applied batch", async () => {
    const graphPath = "/api/graph?claim=" + CLAIM;
    const proofPath = "/api/proof-task?claim=" + CLAIM;
    const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
    let withdrawn = false;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") {
        return Response.json({
          items: [],
          workspace_events: { workspace_id: WORKSPACE, cursor: withdrawn ? "9" : "1" },
        });
      }
      if (input === graphPath) {
        const status = withdrawn ? "needs_recheck" : "proved";
        return Response.json({
          claim: { id: CLAIM, title: "Left zero", status, domain: "lean-core" },
          nodes: [{ id: `goal:${CLAIM}`, kind: "goal", title: "Original claim", status }],
          edges: [],
        });
      }
      if (input === proofPath) {
        return Response.json({
          ...PROOF_TASK,
          state: withdrawn ? "blocked" : "authorized",
          blocker: withdrawn ? "proof_task.decision_needed" : null,
          can_start: !withdrawn,
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
    const app = renderApp("active");
    fireEvent.click(await screen.findByRole("button", { name: en.proofTask.title }));
    const pane = within(await screen.findByRole("dialog", { name: en.proofTask.title }));
    expect(await pane.findByText(en.proofTask.states.authorized)).toBeTruthy();
    await waitFor(() => expect(streams).toHaveLength(1));
    const count = (path: string) => fetch.mock.calls.filter(([url]) => url === path).length;
    const originalGraphReads = count(graphPath);
    const originalProofReads = count(proofPath);
    const emit = (id: string, type: string, kind: string, subjectId: string, data: object) => {
      const body = {
        id,
        type,
        workspace_id: WORKSPACE,
        schema_version: 1,
        occurred_at: "2026-11-19T00:00:00Z",
        actor: { kind: "system", id: null },
        subject: { kind, id: subjectId },
        data,
      };
      streams[0]!.enqueue(new TextEncoder().encode(`id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(body)}\n\n`));
    };
    withdrawn = true;
    emit("2", "basis.confirmed", "formalization", EVIDENCE, { claim_id: CLAIM });
    emit("3", "basis_item.retired", "basis_item", EVIDENCE, {});
    emit("4", "proof_task.updated", "proof_task", EVIDENCE, { claim_id: CLAIM });
    expect(await pane.findByText(en.proofTask.states.blocked)).toBeTruthy();
    expect(await pane.findByText(en.errors["proof_task.decision_needed"])).toBeTruthy();
    expect((await screen.findAllByText(en.status.needs_recheck)).length).toBeGreaterThan(0);
    expect(count(graphPath) - originalGraphReads).toBe(1);
    expect(count(proofPath) - originalProofReads).toBe(1);
    expect(pane.queryByRole("button", { name: en.proofTask.start })).toBeNull();
    streams[0]!.close();
    await waitFor(() => expect(streams).toHaveLength(2), { timeout: 2500 });
    const resumed = fetch.mock.calls.filter(([path]) => path === EVENT_PATH)[1]?.[1];
    expect(new Headers(resumed?.headers).get("Last-Event-ID")).toBe("4");
    app.unmount();
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

  it("stops online polling, handles durable and runtime changes, and resumes fallback for a stalled stream", async () => {
    vi.useFakeTimers();
    const advance = (ms: number) =>
      act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
    const proofPath = "/api/proof-task?claim=" + CLAIM;
    const graphPath = "/api/graph?claim=" + CLAIM;
    let state = "authorized";
    let head = "3";
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") {
        return Response.json({
          items: [],
          workspace_events: { workspace_id: WORKSPACE, cursor: head, coverage: "workspace-writes-v1" },
        });
      }
      if (input === proofPath) return Response.json({ ...PROOF_TASK, state, can_start: state === "authorized" });
      if (input === EVENT_PATH && init?.method === "GET") {
        return new Response(new ReadableStream<Uint8Array>({ start: (stream) => streams.push(stream) }), {
          headers: { "Content-Type": "text/event-stream; charset=utf-8" },
        });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    const app = renderApp("active");
    try {
      await advance(40);
      await advance(40);
      expect(streams).toHaveLength(1);
      const encoder = new TextEncoder();
      const emit = (index: number, text: string) => streams[index]!.enqueue(encoder.encode(text));
      const count = (path: string) => fetch.mock.calls.filter(([url]) => url === path).length;
      const countLiveReads = () => ["/api/inbox", "/api/team", "/api/activity?claim=" + CLAIM, proofPath].map(count);
      const feeds = () => fetch.mock.calls.filter(([url]) => url === EVENT_PATH);
      const firstHeaders = new Headers(feeds()[0]?.[1]?.headers);
      expect(firstHeaders.get("X-RM-Workbench-Updates")).toBe("workspace-writes-v1");
      expect(firstHeaders.get("Last-Event-ID")).toBe("3");

      emit(0, ": connected\n\n");
      fireEvent.click(screen.getByRole("button", { name: en.proofTask.title }));
      await advance(50);
      const modal = within(screen.getByRole("dialog", { name: en.proofTask.title }));
      expect(modal.getByText(en.proofTask.states.authorized)).toBeTruthy();
      const beforeIdle = countLiveReads();
      await advance(8_200);
      expect(countLiveReads()).toEqual(beforeIdle);

      state = "running";
      head = "4";
      const write = {
        id: "4",
        type: "workspace.changed",
        schema_version: 1,
        workspace_id: WORKSPACE,
        occurred_at: "2026-11-19T00:00:00Z",
        actor: { kind: "system", id: null },
        subject: { kind: "workspace", id: WORKSPACE },
        data: { area: "proof", claim_id: CLAIM },
      };
      emit(0, `id: 4\nevent: workspace.changed\ndata: ${JSON.stringify(write)}\n\n`);
      await advance(60);
      expect(modal.getByText(en.proofTask.states.running)).toBeTruthy();

      state = "waiting";
      const beforeRuntime = count(graphPath);
      emit(0, ": workbench-runtime-changed\n\n");
      await advance(60);
      expect(modal.getByText(en.proofTask.states.waiting)).toBeTruthy();
      expect(count(graphPath)).toBe(beforeRuntime);

      // No more valid signals. The watchdog should reopen the stream at the
      // applied durable cursor while polling the still-visible window.
      const beforeStall = countLiveReads();
      await advance(26_000);
      expect(feeds()[0]?.[1]?.signal?.aborted).toBe(true);
      expect(streams).toHaveLength(2);
      expect(new Headers(feeds()[1]?.[1]?.headers).get("Last-Event-ID")).toBe("4");
      head = "5";
      state = "stopped";
      await advance(8_200);
      expect(countLiveReads().every((reads, i) => reads > beforeStall[i]!)).toBe(true);
      expect(modal.getByText(en.proofTask.states.stopped)).toBeTruthy();
      expect(count(graphPath)).toBeGreaterThan(beforeRuntime);

      const graphBeforeReconnect = count(graphPath);
      emit(1, ": connected\n\n");
      await advance(60);
      expect(count(graphPath)).toBe(graphBeforeReconnect);
      const afterReconnect = countLiveReads();
      await advance(8_200);
      expect(countLiveReads()).toEqual(afterReconnect);
    } finally {
      app.unmount();
      vi.useRealTimers();
    }
  });

  it("keeps polling with a connected older stream that has no comprehensive capability", async () => {
    vi.useFakeTimers();
    const advance = (ms: number) =>
      act(async () => {
        await vi.advanceTimersByTimeAsync(ms);
      });
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") {
        return Response.json({ items: [], workspace_events: { workspace_id: WORKSPACE, cursor: "1" } });
      }
      if (input === EVENT_PATH && init?.method === "GET") {
        return new Response(
          new ReadableStream<Uint8Array>({
            start(stream) {
              stream.enqueue(new TextEncoder().encode(": connected\n\n"));
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        );
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    const app = renderApp("old");
    try {
      await advance(40);
      await advance(40);
      const initial = fetch.mock.calls.filter(([path]) => path === "/api/inbox").length;
      const eventRequest = fetch.mock.calls.find(([path]) => path === EVENT_PATH);
      expect(eventRequest).toBeDefined();
      expect(new Headers(eventRequest?.[1]?.headers).has("X-RM-Workbench-Updates")).toBe(false);
      await advance(8_200);
      expect(fetch.mock.calls.filter(([path]) => path === "/api/inbox").length).toBeGreaterThan(initial);
    } finally {
      app.unmount();
      vi.useRealTimers();
    }
  });

  it("reconciles an in-flight view that started before the first comprehensive cursor", async () => {
    const graphPath = "/api/graph?claim=" + CLAIM;
    const graph = (title: string) => ({
      claim: { id: CLAIM, title, status: "open", domain: "lean-core" },
      nodes: [{ id: `goal:${CLAIM}`, kind: "goal", title: "Original claim", status: "open" }],
      edges: [],
    });
    const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
    const graphReads: (AbortSignal | null | undefined)[] = [];
    let resolveInbox!: (response: Response) => void;
    const initialInbox = new Promise<Response>((resolve) => {
      resolveInbox = resolve;
    });
    let resolveOldGraph!: (response: Response) => void;
    const oldGraph = new Promise<Response>((resolve) => {
      resolveOldGraph = resolve;
    });
    let bootstrapped = false;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/inbox") {
        const data = {
          items: [],
          workspace_events: { workspace_id: WORKSPACE, cursor: "7", coverage: "workspace-writes-v1" },
        };
        return bootstrapped ? Response.json(data) : initialInbox;
      }
      if (input === graphPath) {
        graphReads.push(init?.signal);
        return graphReads.length === 1 ? oldGraph : Response.json(graph("Committed before the cursor"));
      }
      if (input === EVENT_PATH) {
        return new Response(new ReadableStream<Uint8Array>({ start: (stream) => streams.push(stream) }), {
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("active");
    await waitFor(() => expect(graphReads).toHaveLength(1));
    bootstrapped = true;
    resolveInbox(
      Response.json({
        items: [],
        workspace_events: { workspace_id: WORKSPACE, cursor: "7", coverage: "workspace-writes-v1" },
      }),
    );
    await waitFor(() => expect(streams).toHaveLength(1));
    const request = fetch.mock.calls.find(([path]) => path === EVENT_PATH)?.[1];
    expect(new Headers(request?.headers).get("Last-Event-ID")).toBe("7");
    streams[0]!.enqueue(new TextEncoder().encode(": connected\n\n"));
    expect(await screen.findByRole("heading", { level: 2, name: "Committed before the cursor" })).toBeTruthy();
    expect(graphReads).toHaveLength(2);
    expect(graphReads[0]?.aborted).toBe(true);
    await act(async () => {
      resolveOldGraph(Response.json(graph("Obsolete initial result")));
    });
    expect(screen.queryByRole("heading", { level: 2, name: "Obsolete initial result" })).toBeNull();
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
    expect(await screen.findByRole("application", { name: en.projectCanvas.label })).toBeTruthy();
    const lemma = await screen.findByRole("button", { name: /^A helper lemma/ });
    expect(lemma.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /^Original claim/ }));
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
    await openResearchAction(en.claimCodes.title);
    const modal = within(await screen.findByRole("dialog", { name: en.claimCodes.title }));
    fireEvent.change(modal.getByLabelText(en.claimCodes.claim), { target: { value: "new" } });
    fireEvent.click(modal.getByRole("button", { name: en.claimCodes.save }));
    expect(await modal.findByText(en.claimCodes.saved)).toBeTruthy();
    expect(await screen.findAllByText("NEW-09")).not.toHaveLength(0);
    fireEvent.change(modal.getByLabelText(en.claimCodes.reference), { target: { value: "flow-09" } });
    fireEvent.click(modal.getByRole("button", { name: en.claimCodes.open }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^A helper lemma/ }).getAttribute("aria-pressed")).toBe("true"),
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
    await openResearchAction(en.claimCodes.title);
    const modal = within(await screen.findByRole("dialog", { name: en.claimCodes.title }));
    expect(modal.queryByLabelText(en.claimCodes.claim)).toBeNull();
    fireEvent.change(modal.getByLabelText(en.claimCodes.reference), { target: { value: "OLD-19" } });
    fireEvent.click(modal.getByRole("button", { name: en.claimCodes.open }));
    expect(await screen.findByRole("heading", { level: 2, name: "Archived theorem" })).toBeTruthy();
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
    await openResearchAction(en.projectEntry.organize);
    const dialog = within(await screen.findByRole("dialog"));
    expect(await dialog.findByText("local-text", { selector: "button" })).toBeTruthy();
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

  it.each(NATIVE_CONNECTIONS)("discovers both tools and connects only the selected $label login", async (choice) => {
    window.history.replaceState(null, "", "#team/runtimes");
    let existing = false;
    let scans = 0;
    const other = NATIVE_CONNECTIONS.find(({ id }) => id !== choice.id)!;
    const connection = {
      id: choice.id,
      provider: choice.provider,
      model: choice.model,
      label: choice.label,
      status: "untested",
      fingerprint: "fingerprint",
    };
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/team") {
        return Response.json({
          ...(responses[input] as object),
          services: [{ provider: "openai", ready: false, transport: "https", via: "env", detail: "OPENAI_API_KEY" }],
        });
      }
      if (input === "/api/model-connections") return Response.json({ connections: existing ? [connection] : [] });
      if (input === "/api/model-connections/native") {
        scans++;
        return Response.json({
          providers: NATIVE_CONNECTIONS.map(({ id, provider, model }) => ({
            provider,
            status: id === other.id && scans === 1 ? "connection.authentication" : "signed_in",
            models: [{ id: model, label: model }],
          })),
        });
      }
      if (input === "/api/model-connections/add-native") {
        expect(JSON.parse(String(init?.body))).toEqual({ provider: choice.provider, model: choice.model });
        existing = true;
        return Response.json(connection);
      }
      if (input === "/api/model-connections/test") {
        expect(JSON.parse(String(init?.body))).toEqual({ id: connection.id });
        return Response.json({ status: "connected", reported_model: `${choice.model}-reported` });
      }
      if (input === "/api/model-connections/remove") {
        expect(JSON.parse(String(init?.body))).toEqual({ id: connection.id });
        existing = false;
        return Response.json({ removed: true });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    const connections = within(await screen.findByRole("region", { name: en.modelConnections.title }));
    expect(await connections.findByText(en.modelConnections.empty)).toBeTruthy();
    expect(screen.queryByText(en.team.providers.openai)).toBeNull();
    expect(scans).toBe(0);
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.add }));
    const connect = await connections.findByRole("button", { name: `Connect ${choice.label}` });
    const otherConnect = connections.getByRole("button", { name: `Connect ${other.label}` });
    expect(otherConnect.hasAttribute("disabled")).toBe(true);
    expect(connections.getByText(new RegExp(other.login))).toBeTruthy();
    expect(connections.queryByLabelText(en.modelConnections.provider)).toBeNull();
    expect(connections.queryByLabelText(en.modelConnections.model)).toBeNull();
    expect(connections.queryByLabelText(en.modelConnections.apiKey)).toBeNull();
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/add-native"))).toBe(false);
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.refreshNative }));
    await waitFor(() => expect(otherConnect.hasAttribute("disabled")).toBe(false));
    fireEvent.change(connections.getByRole("combobox", { name: `${choice.label} model` }), {
      target: { value: choice.model },
    });
    fireEvent.click(connect);
    expect(await connections.findByRole("cell", { name: choice.model })).toBeTruthy();
    expect(connections.queryByText(other.label)).toBeNull();
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/test"))).toBe(false);
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.test }));
    expect(await connections.findByText(new RegExp(`reported ${choice.model}-reported`))).toBeTruthy();
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.remove }));
    expect(fetch.mock.calls.filter(([path]) => path.endsWith("/remove"))).toHaveLength(0);
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.removeConfirm }));
    expect(await connections.findByText(en.modelConnections.empty)).toBeTruthy();
  });

  it("opens a separate password-masked form for API connections", async () => {
    window.history.replaceState(null, "", "#team/runtimes");
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/api/model-connections") return Response.json({ connections: [] });
      if (path === "/api/model-connections/native") return Response.json({ providers: [] });
      if (path === "/api/model-connections/add") {
        expect(JSON.parse(String(init?.body))).toEqual({
          provider: "anthropic",
          model: "claude-model",
          api_key: "test-key",
        });
        return Response.json({ id: "api" });
      }
      return Response.json(responses[path.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    const connections = within(await screen.findByRole("region", { name: en.modelConnections.title }));
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.add }));
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.addApi }));
    const provider = connections.getByLabelText(en.modelConnections.provider);
    expect(within(provider).queryByRole("option", { name: "Codex" })).toBeNull();
    fireEvent.change(provider, { target: { value: "anthropic" } });
    fireEvent.change(connections.getByLabelText(en.modelConnections.model), { target: { value: "claude-model" } });
    const key = connections.getByLabelText(en.modelConnections.apiKey);
    expect(key.getAttribute("type")).toBe("password");
    fireEvent.change(key, { target: { value: "test-key" } });
    fireEvent.click(connections.getByRole("button", { name: en.modelConnections.save }));
    await waitFor(() => expect(fetch.mock.calls.some(([path]) => path === "/api/model-connections/add")).toBe(true));
    expect(fetch.mock.calls.some(([path]) => path === "/api/model-connections/add-native")).toBe(false);
  });

  it.each(NATIVE_CONNECTIONS)("previews the exact public excerpt before calling $label", async (choice) => {
    const content = "Addition of natural numbers is commutative.";
    const connection = {
      id: choice.id,
      provider: choice.provider,
      label: choice.label,
      model: choice.model,
      status: "connected",
      fingerprint: "fingerprint",
    };
    let proposed: Record<string, unknown> | undefined;
    const fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (input === "/api/claims") return Response.json({ claim_id: null, claims: [] });
      if (input === "/api/model-connections") return Response.json({ connections: [connection] });
      if (input === "/api/project-intake/models") return Response.json({ models: [] });
      if (input.endsWith("/public-preview")) {
        proposed = JSON.parse(String(init?.body)) as Record<string, unknown>;
        expect(proposed.connection_id).toBe(choice.id);
        const { domain, objective, notes } = proposed;
        return Response.json({
          token: "reviewed",
          provider: connection.provider,
          model: connection.model,
          outgoing: { domain, objective, notes },
          limits: { timeout_seconds: 45, max_calls: 1 },
        });
      }
      if (input.endsWith("/public-draft")) {
        expect(JSON.parse(String(init?.body))).toEqual({ ...proposed, token: "reviewed" });
        return Response.json({ id: "review", title: "Reviewed public notes", items: [], confirmed: false });
      }
      return Response.json(responses[input.split("?")[0]!] ?? {});
    });
    vi.stubGlobal("fetch", fetch);
    renderApp("token");
    await openResearchAction(en.projectEntry.organize);
    const dialog = within(await screen.findByRole("dialog"));
    const recipient = `${choice.label} · ${choice.model}`;
    fireEvent.click(await dialog.findByRole("button", { name: en.projectEntry.intake.model }));
    fireEvent.click(await dialog.findByRole("option", { name: recipient }));
    fireEvent.change(dialog.getByLabelText(en.projectEntry.intake.notes), { target: { value: content } });
    fireEvent.click(dialog.getByRole("button", { name: en.projectEntry.intake.analyze }));
    const preview = within(await dialog.findByRole("region", { name: en.modelConnections.publicPreview }));
    expect(preview.getByText(new RegExp(content))).toBeTruthy();
    expect(preview.getByText(recipient)).toBeTruthy();
    expect(fetch.mock.calls.some(([path]) => path.endsWith("/public-draft"))).toBe(false);
    fireEvent.click(preview.getByRole("button", { name: en.modelConnections.sendPublic }));
    expect(await dialog.findByText("Reviewed public notes")).toBeTruthy();
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
