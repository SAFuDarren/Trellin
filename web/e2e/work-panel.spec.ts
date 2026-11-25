import { expect, test, type Page } from "@playwright/test";
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
  await expect(records.getByRole("link")).toHaveText([/^Inbox/, /^Review/, /^Agent tasks/, /^Library/]);
  await records.getByRole("link", { name: /^Review/ }).click();
  await expect(page).toHaveURL(/#review/);
  await expect(panel(page).getByText("Needs you · 2")).toBeVisible();
});

test("Handle opens a waiting item large, J and K move through the list, and Escape closes it", async ({ page }) => {
  await openWork(page);
  await panel(page).getByRole("button", { name: "Go through" }).click();
  await expect(peek(page)).toBeVisible();
  await expect(peek(page)).toContainText("1 of 2");
  await expect(peek(page).getByRole("button", { name: "Record decision" })).toBeVisible();
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
  await peek(page).getByRole("radio", { name: "Approve" }).check();
  await peek(page).getByLabel("Reason", { exact: true }).fill("Scope matches.");
  await peek(page).getByRole("button", { name: "Record decision" }).click();
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
  await expect(pins.getByRole("button")).toHaveCount(1);
  await expect(pins).toContainText(title);

  await page.keyboard.press("Escape");
  await page.reload();
  await expect(panel(page).locator(".work-pins").getByRole("button")).toHaveCount(1);
  await panel(page).locator(".work-pins").getByRole("button").click();
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
