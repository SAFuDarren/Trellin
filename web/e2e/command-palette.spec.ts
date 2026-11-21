import { expect, test, type Page } from "@playwright/test";
import type { ClaimsResponse } from "../src/api/client";
import type { Conversation } from "../src/api/conversations";
import type { TeamAgent } from "../src/api/teamAgents";
import { SECTIONS } from "../src/shell/route";
import type { GraphSnapshot } from "../src/workbench/types";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

const CLAIM_ID = "11111111-1111-4111-8111-111111111111";
const OBLIGATION_ID = "obligation:55555555-5555-4555-8555-555555555555:2";
const LIBRARY_ID = "99999999-9999-4999-8999-999999999999";
const PALETTE = "Command palette";

function palette(page: Page) {
  return page.getByRole("dialog", { name: PALETTE, exact: true });
}

async function openPalette(page: Page, shortcut = "Control+k") {
  await page.keyboard.press(shortcut);
  const dialog = palette(page);
  await expect(dialog).toBeVisible();
  const search = dialog.getByRole("combobox");
  await expect(search).toBeFocused();
  return search;
}

async function choose(page: Page, query: string, label: string) {
  const search = await openPalette(page);
  await search.fill(query);
  const result = palette(page).getByRole("option", { name: label, exact: true });
  await expect(result).toBeEnabled();
  await expect(search).toHaveAttribute("aria-activedescendant", (await result.getAttribute("id"))!);
  await search.press("Enter");
  await expect(palette(page)).toBeHidden();
}

async function withReferences(page: Page) {
  await page.route("**/api/claims", async (route) => {
    const response = await route.fetch();
    const claims = (await response.json()) as ClaimsResponse;
    await route.fulfill({ response, json: { ...claims, claim_codes: true } });
  });
  await page.route("**/api/claim-code?*", async (route) => {
    const code = new URL(route.request().url()).searchParams.get("code");
    if (code === "ZADD" || code === "ZADD-02") {
      await route.fulfill({ json: { claim_id: CLAIM_ID, node_id: code === "ZADD-02" ? OBLIGATION_ID : null } });
    } else {
      await route.fulfill({ status: 404, json: { error: "claim_code.missing" } });
    }
  });
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    if (graph.claim.id === CLAIM_ID) {
      graph.claim.code = "ZADD";
      graph.nodes.find(({ id }) => id === OBLIGATION_ID)!.code = "ZADD-02";
    }
    await route.fulfill({ response, json: graph });
  });
}

test("both shortcuts work throughout the shell and Escape restores the previous keyboard focus", async ({ page }) => {
  await page.goto("/#session=fixture");
  const home = page.locator("nav.rail").getByRole("link", { name: "Home", exact: true });
  for (const [index, section] of SECTIONS.entries()) {
    await page.goto(`/#${section}`);
    if (["workbench", "claims", "library", "chat", "team", "progress"].includes(section)) {
      await expect(page.locator(`nav.rail .rail-link[href="#${section}"]`)).toHaveAttribute("aria-current", "page");
    }
    await home.focus();
    const search = await openPalette(page, index % 2 === 0 ? "Control+k" : "Meta+k");
    await expect(search).toHaveValue("");
    await search.fill("temporary filter");
    await search.press("Escape");
    await expect(palette(page)).toBeHidden();
    await expect(home).toBeFocused();
    await expect(page).toHaveURL((url) => url.hash === `#${section}`);
  }

  const launcher = page.locator("nav.rail").getByRole("button", { name: PALETTE, exact: true });
  await launcher.click();
  await expect(palette(page).getByRole("combobox")).toBeFocused();
});

test("keyboard search opens claims and exact proof references on the project map", async ({ page }) => {
  await withReferences(page);
  await page.goto("/#session=fixture");
  await page.goto("/#library");
  await choose(page, "加零", FIXTURE_CLAIM_TITLE);
  const map = page.getByRole("application", { name: "Project map", exact: true });
  await expect(map).toBeVisible();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");

  await page.goto("/#team");
  await choose(page, "zadd", "ZADD");
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");

  await choose(page, "ZADD-02", "ZADD-02");
  await expect(map).toBeVisible();
  const node = page.locator(`.inspector-graph [data-node="${OBLIGATION_ID}"]`);
  await expect(node).toBeVisible();
  await expect(node).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#node-detail").getByRole("heading", { name: "Proof obligation 2" })).toBeVisible();
});

test("Library, conversation and Agent results take the existing routes", async ({ page }) => {
  const agent: TeamAgent = {
    id: "4b9987f9-9836-485b-892f-4f11ca4fc215",
    revision: 1,
    name: "Lean Reviewer",
    description: "Checks Lean statements.",
    connection_id: "fixture-local",
    avatar: "book",
    starters: [],
    instructions: "",
  };
  const thread: Conversation = {
    id: "3a9987f9-9836-485b-892f-4f11ca4fc215",
    title: "Explore multiplication lemmas",
    agent_id: null,
    claim_id: null,
    node_id: null,
    statement_id: null,
    created_at: "2026-11-20T01:00:00Z",
    updated_at: "2026-11-20T01:05:00Z",
    running: false,
    revision: 1,
    messages: [],
  };
  await page.route("**/api/team-agents", (route) => route.fulfill({ json: { agents: [agent] } }));
  await page.route("**/api/conversations", (route) => route.fulfill({ json: { threads: [thread] } }));
  await page.route("**/api/conversations/thread?*", (route) => route.fulfill({ json: { thread } }));
  await page.goto("/#session=fixture");
  await page.goto("/#library");
  await page.getByRole("searchbox", { name: /^Search by name or statement/ }).fill("Nat.add_zero");
  await expect(page.getByRole("row", { name: /Nat\.add_zero/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /Nat\.mul_comm/ })).toHaveCount(0);
  await choose(page, "mul_comm", "Nat.mul_comm");
  await expect(page).toHaveURL((url) => url.hash === `#library/~${LIBRARY_ID}`);
  await expect(page.getByRole("complementary", { name: "Nat.mul_comm", exact: true })).toBeVisible();

  await choose(page, "multiplication lemmas", thread.title);
  await expect(page).toHaveURL((url) => url.hash === `#chat/${thread.id}`);
  await expect(page.getByRole("heading", { name: thread.title })).toBeVisible();

  await choose(page, "lean reviewer", agent.name);
  await expect(page).toHaveURL((url) => url.hash === `#chat/employee%2F${agent.id}`);
  await expect(page.getByRole("heading", { name: agent.name })).toBeVisible();
});

