import { expect, test, type Locator, type Page } from "@playwright/test";
import type { GraphSnapshot } from "../src/workbench/types";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

const SECOND_TITLE = "单射映射的复合";
const FIRST_STATEMENT = "For each natural number, adding zero on the left returns that number.";
const SECOND_LEAN = "Function.Injective f → Function.Injective g → Function.Injective (g ∘ f)";

async function centerOf(locator: Locator) {
  await expect(locator).toBeInViewport();
  const bounds = (await locator.boundingBox())!;
  return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("project claims open the existing inspector without leaving or moving the canvas", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  // The older claim is still in the project even after falling out of the recent list.
  await page.route("**/api/claims", async (route) => {
    const response = await route.fetch();
    const project = await response.json();
    const claims = project.claims.filter((claim: { title: string }) => claim.title !== SECOND_TITLE);
    await route.fulfill({ response, json: { ...project, claims } });
  });
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    const goal = graph.nodes.find((node) => node.kind === "goal")!;
    if (graph.claim.title === "Other claim") {
      // The fixture shares a proof graph across claims. Give this goal its own record.
      const oldGoalId = goal.id;
      goal.id = `goal:${graph.claim.id}`;
      goal.title = SECOND_TITLE;
      goal.formula = undefined;
      goal.formalization = SECOND_LEAN;
      graph.claim.title = SECOND_TITLE;
      for (const edge of graph.edges) {
        if (edge.source === oldGoalId) edge.source = goal.id;
        if (edge.target === oldGoalId) edge.target = goal.id;
      }
    } else {
      goal.formula = undefined;
      goal.statement = FIRST_STATEMENT;
    }
    await route.fulfill({ response, json: graph });
  });
  await page.goto("/#session=fixture");
  const canvas = page.locator(".project-canvas");
  const stage = page.getByRole("application", { name: "Project map", exact: true });
  await expect(stage).toBeVisible();
  const first = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const second = mapClaim(page, SECOND_TITLE);
  await expect(canvas.locator(".project-canvas-node")).toHaveCount(5);
  await expect(page.locator("#sidebar").getByText(SECOND_TITLE, { exact: true })).toHaveCount(0);
  const plot = canvas.locator(".project-canvas-plot");
  const camera = () => plot.evaluate((element) => getComputedStyle(element).transform);
  await canvas.getByRole("button", { name: "Zoom in", exact: true }).click();
  const zoomed = await camera();
  await stage.press("ArrowLeft");
  const panned = await camera();
  expect(panned).not.toEqual(zoomed);

  await first.click();
  await expect(first).toHaveAttribute("aria-pressed", "true");
  const inspector = page.locator("#review");
  const detail = inspector.locator("#node-detail");
  await expect(detail.getByText(FIRST_STATEMENT, { exact: true })).toBeVisible();
  await expect(detail.getByText("∀ n : Nat, n = 0 + n", { exact: true })).toBeVisible();
  await expect(detail.getByRole("link", { name: "1 result to review", exact: true })).toBeVisible();
  await detail.getByText("Sources and versions", { exact: true }).click();
  await expect(detail.getByText("Lean statement ID", { exact: true })).toBeVisible();
  await expect(
    inspector.getByRole("button", { name: "reverseZeroAdd Awaiting your review", exact: true }),
  ).toBeVisible();
  expect(await camera()).toEqual(panned);
  await expect(page.locator(".graph-edges")).toHaveCount(0);

  await second.press("Space");
  await expect(second).toHaveAttribute("aria-pressed", "true");
  await expect(second).toHaveCSS("box-shadow", /0px 0px 0px 1px/);
  await expect(second).toHaveCSS("background-image", "none");
  await expect(first).toHaveAttribute("aria-pressed", "false");
  await expect(detail.getByRole("heading", { name: SECOND_TITLE, exact: true })).toBeVisible();
  await expect(detail.getByText(SECOND_LEAN, { exact: true })).toBeVisible();
  await expect(detail.getByText(FIRST_STATEMENT, { exact: true })).toHaveCount(0);
  const detailsToggle = canvas.getByRole("button", { name: "Details", exact: true });
  await expect(detailsToggle).toHaveAttribute("aria-expanded", "true");
  await inspector.getByRole("button", { name: "Collapse review column", exact: true }).click();
  await expect(inspector).toBeHidden();
  await expect(detailsToggle).toBeFocused();
  await expect(detailsToggle).toHaveAttribute("aria-expanded", "false");
  await expect(second).toHaveAttribute("aria-pressed", "true");
  await second.click();
  await expect(detail.getByText(SECOND_LEAN, { exact: true })).toBeVisible();
  expect(await camera()).toEqual(panned);
  await expect(page.locator(".graph-edges")).toHaveCount(0);

  await inspector.getByRole("button", { name: "Open proof", exact: true }).click();
  await expect(stage).toBeHidden();
  await expect(detail.getByRole("heading", { name: SECOND_TITLE, exact: true })).toBeVisible();
  await expect(page.locator(".main-proof-graph .graph-edge")).toHaveCount(7);
  await expect(inspector.locator(".graph-edge")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(canvas.locator(".project-canvas-node")).toHaveCount(5);
  await expect(canvas.getByRole("button", { name: "Open proof", exact: true })).toHaveCount(0);
  expect(await camera()).toEqual(panned);
});

