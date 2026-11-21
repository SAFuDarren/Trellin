import { expect, test, type Locator } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

const SECOND_TITLE = "单射映射的复合";

async function centerOf(locator: Locator) {
  await expect(locator).toBeInViewport();
  const bounds = (await locator.boundingBox())!;
  return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("the project map is the workbench entry and node selection keeps its camera", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  // The older claim is still in the project even after falling out of the recent list.
  await page.route("**/api/claims", async (route) => {
    const response = await route.fetch();
    const project = await response.json();
    const claims = project.claims.filter((claim: { title: string }) => claim.title !== SECOND_TITLE);
    await route.fulfill({ response, json: { ...project, claims } });
  });
  const proofRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/graph") proofRequests.push(url.searchParams.get("claim") ?? "");
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
  await second.press("Enter");
  await expect(second).toHaveAttribute("aria-pressed", "true");
  await expect(second).toHaveCSS("box-shadow", "none");
  await expect(second).toHaveCSS("background-image", "none");
  await expect(first).toHaveAttribute("aria-pressed", "false");
  const secondId = await second.getAttribute("data-claim-id");
  expect(await camera()).toEqual(panned);
  expect(proofRequests).toEqual([]);
  await expect(page.locator(".graph-edges")).toHaveCount(0);

  await page.getByRole("button", { name: "Open proof", exact: true }).click();
  await expect(page.locator(".graph-edge")).toHaveCount(7);
  expect(proofRequests).toEqual([secondId]);
});

test("an explicitly named launch claim opens proof and can return to the project map", async ({ page }) => {
  await page.route("**/api/claims", async (route) => {
    const response = await route.fetch();
    const project = await response.json();
    await route.fulfill({ response, json: { ...project, claim_id: project.claims[0].id } });
  });
  await page.goto("/#session=fixture");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(FIXTURE_CLAIM_TITLE);
  await expect(page.locator(".graph-edge")).toHaveCount(7);
  await page.getByRole("button", { name: "Project map", exact: true }).click();
  await expect(mapClaim(page, FIXTURE_CLAIM_TITLE)).toHaveAttribute("aria-pressed", "true");
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

  await page.reload();
  await expect(paths).toHaveCount(1);
  const restored = await first.evaluate((element) => ({
    x: (element as HTMLElement).offsetLeft,
    y: (element as HTMLElement).offsetTop,
  }));
  expect(restored).toEqual({ x: Math.round(position.x), y: Math.round(position.y) });
  await page.getByRole("button", { name: "Research links", exact: true }).click();
  await page.getByRole("button", { name: /^Remove link between/ }).click();
  await expect(paths).toHaveCount(0);
  await expect.poll(async () => (await (await request.get("/api/project-canvas")).json()).links).toEqual([]);
  await page.reload();
  await expect(first).toBeVisible();
  await expect(paths).toHaveCount(0);
});
