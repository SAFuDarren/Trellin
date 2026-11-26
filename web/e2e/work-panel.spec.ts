import { expect, test, type Page } from "@playwright/test";
import { boardLayout } from "./claims-display";
import { railSpace } from "./navigation";

// #326: the Work panel lists what waits for you in one row style, and a side panel opens items large
// beside the board so you can move through them without leaving it.

const panel = (page: Page) => page.locator("nav.section-nav");
const waiting = (page: Page) => panel(page).locator(".waiting-row");
const peek = (page: Page) => page.locator(".work-peek");

async function openWork(page: Page) {
  await page.goto("/#session=fixture");
  await railSpace(page, "Work").click();
  await expect(page).toHaveURL(/#claims$/);
}

test.beforeEach(async ({ page, request }) => {
  await request.post("/__reset", { data: {} });
  await page.addInitScript(() => {
    if (sessionStorage.getItem("work-panel-test")) return;
    sessionStorage.setItem("work-panel-test", "1");
    localStorage.removeItem("trellin.work.pinned");
    localStorage.removeItem("trellin.claims.display");
    localStorage.removeItem("trellin.work.panel.folded");
    localStorage.removeItem("research-multiplier-workbench-layout");
  });
});

test("the Work panel lists waiting items, views with counts and records", async ({ page }) => {
  await openWork(page);
  await expect(panel(page).getByText("Needs you · 2")).toBeVisible();
  await expect(waiting(page)).toHaveCount(2);
  // Actions stay hidden until a row is hovered or focused.
  const first = waiting(page).first();
  await expect(first.getByRole("button", { name: "Handle" })).toBeHidden();
  await first.hover();
  await expect(first.getByRole("button", { name: "Handle" })).toBeVisible();
  await expect(first.getByRole("link", { name: "Open page" })).toBeVisible();

  const views = panel(page).locator(".work-views");
  await expect(views.getByRole("link", { name: /^All claims/ })).toHaveAttribute("aria-current", "page");
  await expect(views.getByRole("link", { name: /^All claims/ })).toContainText("5");
  await views.getByRole("link", { name: /^Running work/ }).click();
  await expect(page).toHaveURL(/#claims\/running$/);
  await expect(views.getByRole("link", { name: /^Running work/ })).toHaveAttribute("aria-current", "page");

  const records = panel(page).locator(".work-records");
  await expect(records.getByRole("link")).toHaveText([/^Decision records/, /^Agent tasks/, /^Library/]);
  await records.getByRole("link", { name: /^Decision records/ }).click();
  await expect(page).toHaveURL(/#review/);
  await expect(panel(page).getByText("Needs you · 2")).toBeVisible();
});

test("Handle opens a waiting item large, J and K move through the list, and Escape closes it", async ({ page }) => {
  await openWork(page);
  await panel(page).getByRole("button", { name: "Go through" }).click();
  await expect(peek(page)).toBeVisible();
  await expect(peek(page)).toContainText("1 of 2");
  await expect(peek(page).getByRole("button", { name: "Approve and next" })).toBeVisible();
  await expect(page.locator(".research-lanes")).toBeVisible();

  await page.keyboard.press("j");
  await expect(peek(page)).toContainText("2 of 2");
  await expect(peek(page).getByRole("button", { name: "Next (J)" })).toBeDisabled();
  await page.keyboard.press("k");
  await expect(peek(page)).toContainText("1 of 2");

  // Typing in the form does not move through the list.
  await peek(page).getByLabel("Reason", { exact: true }).fill("jk");
  await expect(peek(page)).toContainText("1 of 2");
  await peek(page).getByLabel("Reason", { exact: true }).press("Escape");
  await page.locator("main").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Escape");
  await expect(peek(page)).toHaveCount(0);
});

test("recording a decision in the panel moves on to the next waiting item", async ({ page }) => {
  await openWork(page);
  await waiting(page)
    .first()
    .getByRole("button", { name: /reverseZeroAdd/ })
    .click();
  await expect(peek(page)).toContainText("1 of 2");
  await peek(page).getByLabel("Your name").fill("Fixture researcher");
  await peek(page).getByLabel("Reason", { exact: true }).fill("Scope matches.");
  await peek(page).getByRole("button", { name: "Approve and next" }).click();
  await expect(peek(page).getByRole("status").filter({ hasText: "Approved and recorded." })).toBeVisible();
  await expect(peek(page)).toContainText("1 of 1");
  await expect(peek(page)).not.toContainText("reverseZeroAdd");
  await expect(panel(page).getByText("Needs you · 1")).toBeVisible();
});

test("a board card opens its claim in the panel, which can pin it", async ({ page }) => {
  await openWork(page);
  const card = page.locator(".lane-card-toggle").first();
  await card.click();
  await expect(card).toHaveAttribute("aria-expanded", "true");
  await expect(peek(page)).toContainText(/1 of \d/);
  await expect(peek(page).getByRole("button", { name: "Open in Workbench" })).toBeVisible();
  const title = (await peek(page).locator(".work-peek-title").textContent())!;

  await peek(page).getByRole("button", { name: "Pin" }).click();
  await expect(peek(page).getByRole("button", { name: "Unpin" })).toHaveAttribute("aria-pressed", "true");
  const pins = panel(page).locator(".work-pins");
  await expect(pins.locator(".work-pin")).toHaveCount(1);
  await expect(pins).toContainText(title);

  await page.keyboard.press("Escape");
  await page.reload();
  await expect(panel(page).locator(".work-pins .work-pin")).toHaveCount(1);
  await panel(page).locator(".work-pins .work-pin").click();
  await expect(peek(page).locator(".work-peek-title")).toHaveText(title);
});

test("the panel reads in Chinese", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("research-multiplier-language", "zh-CN"));
  await page.goto("/#session=fixture");
  await page.evaluate(() => {
    location.hash = "#claims";
  });
  await expect(panel(page).getByText("等你判断 · 2")).toBeVisible();
  await panel(page).getByRole("button", { name: "逐项处理" }).click();
  await expect(peek(page)).toContainText("第 1 项，共 2 项");
});

const LONG_LEAN =
  "theorem long_statement\n" +
  Array.from(
    { length: 14 },
    (_, i) => `  (h${i} : Fintype.card S ≤ m → ∃ C, C ∈ M ∧ dist (p C) (q C) ≥ 1 - m * (1 - k m))`,
  ).join("\n");

async function withLongLean(page: Page) {
  await page.route("**/api/graph?*", async (route) => {
    const response = await route.fetch();
    const graph = await response.json();
    for (const node of graph.nodes ?? []) if (node.kind === "goal") node.formalization = LONG_LEAN;
    await route.fulfill({ response, json: graph });
  });
}

test("a claim opens in a window floating over the board, which stays where it was", async ({ page }) => {
  await openWork(page);
  const board = page.locator(".research-lanes");
  const resting = await boardLayout(page);
  const card = page.locator(".lane-card").first();
  await card.locator(".lane-card-toggle").click();
  await expect(peek(page)).toBeVisible();
  // #351: the board does not move under the window; the opened claim is marked, and scrolls clear by hand.
  expect(await boardLayout(page)).toEqual(resting);
  const window = (await page.locator(".work-peek-float").boundingBox())!;
  expect(window.width).toBeCloseTo(560, -1);
  await expect(page.locator('.lane-card[data-open="true"]')).toHaveCount(1);
  await board.evaluate((element) => (element.scrollLeft = element.scrollWidth));
  const opened = (await page.locator('.lane-card[data-open="true"]').boundingBox())!;
  expect(opened.x + opened.width).toBeLessThanOrEqual(window.x);
  await page.keyboard.press("Escape");
  await expect(peek(page)).toHaveCount(0);
});

test("a long Lean statement stays inside its box in the panel and in a preview", async ({ page }) => {
  await withLongLean(page);
  await openWork(page);
  await page.locator(".lane-card-toggle").first().click();
  const code = peek(page).locator(".code-block");
  await expect(code).toContainText("h13");
  const codeBox = (await code.boundingBox())!;
  const button = (await peek(page).getByRole("button", { name: "Open in Workbench" }).boundingBox())!;
  expect(codeBox.y + codeBox.height).toBeLessThanOrEqual(button.y);
  await page.keyboard.press("Escape");

  // A preview in the list keeps its height and scrolls the statement inside it.
  await page.getByRole("button", { name: "Display", exact: true }).click();
  await page.getByRole("menuitemradio", { name: "List", exact: true }).click();
  await page.locator(".board-card-toggle").first().click();
  const preview = page.locator(".board-preview .code-block").first();
  await expect(preview).toBeVisible();
  expect(await preview.evaluate((element) => getComputedStyle(element).overflowY)).toBe("auto");
  expect(await preview.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
});

test("panel sections fold and stay folded, and a record page's groups hang under its entry", async ({ page }) => {
  await openWork(page);
  const views = panel(page).getByRole("button", { name: "Views" });
  await expect(views).toHaveAttribute("aria-expanded", "true");
  await views.click();
  await expect(views).toHaveAttribute("aria-expanded", "false");
  await expect(panel(page).getByRole("link", { name: /^All claims/ })).toHaveCount(0);
  await page.reload();
  await expect(panel(page).getByRole("button", { name: "Views" })).toHaveAttribute("aria-expanded", "false");
  await panel(page).getByRole("button", { name: "Views" }).click();

  await panel(page)
    .getByRole("link", { name: /^Library/ })
    .click();
  const branch = panel(page).locator(".work-branch");
  await expect(branch.getByRole("link", { name: /^Everything/ })).toBeVisible();
  // The groups follow the Library entry, with the remaining records after them.
  const libraryBox = (await panel(page)
    .getByRole("link", { name: /^Library/ })
    .boundingBox())!;
  expect((await branch.boundingBox())!.y).toBeGreaterThan(libraryBox.y);
  const fold = panel(page).getByRole("button", { name: "Fold Library groups" });
  await fold.click();
  await expect(branch).toHaveCount(0);
  await panel(page).getByRole("button", { name: "Show Library groups" }).click();
  await expect(panel(page).locator(".work-branch")).toBeVisible();
});
