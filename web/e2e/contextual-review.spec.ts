import { expect, test, type Page } from "@playwright/test";
import type { InboxItem } from "../src/api/sections";
import { FIXTURE_CLAIM_TITLE, mapClaim } from "./project-map";

const EVIDENCE = "77777777-7777-4777-8777-777777777777";
const SECOND_EVIDENCE = "77777777-7777-4777-8777-777777777778";
const WORKSPACE = "11111111-2222-4333-8444-555555555555";
const CLAIM = "11111111-1111-4111-8111-111111111111";
const resultName = "reverseZeroAdd";

async function inspectClaim(page: Page) {
  await page.goto("/#session=fixture");
  await mapClaim(page, FIXTURE_CLAIM_TITLE).click();
  return page.locator("#review");
}

async function expectReview(page: Page, evidence: string, title: string) {
  await expect(page).toHaveURL(new RegExp(`#review/${evidence}$`));
  await expect(page.locator(".page-head h2")).toHaveText(title);
  await expect(page.locator(".section-nav .nav-item[aria-current=page] .label")).toHaveText(title);
  await expect(page.locator(".section-nav .nav-item[aria-current=page] .nav-sub")).toContainText(FIXTURE_CLAIM_TITLE);
}

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("the claim, inspector queue and selected result open the evidence review directly", async ({ page }) => {
  const inspector = await inspectClaim(page);
  const queueItem = inspector.getByRole("link", { name: "reverseZeroAdd Awaiting your review", exact: true });
  const review = inspector.getByRole("link", { name: "Review", exact: true });
  await expect(review).toHaveClass(/\bprimary\b/);
  await expect(review).toHaveAttribute("href", `#review/${EVIDENCE}`);
  await queueItem.click();
  await expectReview(page, EVIDENCE, resultName);
  await expect(page.getByText(/records acceptance of this verified proof of the claim’s Lean statement/)).toBeVisible();

  await page.getByRole("button", { name: "Open in workbench", exact: true }).click();
  await expect(inspector.getByRole("heading", { name: resultName, exact: true })).toBeVisible();
  await expect(review).toHaveClass(/\bprimary\b/);
  await review.click();
  await expectReview(page, EVIDENCE, resultName);

  let writes = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/review")) writes++;
  });
  await page.getByLabel("Your name").fill("Fixture researcher");
  const reason = page.getByLabel("Reason", { exact: true });
  await reason.fill("Recheck the scope against the selected Lean statement.");
  await reason.press("Meta+Enter");
  await expect(page.getByText("Choose Approve or Return first.", { exact: true })).toBeVisible();
  expect(writes).toBe(0);

  await page.getByRole("radio", { name: "Return", exact: true }).check();
  const sent = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/api/review"));
  await reason.press("Meta+Enter");
  expect((await sent).postDataJSON()).toEqual({
    evidence_id: EVIDENCE,
    action: "return",
    actor: "Fixture researcher",
    reason: "Recheck the scope against the selected Lean statement.",
  });
  await expect(page.getByRole("status").filter({ hasText: "Returned with your note." })).toBeVisible();
  expect(writes).toBe(1);
});

test("a single footer result opens directly and records the decision through the bound identity API", async ({
  page,
}) => {
  let decision: { decision: string; reason: string } | null = null;
  let idempotency: string | undefined;
  await page.route("**/api/inbox", async (route) => {
    const response = await route.fetch();
    const inbox = await response.json();
    const items = decision ? inbox.items.filter((item: InboxItem) => item.item_id !== EVIDENCE) : inbox.items;
    await route.fulfill({ response, json: { ...inbox, items, review_identity: { workspace_id: WORKSPACE } } });
  });
  await page.route(`**/api/v1/workspaces/${WORKSPACE}/evidence/${EVIDENCE}/reviews`, async (route) => {
    expect(route.request().method()).toBe("POST");
    idempotency = (await route.request().allHeaders())["idempotency-key"];
    decision = route.request().postDataJSON();
    await route.fulfill({ status: 201, json: { id: "decision-one", user_id: "fixture-user" } });
  });
  await page.goto("/#session=fixture");
  await page.getByRole("link", { name: "1 to review", exact: true }).click();
  await expectReview(page, EVIDENCE, resultName);
  await expect(page.getByLabel("Your name")).toHaveCount(0);
  await page.getByRole("radio", { name: "Approve", exact: true }).check();
  const reason = page.getByLabel("Reason", { exact: true });
  await reason.press("Control+Enter");
  await expect(page.getByText("Add a reason first.", { exact: true })).toBeVisible();
  expect(decision).toBeNull();
  await reason.fill("The result proves the selected Lean statement.");
  await reason.press("Control+Enter");
  await expect(page.getByText("Approved and recorded.", { exact: true })).toBeVisible();
  expect(decision).toEqual({ decision: "approve", reason: "The result proves the selected Lean statement." });
  expect(idempotency).toMatch(/^[a-f0-9-]{36}$/);
  await expect(page.getByRole("link", { name: "1 to review", exact: true })).toHaveCount(0);
});

test("the footer filters multiple reviews and names each result before opening its exact decision", async ({
  page,
}) => {
  const second: InboxItem = {
    kind: "review_evidence",
    item_id: SECOND_EVIDENCE,
    claim_id: CLAIM,
    claim_title: FIXTURE_CLAIM_TITLE,
    waiting_since: new Date().toISOString(),
    question: "Review this fixture result.",
    detail: { kind: "counterexample", declaration: "Alternative.checkedCounterexample", target: "refutation" },
    priority: 1,
  };
  const seen: string[] = [];
  await page.route("**/api/inbox", async (route) => {
    const response = await route.fetch();
    const inbox = await response.json();
    await route.fulfill({ response, json: { ...inbox, items: seen.length ? inbox.items : [...inbox.items, second] } });
  });
  await page.route("**/api/review", async (route) => {
    seen.push(route.request().postDataJSON().evidence_id);
    await route.fulfill({ json: { id: "decision-two" } });
  });
  const inspector = await inspectClaim(page);
  await inspector.getByRole("button", { name: "Review", exact: true }).click();
  const picker = page.getByRole("menu", { name: "Review", exact: true });
  await expect(picker.getByRole("menuitem", { name: "checkedCounterexample", exact: true })).toHaveAttribute(
    "href",
    `#review/${SECOND_EVIDENCE}`,
  );
  await page.keyboard.press("Escape");

  const count = page.getByRole("button", { name: "2 to review", exact: true });
  await count.click();
  const activity = page.getByRole("region", { name: "Agent activity", exact: true });
  await expect(activity.getByRole("button", { name: "Needs review", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(activity.locator("[data-activity-state]")).toHaveCount(2);
  const row = activity.getByRole("link", { name: /checkedCounterexample/ });
  await expect(row.locator("strong")).toHaveText("checkedCounterexample");
  await expect(row.locator("small")).toContainText(FIXTURE_CLAIM_TITLE);
  await expect(row).toContainText("Awaiting your review");
  await row.click();
  await expectReview(page, SECOND_EVIDENCE, "checkedCounterexample");
  await expect(page.getByText(/as accepted evidence against the claim/)).toBeVisible();

  await page.getByLabel("Your name").fill("Fixture researcher");
  await page.getByRole("radio", { name: "Approve", exact: true }).check();
  await page.getByLabel("Reason", { exact: true }).fill("The second result addresses the recorded claim.");
  await page.getByRole("button", { name: "Record decision", exact: true }).click();
  await expect(page.getByText("Approved and recorded.", { exact: true })).toBeVisible();
  expect(seen).toEqual([SECOND_EVIDENCE]);
});
