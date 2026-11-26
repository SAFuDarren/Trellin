import { expect, test, type Page } from "@playwright/test";

// #345: Review lists the decisions people recorded; what waits is handled from Needs you, with Approve and next.

const EVIDENCE = "77777777-7777-4777-8777-777777777777";
const QUESTION = "88888888-8888-4888-8888-888888888888";
const panel = (page: Page) => page.locator("nav.section-nav");
const records = (page: Page) => page.locator(".decision-records-list > li");

async function go(page: Page, hash: string) {
  await page.goto("/#session=fixture");
  await page.evaluate((target) => (location.hash = target), hash);
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("Decision records lists recorded decisions newest first, filters them and opens their claim", async ({ page }) => {
  await go(page, "review");
  await expect(page.getByRole("heading", { name: "Decision records", level: 2 })).toBeVisible();
  await expect(records(page)).toHaveCount(2);
  await expect(records(page).first()).toContainText("Approved");
  await expect(records(page).first()).toContainText("FirstProof.zeroAddEarlier");
  await expect(records(page).first()).toContainText("Scope matches; no extra premises.");
  await expect(records(page).nth(1)).toContainText("Confirmed");
  await expect(records(page).nth(1)).toContainText("Synthetic starting scope");

  await page.getByRole("button", { name: "Starting scopes", exact: true }).click();
  await expect(records(page)).toHaveCount(1);
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(records(page)).toHaveCount(2);

  // The page does not repeat what waits; Needs you lists it.
  await expect(page.locator(".section-nav .nav-group").filter({ hasText: "Waiting for you" })).toHaveCount(0);
  await records(page).first().getByRole("button", { name: "自然数加零的对称性" }).click();
  // The claim opens on the project map, as Open in workbench does.
  await expect(page).toHaveURL(/#workbench$/);
  await expect(page.getByRole("application", { name: "Project map", exact: true })).toBeVisible();
});

test("Approve and next records the decision, moves to the next waiting item and lists the decision", async ({
  page,
}) => {
  await go(page, `review/${EVIDENCE}`);
  await expect(page.locator(".page-head h2")).toHaveText("reverseZeroAdd");
  await expect(page.getByRole("radio")).toHaveCount(0);
  await page.getByLabel("Your name").fill("Fixture reviewer");
  const reason = page.getByLabel("Reason", { exact: true });
  await page.getByRole("button", { name: "The scope matches the claim" }).click();
  await expect(reason).toHaveValue("The scope matches the claim");
  await page.getByRole("button", { name: "Uses only confirmed premises" }).click();
  await expect(reason).toHaveValue("The scope matches the claim; Uses only confirmed premises");
  await page.getByRole("button", { name: "Approve and next" }).click();

  await expect(page).toHaveURL(new RegExp(`#review/${QUESTION}$`));
  await expect(page.locator(".page-notice")).toHaveText("Approved and recorded.");
  await expect(panel(page).locator(".work-waiting")).toContainText("Needs you · 1");

  await go(page, "review");
  await expect(records(page)).toHaveCount(3);
  await expect(records(page).first()).toContainText("Approved");
  await expect(records(page).first()).toContainText("The scope matches the claim; Uses only confirmed premises");
});

test("Return asks for a reason and records the return", async ({ page }) => {
  await go(page, `review/${EVIDENCE}`);
  await page.getByLabel("Your name").fill("Fixture reviewer");
  await page.getByRole("button", { name: "Return", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Add your name and a reason first.");
  await page.getByLabel("Reason", { exact: true }).fill("The statement uses an unconfirmed premise.");
  await page.getByRole("button", { name: "Return", exact: true }).click();
  await expect(page.locator(".page-notice")).toHaveText("Returned with your note.");
  await go(page, "review");
  await expect(records(page).first()).toContainText("Returned");
});

test("Inbox is retired: Records lists Decision records, and its old address opens Work", async ({ page }) => {
  await go(page, "claims");
  const entries = panel(page).locator(".work-records");
  await expect(entries.getByRole("link")).toHaveText([/^Decision records/, /^Agent tasks/, /^Library/]);
  await page.evaluate(() => (location.hash = "agent-work/inbox"));
  await expect(page).toHaveURL(/#claims$/);
});

test("Decision records reads in Chinese", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("research-multiplier-language", "zh-CN"));
  await go(page, "review");
  await expect(page.getByRole("heading", { name: "判断记录", level: 2 })).toBeVisible();
  await expect(records(page).first()).toContainText("已批准");
  await go(page, `review/${EVIDENCE}`);
  await expect(page.getByRole("button", { name: "批准并下一项" })).toBeVisible();
  await expect(page.getByRole("button", { name: "范围与课题一致" })).toBeVisible();
});