test("the global map is available from the project canvas", async ({ page }) => {
  await page.goto("/#session=fixture");
  const canvas = page.locator(".project-canvas");
  const toggle = canvas.getByRole("button", { name: "Global map", exact: true });
  const overview = canvas.locator(".atlas");
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await expect(overview.getByRole("img", { name: /^Recent claims in the workspace/ })).toBeVisible();
  const resize = overview.getByRole("separator", { name: "Drag to resize the overview" });
  const width = Number(await resize.getAttribute("aria-valuenow"));
  await resize.press("ArrowRight");
  await expect(resize).toHaveAttribute("aria-valuenow", String(width + 16));
  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  await canvas.getByRole("button", { name: "Link selected claim", exact: true }).click();
  await overview.getByRole("button", { name: SECOND_TITLE, exact: true }).press("Enter");
  await expect(mapClaim(page, SECOND_TITLE)).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#review")).toBeVisible();
  await expect(page.locator(".graph-edges")).toHaveCount(0);
  await expect(canvas.locator("[data-research-link]")).toHaveCount(0);
  await canvas.getByRole("button", { name: "Cancel link", exact: true }).click();
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await expect(overview).toBeHidden();
  await expect(mapClaim(page, SECOND_TITLE)).toHaveAttribute("aria-pressed", "true");
});

async function openOverview(page: Page, viewport: { width: number; height: number }) {
  await page.setViewportSize(viewport);
  await page.goto("/#session=fixture");
  const canvas = page.locator(".project-canvas");
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toBeVisible();
  await canvas.getByRole("button", { name: "Global map", exact: true }).click();
  const overview = canvas.locator(".atlas");
  await expect(overview).toBeVisible();
  const box = (await overview.boundingBox())!;
  const cards = await canvas.locator(".project-canvas-plot [data-claim-id]").evaluateAll((elements) =>
    elements.map((element) => {
      const rect = element.getBoundingClientRect();
      return {
        label: element.textContent ?? "",
        selected: element.getAttribute("aria-pressed") === "true",
        x: rect.x,
        y: rect.y,
        w: rect.width,
        h: rect.height,
      };
    }),
  );
  expect(cards.length).toBeGreaterThan(0);
  const covered = cards.filter(
    (card) =>
      card.x < box.x + box.width && box.x < card.x + card.w && card.y < box.y + box.height && box.y < card.y + card.h,
  );
  return { box, cards, covered };
}

for (const viewport of [
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
]) {
  test(`the global map opens beside the claim cards at ${viewport.width}×${viewport.height} (#154)`, async ({
    page,
  }) => {
    const { covered } = await openOverview(page, viewport);
    expect(covered.map((card) => card.label)).toEqual([]);
  });
}

for (const viewport of [
  { width: 1280, height: 640 },
  { width: 640, height: 460 },
]) {
  test(`with no free corner at ${viewport.width}×${viewport.height} the global map leaves the selected claim visible (#154)`, async ({
    page,
  }) => {
    const { box, cards, covered } = await openOverview(page, viewport);
    expect(cards.some((card) => card.selected)).toBe(true);
    expect(covered.filter((card) => card.selected)).toEqual([]);
    expect(covered.length).toBeLessThanOrEqual(1);
    if (viewport.width < 800) expect(box.width).toBeLessThan(160);
  });
}

