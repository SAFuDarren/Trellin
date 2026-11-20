import { expect, test, type Locator } from "@playwright/test";
import type { GraphSnapshot } from "../src/workbench/types";

const TITLE = "Symmetry of equality on the natural numbers with explanatory text that needs several visible lines";
const LAYOUT_KEY = "research-multiplier-workbench-layout";

function dimensions(card: Locator) {
  return card.evaluate((el) => {
    const { x, y } = el.getBoundingClientRect();
    const { offsetWidth: cssWidth, offsetHeight: cssHeight } = el as HTMLElement;
    return { x, y, cssWidth, cssHeight };
  });
}

test("resize a graph card independently at different zooms and remember per view", async ({ page }) => {
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = (await response.json()) as GraphSnapshot;
    const goal = graph.nodes.find((node) => node.kind === "goal")!;
    goal.title = TITLE;
    await route.fulfill({ response, json: { ...graph, nodes: [goal], edges: [], focus_id: goal.id } });
  });
  await page.goto("/#session=fixture");
  const card = page.locator('.graph-node[data-kind="goal"]');
  const handle = page.getByRole("button", { name: `Resize node ${TITLE}`, exact: true });
  const zoom = page.locator(".zoom-level");
  await expect(card).toHaveAttribute("title", TITLE);
  await expect(handle).toHaveAttribute("aria-keyshortcuts", "ArrowLeft ArrowRight ArrowUp ArrowDown");
  await page.getByRole("button", { name: "Fit to window", exact: true }).click();
  await expect(zoom).toHaveText("125%");
  const start = await dimensions(card);
  const originalTitle = await card.locator(".node-title").evaluate((el) => el.clientHeight);
  const first = (await handle.boundingBox())!;
  await page.mouse.move(first.x + first.width / 2, first.y + first.height / 2);
  await page.mouse.down();
  await page.mouse.move(first.x + first.width / 2 + 70, first.y + first.height / 2, { steps: 5 });
  await expect.poll(async () => (await dimensions(card)).cssWidth).toBe(start.cssWidth + 56);
  const wide = await dimensions(card);
  expect(wide.cssHeight).toBe(start.cssHeight);
  expect(wide.x).toBeCloseTo(start.x, 0);
  expect(wide.y).toBeCloseTo(start.y, 0);
  expect(await page.evaluate((key) => localStorage.getItem(key), LAYOUT_KEY)).toBeNull();
  await page.mouse.up();
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), LAYOUT_KEY)).not.toBeNull();

  await page.getByRole("button", { name: "Zoom out", exact: true }).click();
  await expect(zoom).toHaveText("104%");
  const beforeHeight = await dimensions(card);
  const second = (await handle.boundingBox())!;
  await page.mouse.move(second.x + second.width / 2, second.y + second.height / 2);
  await page.mouse.down();
  await page.mouse.move(second.x + second.width / 2, second.y + second.height / 2 + 80, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => (await dimensions(card)).cssHeight).toBe(beforeHeight.cssHeight + 77);
  const tall = await dimensions(card);
  expect(tall.cssWidth).toBe(wide.cssWidth);
  expect(tall.x).toBeCloseTo(beforeHeight.x, 0);
  expect(tall.y).toBeCloseTo(beforeHeight.y, 0);
  const title = await card.locator(".node-title").evaluate((el) => ({
    height: el.clientHeight,
    contained: el.getBoundingClientRect().right <= el.closest(".graph-node")!.getBoundingClientRect().right,
  }));
  expect(title.height).toBeGreaterThan(originalTitle);
  expect(title.contained).toBe(true);
  await expect(card).toHaveAttribute("title", TITLE);

  await handle.focus();
  await handle.press("Shift+ArrowRight");
  await expect.poll(async () => (await dimensions(card)).cssWidth).toBe(tall.cssWidth + 36);
  await handle.press("ArrowUp");
  await expect.poll(async () => (await dimensions(card)).cssHeight).toBe(tall.cssHeight - 12);
  await expect(handle).toBeFocused();

  await page.getByRole("button", { name: "References", exact: true }).click();
  await expect.poll(async () => (await dimensions(card)).cssWidth).toBe(start.cssWidth);
  await page.getByRole("button", { name: "Proof", exact: true }).click();
  await expect.poll(async () => (await dimensions(card)).cssWidth).toBe(tall.cssWidth + 36);
  await page.reload();
  await expect.poll(async () => (await dimensions(card)).cssHeight).toBe(tall.cssHeight - 12);
  await expect.poll(async () => (await dimensions(card)).cssWidth).toBe(tall.cssWidth + 36);
});