test("recent results lead the list and stay inside the active project", async ({ page }) => {
  const firstWorkspace = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const secondWorkspace = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const shared = { id: CLAIM_ID, title: "Common induction exercise", status: "open", domain: "lean-core" };
  const firstOnly = { ...shared, id: "aaaaaaaa-0000-4000-8000-000000000001", title: "First project private claim" };
  const secondOnly = { ...shared, id: "aaaaaaaa-0000-4000-8000-000000000002", title: "Second project private claim" };
  await page.route("**/api/inbox", (route) => {
    const workspace_id = route.request().headers()["x-rm-session"] === "project-a" ? firstWorkspace : secondWorkspace;
    return route.fulfill({ json: { items: [], workspace_events: { workspace_id, cursor: "0" } } });
  });
  await page.route(/\/api\/(?:claims|project-canvas\/claims)$/, (route) => {
    const only = route.request().headers()["x-rm-session"] === "project-a" ? firstOnly : secondOnly;
    return route.fulfill({ json: { claims: [shared, only] } });
  });

  await page.goto("/?project=a#session=project-a");
  await choose(page, firstOnly.title, firstOnly.title);
  await choose(page, shared.title, shared.title);
  const search = await openPalette(page);
  const recent = palette(page).getByRole("group", { name: "Recent", exact: true });
  await expect(recent.getByRole("option")).toHaveCount(2);
  await expect(palette(page).getByRole("option").first()).toHaveAccessibleName(shared.title);
  await search.press("ArrowDown");
  const previous = recent.getByRole("option", { name: firstOnly.title, exact: true });
  await expect(search).toHaveAttribute("aria-activedescendant", (await previous.getAttribute("id"))!);
  await search.press("Escape");
  await expect(palette(page)).toBeHidden();

  await page.reload();
  await openPalette(page);
  await expect(recent.getByRole("option").first()).toHaveAccessibleName(shared.title);

  // The two launch sessions share an origin, so this also exercises persisted recent state.
  await page.goto("/?project=b#session=project-b");
  const secondSearch = await openPalette(page);
  await expect(palette(page).getByRole("option", { name: secondOnly.title, exact: true })).toBeVisible();
  await expect(palette(page).getByRole("option", { name: shared.title, exact: true })).toBeVisible();
  await expect(recent).toHaveCount(0);
  await secondSearch.fill("first project private");
  await expect(palette(page).getByRole("option")).toHaveCount(0);
});

test("palette actions open the existing forms and wait for explicit authorization", async ({ page }) => {
  await withReferences(page);
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname.startsWith("/api/")) {
      posts.push(new URL(request.url()).pathname);
    }
  });
  await page.route("**/api/proof-task?*", (route) => {
    const claim_id = new URL(route.request().url()).searchParams.get("claim");
    expect(claim_id).toBe(CLAIM_ID);
    return route.fulfill({
      json: {
        claim_id,
        title: FIXTURE_CLAIM_TITLE,
        state: "ready",
        can_start: true,
        can_stop: false,
        blocker: null,
        local_model_allowed: false,
        scope: { token: "fixture-proof-scope", proposition: "∀ n : Nat, n + 0 = n", basis: [], omitted_basis: 0 },
        decisions: [],
        policy: null,
        receipt: null,
        adopted: false,
      },
    });
  });
  await page.goto("/#session=fixture");
  const selected = mapClaim(page, FIXTURE_CLAIM_TITLE);
  await selected.click();
  await expect(selected).toHaveAttribute("aria-pressed", "true");
  await page.goto("/#library");
  await choose(page, "proof task", "Proof task");
  const proofTask = page.getByRole("dialog", { name: "Proof task", exact: true });
  await expect(proofTask.getByRole("heading", { name: FIXTURE_CLAIM_TITLE })).toBeVisible();
  await expect(proofTask.getByRole("checkbox", { name: /I authorize proof work/ })).not.toBeChecked();
  await expect(proofTask.getByRole("button", { name: "Authorize and start", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(proofTask).toBeHidden();

  await page.goto("/#team");
  for (const name of ["Add theorem", "Organize with AI", "Research references"]) {
    await choose(page, name, name);
    const dialog = page.getByRole("dialog", { name, exact: true });
    await expect(dialog).toBeVisible();
    if (name === "Add theorem") {
      const confirmation = dialog.getByRole("checkbox", { name: /The Lean proposition expresses/ });
      await expect(confirmation).toBeVisible();
      await expect(confirmation).not.toBeChecked();
    }
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  }

  await page.goto("/#library");
  await choose(page, "start a chat", "Start a chat");
  const dock = page.getByRole("region", { name: "Agent chat", exact: true });
  await expect(dock.getByRole("button", { name: "Choose an Agent", exact: true })).toBeVisible();
  await expect(page).toHaveURL((url) => url.hash === "#library");
  expect(posts).toEqual([]);
});
