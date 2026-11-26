import { expect, test, type Page } from "@playwright/test";

// #345: Needs you is the one list of waiting items, and an item opens in a window floating over the board.

const INTAKE = {
  kind: "confirm_intake",
  item_id: "34534534-0000-4000-8000-000000000001",
  claim_id: null,
  claim_title: null,
  waiting_since: "2026-11-25T08:00:00Z",
  question: "",
  detail: { title: "Synthetic scope", outstanding: 1 },
  priority: 2,
};

const panel = (page: Page) => page.locator("nav.section-nav");
const needsYou = (page: Page) => panel(page).locator(".work-waiting");
const floating = (page: Page) => page.locator(".work-peek-float");
const edge = (page: Page) => page.getByRole("separator", { name: /Drag to resize the window/ });

async function openWork(page: Page, extra: object[] = [INTAKE]) {
  await page.route("**/api/inbox", async (route) => {
    const response = await route.fetch();
    const inbox = await response.json();
    await route.fulfill({ response, json: { ...inbox, items: [...inbox.items, ...extra] } });
  });
  await page.goto("/#session=fixture");
  await page.evaluate(() => (location.hash = "claims"));
  await expect(page.locator(".research-lanes")).toBeVisible();
}

test.beforeEach(async ({ page, request }) => {
  await request.post("/__reset", { data: {} });
  await page.addInitScript(() => {
    if (sessionStorage.getItem("needs-you-test")) return;
    sessionStorage.setItem("needs-you-test", "1");
    localStorage.removeItem("research-multiplier-workbench-layout");
    localStorage.removeItem("trellin.work.panel.folded");
  });
});

test("every count of waiting items agrees, whether or not an item belongs to a claim", async ({ page }) => {
  await openWork(page);
  const rows = needsYou(page).locator(".waiting-row");
  const total = await rows.count();
  expect(total).toBe(3);
  await expect(needsYou(page)).toContainText(`Needs you · ${total}`);
  await expect(page.getByRole("link", { name: new RegExp(`^Work · ${total} waiting for you`) })).toBeVisible();
  await expect(needsYou(page).getByRole("button", { name: `All ${total}` })).toHaveAttribute("aria-pressed", "true");
  // Records keep no second count, the status bar shows Agent activity only, and there is no review view.
  await expect(panel(page).locator(".work-records .count")).toHaveCount(0);
  await expect(page.locator(".agent-dock-status")).not.toContainText(/to review|needs attention/);
  await expect(panel(page).getByRole("link", { name: /^Waiting for review/ })).toHaveCount(0);
});

test("the kind filter narrows the list and what Go through steps through", async ({ page }) => {
  await openWork(page);
  await needsYou(page).getByRole("button", { name: "To confirm 1" }).click();
  await expect(needsYou(page).locator(".waiting-row")).toHaveCount(1);
  await expect(needsYou(page)).toContainText("Needs you · 3");
  await needsYou(page).getByRole("button", { name: "Go through" }).click();
  await expect(floating(page)).toContainText("1 of 1");
  await expect(floating(page).getByRole("button", { name: "Next" })).toBeDisabled();
  await needsYou(page)
    .getByRole("button", { name: /^All 3/ })
    .click();
  await expect(needsYou(page).locator(".waiting-row")).toHaveCount(3);
});

test("an item opens floating over the board, which keeps its width and shows the item's claim", async ({ page }) => {
  await openWork(page, []);
  const board = page.locator(".research-lanes");
  const before = (await board.boundingBox())!;
  const cardWidths = () => page.locator(".lane-card").evaluateAll((cards) => cards.map((card) => card.clientWidth));
  const widths = await cardWidths();
  const row = needsYou(page).locator(".waiting-row").first();
  await row.hover();
  await row.getByRole("button", { name: "Handle" }).click();
  await expect(floating(page)).toBeVisible();
  const after = (await board.boundingBox())!;
  expect(after.width).toBe(before.width);
  // No column narrows or rewraps its cards when the window opens.
  expect(await cardWidths()).toEqual(widths);
  // The window sits below the page header, which stays usable.
  const head = (await page.locator(".section-main .page-head").first().boundingBox())!;
  const window = (await floating(page).boundingBox())!;
  expect(window.y).toBeGreaterThanOrEqual(head.y + head.height);
  await expect(page.getByRole("button", { name: "New claim" })).toBeEnabled();
  // The claim the item belongs to is marked and scrolled clear of the window.
  const card = page.locator('.lane-card[data-open="true"]');
  await expect(card).toHaveCount(1);
  const box = (await card.boundingBox())!;
  expect(box.x + box.width).toBeLessThanOrEqual(window.x);
  await page.keyboard.press("Escape");
  await expect(floating(page)).toHaveCount(0);
});

test("the window's left edge resizes it within limits, double-click restores it, and the width is kept", async ({
  page,
}) => {
  await openWork(page, []);
  await needsYou(page).getByRole("button", { name: "Go through" }).click();
  await expect(edge(page)).toHaveAttribute("aria-valuenow", "560");
  await expect(edge(page)).toHaveAttribute("aria-valuemin", "420");
  await expect(edge(page)).toHaveAttribute("aria-valuemax", "900");

  const box = (await edge(page).boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 - 140, box.y + box.height / 2, { steps: 4 });
  await page.mouse.up();
  await expect(edge(page)).toHaveAttribute("aria-valuenow", "700");
  expect(Math.round((await floating(page).boundingBox())!.width)).toBe(700);

  await edge(page).focus();
  await page.keyboard.press("Shift+ArrowLeft");
  await page.keyboard.press("Shift+ArrowLeft");
  await page.keyboard.press("Shift+ArrowLeft");
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(edge(page)).toHaveAttribute("aria-valuenow", "892");
  // A further step stops at the limit.
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(edge(page)).toHaveAttribute("aria-valuenow", "900");

  await page.reload();
  await expect(page.locator(".research-lanes")).toBeVisible();
  await needsYou(page).getByRole("button", { name: "Go through" }).click();
  await expect(edge(page)).toHaveAttribute("aria-valuenow", "900");
  await edge(page).dblclick();
  await expect(edge(page)).toHaveAttribute("aria-valuenow", "560");
});

test("Needs you reads in Chinese", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("research-multiplier-language", "zh-CN"));
  await openWork(page);
  await expect(needsYou(page).getByRole("button", { name: "全部 3" })).toBeVisible();
  await expect(needsYou(page).getByRole("button", { name: "待确认 1" })).toBeVisible();
  await needsYou(page).getByRole("button", { name: "逐项处理" }).click();
  await expect(page.getByRole("separator", { name: "拖动调整窗口宽度，双击恢复" })).toBeVisible();
});