test("opening the inspector keeps the rightmost card visible in a compact window", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/#session=fixture");
  const card = mapClaim(page, "连续自然数之积的奇偶性");
  const zoom = page.getByRole("button", { name: "Zoom to 100%", exact: true });
  await expect(card).toBeInViewport({ ratio: 0.99 });
  const initialZoom = Number.parseInt(await zoom.innerText(), 10);
  const neighbor = mapClaim(page, SECOND_TITLE);

  await card.click();
  await expect(page.locator("#review")).toBeVisible();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  await expect(card).toBeInViewport({ ratio: 0.99 });
  await expect(neighbor).toBeInViewport({ ratio: 0.99 });
  const fittedZoom = Number.parseInt(await zoom.innerText(), 10);
  expect(fittedZoom).toBeLessThanOrEqual(initialZoom);
  expect(fittedZoom).toBeGreaterThanOrEqual(60);

  await page.getByRole("button", { name: "Global map", exact: true }).click();
  const overview = page.locator(".project-canvas-stage .atlas");
  await expect(overview).toBeInViewport({ ratio: 0.99 });
  const legend = overview.getByRole("button", { name: FIXTURE_CLAIM_TITLE, exact: true });
  await expect(legend).toBeInViewport({ ratio: 0.99 });
  await legend.click();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");
});

test("an explicitly named launch claim opens the main proof canvas and returns to the selected project card", async ({
  page,
}) => {
  await page.route("**/api/claims", async (route) => {
    const response = await route.fetch();
    const project = await response.json();
    await route.fulfill({ response, json: { ...project, claim_id: project.claims[0].id } });
  });
  await page.goto("/#session=fixture");
  await expect(page.locator("#node-detail").getByRole("heading", { name: FIXTURE_CLAIM_TITLE })).toBeVisible();
  await expect(page.locator(".graph-edge")).toHaveCount(7);
  await expect(page.locator(".project-canvas")).toBeHidden();
  await page.locator(".proof-return").click();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");
});

