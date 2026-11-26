import { expect, test, type Page } from "@playwright/test";
import type { ClaimsResponse } from "../src/api/client";
import type { Conversation } from "../src/api/conversations";
import type { TeamAgent } from "../src/api/teamAgents";
import { SECTIONS } from "../src/shell/route";
import type { Atlas, GraphNode, GraphSnapshot } from "../src/workbench/types";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";
import { openDestination } from "./navigation";

const CLAIM_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_CLAIM_ID = "aaaaaaaa-0000-4000-8000-000000000001";
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

async function choose(page: Page, query: string, label: string, group?: string) {
  const search = await openPalette(page);
  await search.fill(query);
  const list = group ? palette(page).getByRole("group", { name: group, exact: true }) : palette(page);
  const result = list.getByRole("option", { name: label, exact: true });
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
    } else if (code === "OTHER-02") {
      await route.fulfill({ json: { claim_id: OTHER_CLAIM_ID, node_id: OBLIGATION_ID } });
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
    if (!["settings", "help"].includes(section)) {
      // #289: every page lights up exactly one space in the rail.
      await expect(page.locator('nav.rail .rail-group [aria-current="page"]')).toHaveCount(1);
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

for (const [platform, shortcut, actionHint, openHint] of [
  ["MacIntel", "Meta+k", "⌥N", "⌥P"],
  ["Win32", "Control+k", "Alt+N", "Alt+P"],
] as const) {
  test(`visible shortcut hints follow ${platform} in the palette, context menu and Help`, async ({ page }) => {
    await page.addInitScript((value) => Object.defineProperty(navigator, "platform", { value }), platform);
    await page.goto("/#session=fixture");
    const glyph = platform === "MacIntel" ? "⌘K" : "Ctrl+K";
    const launcher = page.locator("nav.rail").getByRole("button", { name: PALETTE, exact: true });
    await expect(launcher).toHaveAttribute("title", `Command palette (${glyph})`);

    const query = await openPalette(page, shortcut);
    const add = palette(page).getByRole("option", { name: "New theorem…", exact: true });
    await expect(add.locator("kbd")).toHaveText(actionHint);
    await expect(add).toHaveAttribute("aria-keyshortcuts", "Alt+N");
    await expect(palette(page).locator(".command-palette-keys")).toContainText(platform === "MacIntel" ? "↵" : "Enter");
    await query.press("Escape");

    const card = mapClaim(page, FIXTURE_CLAIM_TITLE);
    await card.press("Shift+F10");
    const open = page.getByRole("menuitem", { name: "Open proof", exact: true });
    await expect(open.locator("kbd")).toHaveText(openHint);
    await expect(open).toHaveAttribute("aria-keyshortcuts", "Alt+P");
    await page.keyboard.press("Escape");

    await page.goto("/#help");
    await expect(page.getByRole("main").getByText(glyph, { exact: true })).toBeVisible();
  });
}

test("proof node titles are searchable with the open claim first and move focus to the chosen node", async ({
  page,
}) => {
  const otherNode: GraphNode = { id: "obligation:another:7", kind: "obligation", title: "Proof obligation 7" };
  const reads = new Set<string>();
  await page.route("**/api/atlas", async (route) => {
    const response = await route.fetch();
    const atlas = (await response.json()) as Atlas;
    await route.fulfill({
      response,
      // The open claim can be outside the Atlas window.
      json: {
        claims: atlas.claims
          .filter(({ id }) => id !== CLAIM_ID)
          .map((claim) => (claim.id === OTHER_CLAIM_ID ? { ...claim, nodes: [...claim.nodes, otherNode] } : claim)),
      },
    });
  });
  await page.route("**/api/graph?*", async (route) => {
    const claimId = new URL(route.request().url()).searchParams.get("claim")!;
    reads.add(claimId);
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    if (claimId === OTHER_CLAIM_ID) {
      const goal: GraphNode = { id: `goal:${claimId}`, kind: "goal", title: "Another root" };
      graph.nodes = [goal, otherNode];
      graph.edges = [{ source: otherNode.id, target: goal.id, kind: "planned" }];
    }
    await route.fulfill({ response, json: graph });
  });
  await page.goto("/#session=fixture");
  await openProof(page);

  const query = await openPalette(page);
  await query.fill("# obligation");
  const dialog = palette(page);
  const current = dialog.getByRole("group", { name: "This claim’s proof nodes" });
  const others = dialog.getByRole("group", { name: "Other proof nodes" });
  await expect(current.getByRole("option")).toHaveText([/Proof obligation 1/, /Proof obligation 2/]);
  const crossClaim = others.getByRole("option", { name: "Proof obligation 7" });
  await expect(crossClaim).toBeVisible();
  expect([...reads]).toEqual([CLAIM_ID]);
  await expect(query).toHaveAttribute(
    "aria-activedescendant",
    (await current.getByRole("option").first().getAttribute("id"))!,
  );
  await query.press("ArrowDown");
  await query.press("Enter");
  await expect(page.locator(`.main-proof-graph [data-node="${OBLIGATION_ID}"]`)).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  const crossClaimQuery = await openPalette(page);
  await crossClaimQuery.fill("proof obligation 7");
  await expect(crossClaim).toBeEnabled();
  await crossClaimQuery.press("Enter");
  await expect(page.locator(`.main-proof-graph [data-node="${otherNode.id}"]`)).toHaveAttribute("aria-pressed", "true");
  expect([...reads]).toEqual([CLAIM_ID, OTHER_CLAIM_ID]);
});

test("the # prefix searches claims and exact proof references with the keyboard", async ({ page }) => {
  await withReferences(page);
  await page.goto("/#session=fixture");
  await page.goto("/#library");
  const query = await openPalette(page);
  await query.fill("#");
  const results = palette(page).getByRole("listbox");
  const claimResult = results.getByRole("option", { name: FIXTURE_CLAIM_TITLE, exact: true });
  await expect(claimResult.locator("svg.workbench-icon")).toBeVisible();
  await expect(results.getByRole("option", { name: "Nat.add_zero", exact: true })).toHaveCount(0);
  await expect(results.getByRole("option", { name: "New theorem…", exact: true })).toHaveCount(0);
  await query.press("Escape");
  await choose(page, "# 加零", FIXTURE_CLAIM_TITLE);
  const map = page.getByRole("application", { name: "Project map", exact: true });
  await expect(map).toBeVisible();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");

  await page.goto("/#team");
  await choose(page, "zadd", "ZADD");
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");

  await choose(page, "#ZADD-02", "ZADD-02");
  await expect(map).toBeHidden();
  const node = page.locator(`.main-proof-graph [data-node="${OBLIGATION_ID}"]`);
  await expect(node).toBeVisible();
  await expect(node).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#node-detail").getByRole("heading", { name: "Proof obligation 2" })).toBeVisible();

  const original = mapClaim(page, FIXTURE_CLAIM_TITLE);
  await choose(page, "OTHER-02", "OTHER-02");
  await expect(page.locator(".proof-path h1")).toHaveText("Other claim");
  await page.keyboard.press("Escape");
  await expect(original).toHaveAttribute("aria-pressed", "true");
  await expect(original).toBeFocused();

  await page.getByRole("button", { name: "Collapse review column" }).click();
  await openDestination(page, "Library");
  await choose(page, "OTHER-02", "OTHER-02");
  await expect(page.locator(".proof-path h1")).toHaveText("Other claim");
  await page.locator(".proof-return").click();
  await expect(original).toHaveAttribute("aria-pressed", "true");
  await expect(original).toBeFocused();
  await expect(page.locator("#review")).toBeHidden();
});

test("Library and conversation results keep their routes and @ narrows to Agents", async ({ page }) => {
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
    title: "Lean Reviewer discussion of multiplication lemmas",
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
  await choose(page, "mul_comm", "Nat.mul_comm", "Library");
  await expect(page).toHaveURL((url) => url.hash === `#library/~${LIBRARY_ID}`);
  await expect(page.getByRole("complementary", { name: "Nat.mul_comm", exact: true })).toBeVisible();

  await choose(page, "multiplication lemmas", thread.title);
  await expect(page).toHaveURL((url) => url.hash === `#chat/${thread.id}`);
  await expect(page.getByRole("heading", { name: thread.title })).toBeVisible();

  const search = await openPalette(page);
  await search.fill("lean reviewer");
  await expect(palette(page).getByRole("option", { name: thread.title, exact: true })).toBeVisible();
  await search.fill("@ lean reviewer");
  const agentResult = palette(page).getByRole("option", { name: agent.name, exact: true });
  await expect(agentResult.locator("svg.workbench-icon")).toBeVisible();
  await expect(palette(page).getByRole("option", { name: thread.title, exact: true })).toHaveCount(0);
  await search.press("Enter");
  await expect(page).toHaveURL((url) => url.hash === `#chat/employee%2F${agent.id}`);
  await expect(page.getByRole("heading", { name: agent.name })).toBeVisible();

  const scopedRecent = await openPalette(page);
  await scopedRecent.fill("@ lean reviewer");
  await expect(palette(page).getByRole("group", { name: "Recent", exact: true }).getByRole("option")).toHaveText(
    agent.name,
  );
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
  const search = await openPalette(page);
  const chooser = palette(page);
  const legend = chooser.getByRole("group", { name: "Keyboard shortcuts" });
  const mac = await page.evaluate(() => navigator.platform.startsWith("Mac"));
  await expect(legend.locator("kbd")).toHaveText(["↑", "↓", mac ? "↵" : "Enter", "#", "@"]);
  const backdrop = await chooser.evaluate((element) => getComputedStyle(element, "::backdrop").backgroundColor);
  expect(backdrop).toBe("rgba(0, 0, 0, 0.34)");
  await search.fill("proof");
  await expect(chooser.getByRole("option", { name: "Research references", exact: true })).toBeVisible();
  const proofAction = chooser.getByRole("option", { name: "Proof task", exact: true });
  await expect(proofAction.locator("svg.workbench-icon")).toBeVisible();
  await expect(proofAction).toHaveAttribute("aria-keyshortcuts", "Alt+T");
  const keyHint = proofAction.locator("kbd");
  await expect(keyHint).toHaveText(mac ? "⌥T" : "Alt+T");
  const [hintBounds, textBounds] = await Promise.all([
    keyHint.boundingBox(),
    proofAction.locator(".floating-option-text").boundingBox(),
  ]);
  expect(hintBounds!.x).toBeGreaterThan(textBounds!.x + textBounds!.width);
  await search.press("Alt+t");
  await expect(chooser).toBeHidden();
  const proofTask = page.getByRole("dialog", { name: "Proof task", exact: true });
  await expect(proofTask.getByRole("heading", { name: FIXTURE_CLAIM_TITLE })).toBeVisible();
  await expect(proofTask.getByRole("checkbox", { name: /I authorize proof work/ })).not.toBeChecked();
  await expect(proofTask.getByRole("button", { name: "Authorize and start", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(proofTask).toBeHidden();

  await page.goto("/#team");
  for (const [name, title] of [
    ["New theorem…", "New theorem"],
    ["Paper, notes or code…", "Import paper, notes or code"],
    ["Research references", "Research references"],
  ] as const) {
    await choose(page, name, name);
    const dialog = page.getByRole("dialog", { name: title, exact: true });
    await expect(dialog).toBeVisible();
    if (name === "New theorem…") {
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
