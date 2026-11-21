import { expect, test, type Locator } from "@playwright/test";
import { FIXTURE_CLAIM_TITLE, mapClaim, openProof } from "./project-map";

const viewports = [
  { width: 640, height: 460 },
  { width: 1024, height: 768 },
  { width: 1440, height: 900 },
];

async function inside(target: Locator, container: Locator) {
  const [rect, bounds] = await Promise.all([target.boundingBox(), container.boundingBox()]);
  return Boolean(
    rect &&
      bounds &&
      rect.x >= bounds.x - 1 &&
      rect.y >= bounds.y - 1 &&
      rect.x + rect.width <= bounds.x + bounds.width + 1 &&
      rect.y + rect.height <= bounds.y + bounds.height + 1,
  );
}

async function disjoint(first: Locator, second: Locator) {
  const [a, b] = await Promise.all([first.boundingBox(), second.boundingBox()]);
  return Boolean(
    a &&
      b &&
      (a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1 || a.y + a.height <= b.y + 1 || b.y + b.height <= a.y + 1),
  );
}

for (const viewport of viewports) {
  test(`proof controls and the selected canvas targets remain visible at ${viewport.width}×${viewport.height}`, async ({
    page,
  }) => {
    await page.route("**/api/project-canvas", async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      const response = await route.fetch();
      const layout = await response.json();
      await route.fulfill({
        response,
        json: {
          ...layout,
          positions: {
            "11111111-1111-4111-8111-111111111111": { x: 0, y: 0 },
            "aaaaaaaa-0000-4000-8000-000000000001": { x: 232, y: 0 },
            "aaaaaaaa-0000-4000-8000-000000000002": { x: 464, y: 0 },
            "aaaaaaaa-0000-4000-8000-000000000003": { x: 116, y: 350 },
            "aaaaaaaa-0000-4000-8000-000000000004": { x: 348, y: 350 },
          },
        },
      });
    });
    await page.goto("/#session=fixture");
    await openProof(page);
    const proof = page.locator(".main-proof-graph");
    const stage = proof.locator(".stage");
    const root = proof.locator('.graph-node[data-kind="goal"]');
    const selected = proof.locator('.graph-node[aria-pressed="true"]');
    const evidence = proof.getByRole("button", { name: /^reverseZeroAdd,/ });
    const zoom = proof.getByRole("group", { name: "Zoom" });
    const lenses = page.locator(".proof-lenses");
    const details = page.locator("#proof-details-toggle");
    const review = page.locator("#review");
    await expect(root).toBeVisible();
    await evidence.click();
    await zoom.getByRole("button", { name: "Zoom in" }).click();
    await page.setViewportSize(viewport);
    await expect.poll(() => inside(selected, stage)).toBe(true);
    await expect.poll(() => disjoint(stage, zoom)).toBe(true);
    const counts = proof.getByRole("status", { name: "Proof status counts" });
    expect(await counts.evaluate((row) => row.scrollWidth <= row.clientWidth)).toBe(true);
    await expect.poll(() => disjoint(lenses, details)).toBe(true);
    await expect.poll(() => disjoint(lenses, review)).toBe(true);
    await expect.poll(() => disjoint(zoom, review)).toBe(true);
    expect(
      await lenses
        .locator("button")
        .evaluateAll((buttons) => new Set(buttons.map((button) => button.getBoundingClientRect().y)).size),
    ).toBe(1);
    await expect(review).toHaveCSS("position", viewport.width < 900 ? "absolute" : "static");

    if (viewport.width < 900) {
      await page.keyboard.press("Escape");
      await expect(review).toBeHidden();
      await expect(proof).toBeVisible();
      await expect(details).toBeFocused();
      await expect.poll(() => inside(selected, stage)).toBe(true);
    }
    await page.locator(".proof-return").click();

    const map = page.getByRole("application", { name: "Project map", exact: true });
    const mapZoom = page.locator(".project-canvas").getByRole("group", { name: "Zoom" });
    const middle = mapClaim(page, "单射映射的复合");
    const rightmost = mapClaim(page, "连续自然数之积的奇偶性");
    await mapZoom.getByRole("button", { name: "Fit to window" }).click();
    await mapZoom.getByRole("button", { name: "Zoom in" }).click({ clickCount: 2 });
    // Choose with the finder even when manual zoom moves this card outside the viewport.
    await page.getByRole("button", { name: "Find claim" }).click();
    await page.getByRole("option", { name: "单射映射的复合", exact: true }).click();
    await expect(review).toBeVisible();
    await expect(middle).toHaveAttribute("aria-pressed", "true");
    for (const card of [mapClaim(page, FIXTURE_CLAIM_TITLE), middle, rightmost]) {
      await expect.poll(() => inside(card, map)).toBe(true);
      await expect.poll(() => disjoint(card, review)).toBe(true);
    }
    await expect.poll(() => disjoint(map, mapZoom)).toBe(true);
  });
}

test("saved wide side panels leave room for proof controls when the window shrinks", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      "research-multiplier-workbench-layout",
      JSON.stringify({
        side: 420,
        right: 640,
        sidebarOpen: true,
      }),
    );
  });
  await page.goto("/#session=fixture");
  await openProof(page);
  const proof = page.locator(".main-proof-graph");
  const stage = proof.locator(".stage");
  const selected = proof.locator('.graph-node[aria-pressed="true"]');
  const review = page.locator("#review");
  const toolbar = page.locator(".toolbar");
  const lenses = page.locator(".proof-lenses");
  for (const viewport of [...viewports].reverse()) {
    await page.setViewportSize(viewport);
    await expect.poll(() => inside(selected, stage)).toBe(true);
    await expect.poll(() => disjoint(lenses, review)).toBe(true);
    await expect.poll(() => disjoint(lenses, page.locator("#proof-details-toggle"))).toBe(true);
  }
  await expect.poll(async () => (await toolbar.boundingBox())?.height ?? 0).toBeLessThanOrEqual(52);
});