test("finding a claim with the keyboard focuses its card without completing a pending link", async ({ page }) => {
  await page.goto("/#session=fixture");
  const canvas = page.locator(".project-canvas");
  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  await canvas.getByRole("button", { name: "Link selected claim", exact: true }).click();
  const cancelLink = canvas.getByRole("button", { name: "Cancel link", exact: true });
  const findClaim = canvas.getByRole("button", { name: "Find claim", exact: true });
  const search = page.getByRole("combobox", { name: "Search claims on map", exact: true });
  await findClaim.press("Enter");
  await expect(search).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(search).toBeHidden();
  await expect(findClaim).toBeFocused();
  await expect(cancelLink).toBeVisible();

  await findClaim.press("Enter");
  await search.fill(SECOND_TITLE);
  await expect(page.getByRole("option", { name: SECOND_TITLE, exact: true })).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(search).toBeHidden();
  const card = mapClaim(page, SECOND_TITLE);
  await expect(card).toBeFocused();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  await expect(cancelLink).toBeVisible();
  await expect(canvas.locator("[data-research-link]")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(cancelLink).toBeHidden();
});

test("keyboard moves preview without writing and save only when confirmed", async ({ page }) => {
  await page.goto("/#session=fixture");
  const card = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const layoutWrites: unknown[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/project-canvas")
      layoutWrites.push(request.postDataJSON());
  });
  const position = () =>
    card.evaluate((element) => ({ x: parseFloat(element.style.left), y: parseFloat(element.style.top) }));
  const original = await position();
  await card.press("ArrowRight");
  expect(await position()).toEqual(original);
  expect(layoutWrites).toHaveLength(0);

  const move = page.getByRole("menuitem", { name: "Move card", exact: true });
  async function begin() {
    await card.press("Shift+F10");
    await expect(move).toBeEnabled();
    await move.press("Enter");
    await expect(card).toBeFocused();
    await expect(card).toHaveAttribute("data-moving", "true");
    await expect(page.getByRole("status").filter({ hasText: "Use arrow keys to move." })).toBeVisible();
  }
  await begin();
  await card.press("ArrowRight");
  await card.press("Shift+ArrowDown");
  expect(await position()).toEqual({ x: original.x + 8, y: original.y + 32 });
  expect(layoutWrites).toHaveLength(0);

  await card.press("Escape");
  await expect(card).not.toHaveAttribute("data-moving", "true");
  await expect(card).toBeFocused();
  expect(await position()).toEqual(original);
  expect(layoutWrites).toHaveLength(0);

  await begin();
  await card.press("ArrowRight");
  const saved = { x: original.x + 8, y: original.y };
  const response = page.waitForResponse(
    (response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/project-canvas",
  );
  await card.press("Enter");
  expect((await response).ok()).toBe(true);
  await expect(card).not.toHaveAttribute("data-moving", "true");
  expect(layoutWrites).toEqual([{ action: "move", id: await card.getAttribute("data-claim-id"), ...saved }]);
  await page.reload();
  await expect.poll(position).toEqual(saved);
});

test("moving nodes and linking claims are deliberate and remain after reopen", async ({ page, request }) => {
  await page.goto("/#session=fixture");
  const canvas = page.locator(".project-canvas");
  const first = mapClaim(page, FIXTURE_CLAIM_TITLE);
  const second = mapClaim(page, SECOND_TITLE);
  const paths = canvas.locator("[data-research-link]");
  const moveFrom = await centerOf(first);
  const firstId = (await first.getAttribute("data-claim-id"))!;
  const secondId = (await second.getAttribute("data-claim-id"))!;

  await page.mouse.move(moveFrom.x, moveFrom.y);
  await page.mouse.down();
  await page.mouse.move(moveFrom.x + 64, moveFrom.y + 38, { steps: 8 });
  await page.mouse.up();
  const moved = await centerOf(first);
  expect(moved.x).toBeGreaterThan(moveFrom.x + 40);
  expect(moved.y).toBeGreaterThan(moveFrom.y + 24);
  await expect(page.locator("#review")).toBeHidden();
  await expect
    .poll(async () => (await (await request.get("/api/project-canvas")).json()).positions[firstId])
    .toEqual({ x: expect.any(Number), y: expect.any(Number) });

  await first.click();
  await page.getByRole("button", { name: "Link selected claim" }).click();
  await expect(page.getByRole("button", { name: "Cancel link" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Cancel link" })).toHaveCount(0);
  await expect(paths).toHaveCount(0);

  await page.getByRole("button", { name: "Link selected claim" }).click();
  await second.click();
  await expect(paths).toHaveCount(1);
  await expect(page.locator(".graph-edge")).toHaveCount(0);
  const [source, target] = [firstId, secondId].sort();
  await expect
    .poll(async () => (await (await request.get("/api/project-canvas")).json()).links)
    .toEqual([{ source, target }]);
  const position = (await (await request.get("/api/project-canvas")).json()).positions[firstId];

  const toggle = canvas.getByRole("button", { name: "Global map", exact: true });
  await toggle.click();
  await expect(canvas.locator(".atlas")).toBeVisible();
  await toggle.click();
  await expect(paths).toHaveCount(1);
  await page.locator("#review").getByRole("button", { name: "Open proof", exact: true }).click();
  await expect(page.locator(".graph-edge")).toHaveCount(7);
  await page.locator(".proof-return").click();
  await expect(canvas).toBeVisible();
  await expect(paths).toHaveCount(1);

  await page.reload();
  await expect(paths).toHaveCount(1);
  const restored = await first.evaluate((element) => ({
    x: (element as HTMLElement).offsetLeft,
    y: (element as HTMLElement).offsetTop,
  }));
  expect(restored).toEqual({ x: Math.round(position.x), y: Math.round(position.y) });
  await page.getByRole("button", { name: "Research links", exact: true }).click();
  await page.getByRole("menuitem", { name: /^Remove link between/ }).click();
  await expect(paths).toHaveCount(0);
  await expect(page.getByRole("application", { name: "Project map", exact: true })).toBeFocused();
  await expect.poll(async () => (await (await request.get("/api/project-canvas")).json()).links).toEqual([]);
  await page.reload();
  await expect(first).toBeVisible();
  await expect(paths).toHaveCount(0);
});
