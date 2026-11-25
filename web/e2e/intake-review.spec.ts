import { expect, test, type Page } from "@playwright/test";
import { railSpace } from "./navigation";

// #340: a starting scope waiting in Needs you or Review is confirmed in the workbench, never at a command line.

const INTAKE = "34034034-0000-4000-8000-000000000001";
const SHA = "a".repeat(64);
const LEAN_CLAIM = "34034034-0000-4000-8000-000000000002";
const SIGNER = "Ada Researcher";

const ITEMS = [
  {
    kind: "confirm_intake",
    item_id: INTAKE,
    claim_id: null,
    claim_title: null,
    waiting_since: "2026-11-25T08:59:00Z",
    question: "Do the outstanding items and reused versions match the starting research scope?",
    detail: { title: "Synthetic scope", sha256: SHA, outstanding: 2 },
    priority: 2,
  },
];
const OTHER = {
  kind: "confirm_lean_statement",
  item_id: "34034034-0000-4000-8000-000000000003",
  claim_id: LEAN_CLAIM,
  claim_title: "Synthetic Lean claim",
  waiting_since: "2026-11-25T07:00:00Z",
  question: "Does the Lean statement match the claim?",
  detail: { sha256: "b".repeat(64), version: 2 },
  priority: 2,
};
const DETAIL = {
  id: INTAKE,
  sha256: SHA,
  title: "Synthetic scope",
  domain: "lean-core",
  objective: "Check that addition with zero is the identity.",
  sensitivity: "restricted",
  recorded_at: "2026-11-25T08:59:00Z",
  status: "pending",
  confirmed: false,
  goal_id: null,
  claim_id: null,
  target: {
    statement: "Adding zero leaves a number unchanged.",
    formal: { lean: { proposition: "∀ n : Nat, n + 0 = n" } },
  },
  items: [
    {
      id: "i1",
      kind: "hypothesis",
      natural: "Natural numbers start at zero.",
      formal: { lean: { proposition: "True" } },
      status: "confirm_meaning",
      requires_decision: true,
    },
    {
      id: "i2",
      kind: "definition",
      natural: "A number is even when it is twice another number.",
      formal: null,
      status: "formalization_required",
      requires_decision: true,
    },
    {
      id: "i3",
      kind: "theorem",
      natural: "Addition is commutative.",
      formal: { lean: { proposition: "∀ a b : Nat, a + b = b + a" } },
      status: "reuse",
      requires_decision: false,
      matched_item: { version: 3, natural: "Addition is commutative.", formal: null, stale: false },
    },
  ],
  outstanding_count: 2,
  reused_count: 1,
  confirmation: null,
};

async function project(
  page: Page,
  { signer = true, items = ITEMS, language }: { signer?: boolean; items?: object[]; language?: "zh-CN" } = {},
) {
  if (language)
    await page.addInitScript((value) => localStorage.setItem("research-multiplier-language", value), language);
  let open = items;
  const sent: unknown[] = [];
  await page.route("**/api/inbox", async (route) => {
    const response = await route.fetch();
    const inbox = await response.json();
    const identity = signer ? { review_identity: { workspace_id: "w", display_name: SIGNER } } : {};
    await route.fulfill({ response, json: { ...inbox, ...identity, items: open } });
  });
  await page.route("**/api/intake-review?*", (route) => route.fulfill({ json: DETAIL }));
  await page.route("**/api/intake-review/confirm", async (route) => {
    sent.push(route.request().postDataJSON());
    open = open.filter((item) => (item as { item_id: string }).item_id !== INTAKE);
    await route.fulfill({ json: { id: INTAKE, goal_id: "g", claim_id: null, confirmed: true } });
  });
  return sent;
}

const peek = (page: Page) => page.locator(".work-peek");
const panel = (page: Page) => page.locator("nav.section-nav");
const item = (page: Page, index: number) => page.locator(".intake-item").nth(index);

test.beforeEach(async ({ request }) => {
  await request.post("/__reset", { data: {} });
});

test("Review shows the intake and confirms it with decisions, Lean and a signed reason", async ({ page }) => {
  const sent = await project(page);
  await page.goto(`/#session=fixture`);
  await page.evaluate((id) => (location.hash = `review/${id}`), INTAKE);

  await expect(page.getByRole("heading", { name: "Synthetic scope", level: 2 })).toBeVisible();
  await expect(page.getByText("Starting scope to confirm", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("Adding zero leaves a number unchanged.")).toBeVisible();
  await expect(page.getByText("∀ n : Nat, n + 0 = n")).toBeVisible();
  await expect(page.getByRole("heading", { name: "2 items to decide" })).toBeVisible();
  await expect(item(page, 0)).toContainText("1. Hypothesis");
  await expect(item(page, 0)).toContainText("Check the wording");
  await expect(item(page, 1)).toContainText("Needs a Lean proposition");
  await expect(page.locator(".intake-reused summary")).toHaveText("1 item reused from the Library");
  await expect(page.getByText("Recorded by " + SIGNER, { exact: false })).toBeVisible();
  // Nothing on the page asks for a command line or shows the raw checksum.
  await expect(page.locator("main")).not.toContainText("research-multiplier");
  await expect(page.locator("main")).not.toContainText(SHA);

  const submit = page.getByRole("button", { name: "Confirm starting scope" });
  await submit.click();
  await expect(
    page.getByRole("status").filter({ hasText: "Choose Confirm or Omit for every item first." }),
  ).toBeVisible();
  await item(page, 0).getByLabel("Confirm").check();
  await item(page, 1).getByLabel("Confirm").check();
  await page.getByLabel("Reason").fill("The premises match the setting.");
  await submit.click();
  await expect(page.getByRole("status").filter({ hasText: "Add a Lean proposition" })).toBeVisible();
  await item(page, 1).getByLabel("Lean proposition for 2. Definition").fill("∀ n : Nat, Even n ↔ ∃ k, n = 2 * k");
  await submit.click();

  await expect(page.locator(".page-notice")).toHaveText("The starting scope is confirmed.");
  expect(sent).toEqual([
    {
      id: INTAKE,
      sha256: SHA,
      reason: "The premises match the setting.",
      decisions: {
        i1: { action: "accept", reason: "The premises match the setting." },
        i2: {
          action: "accept",
          reason: "The premises match the setting.",
          formal: { lean: { proposition: "∀ n : Nat, Even n ↔ ∃ k, n = 2 * k" } },
        },
      },
    },
  ]);
});

test("without a local identity the form asks who is confirming, and a stale intake says to review it again", async ({
  page,
}) => {
  const sent = await project(page, { signer: false });
  await page.route("**/api/intake-review/confirm", (route) =>
    route.fulfill({ status: 409, json: { error: "intakeReview.stale" } }),
  );
  await page.goto(`/#session=fixture`);
  await page.evaluate((id) => (location.hash = `review/${id}`), INTAKE);
  await item(page, 0).getByLabel("Omit").check();
  await item(page, 1).getByLabel("Omit").check();
  await expect(item(page, 1).getByRole("textbox")).toHaveCount(0);
  await page.getByLabel("Your name").fill("Grace");
  await page.getByLabel("Reason").fill("Out of scope.");
  await page.getByRole("button", { name: "Confirm starting scope" }).click();
  await expect(page.getByRole("status").filter({ hasText: "This intake changed since you opened it." })).toBeVisible();
  expect(sent).toEqual([]);
});

test("Needs you names the intake, the side panel confirms it, and Handle on Review opens the page instead", async ({
  page,
}) => {
  const sent = await project(page);
  await page.goto("/#session=fixture");
  await railSpace(page, "Work").click();
  const row = panel(page).locator(".waiting-row", { hasText: "Synthetic scope" });
  // The mark names the kind; the detail already says what to confirm, so it is not repeated.
  await expect(row.getByRole("img", { name: "To confirm" })).toBeVisible();
  await expect(row.locator("small")).toHaveText(/^Starting scope to confirm · /);
  await row.hover();
  await row.getByRole("button", { name: "Handle" }).click();
  await expect(peek(page)).toBeVisible();
  await expect(peek(page).getByRole("heading", { name: "Synthetic scope" })).toBeVisible();
  await expect(peek(page)).not.toContainText("research-multiplier");
  await peek(page).locator(".intake-item").nth(0).getByLabel("Confirm").check();
  await peek(page).locator(".intake-item").nth(1).getByLabel("Omit").check();
  await peek(page).getByLabel("Reason").fill("Only the first premise is needed.");
  await peek(page).getByRole("button", { name: "Confirm starting scope" }).click();
  await expect(peek(page).getByRole("status").first()).toHaveText("The starting scope is confirmed.");
  expect((sent[0] as { decisions: object }).decisions).toEqual({
    i1: { action: "accept", reason: "Only the first premise is needed." },
    i2: { action: "omit", reason: "Only the first premise is needed." },
  });
});

test("Handle on the Review page opens the item there without a second copy beside it", async ({ page }) => {
  await project(page, { items: [...ITEMS, OTHER] });
  await page.goto("/#session=fixture");
  await page.evaluate((id) => (location.hash = `review/${id}`), OTHER.item_id);
  const row = panel(page).locator(".waiting-row", { hasText: "Synthetic scope" });
  await row.hover();
  await row.getByRole("button", { name: "Handle" }).click();
  await expect(page).toHaveURL(new RegExp(`#review/${INTAKE}$`));
  await expect(page.getByRole("heading", { name: "Synthetic scope", level: 2 })).toBeVisible();
  await expect(peek(page)).toHaveCount(0);
});

test("an item kind without a form says so plainly, with no command or checksum", async ({ page }) => {
  await project(page, { items: [OTHER] });
  await page.goto("/#session=fixture");
  await page.evaluate((id) => (location.hash = `review/${id}`), OTHER.item_id);
  await expect(page.getByRole("heading", { name: "Lean statement to confirm", level: 2 })).toBeVisible();
  await expect(
    page.getByText("Trellin cannot record this decision in the workbench yet.", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Open in Workbench" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText("research-multiplier");
  await expect(page.locator("main")).not.toContainText("b".repeat(64));
});

test("the intake reads in Chinese", async ({ page }) => {
  await project(page, { language: "zh-CN" });
  await page.goto("/#session=fixture");
  await page.evaluate((id) => (location.hash = `review/${id}`), INTAKE);
  await expect(page.getByRole("heading", { name: "2 项待决定" })).toBeVisible();
  await expect(item(page, 1)).toContainText("需要 Lean 命题");
  await expect(page.locator(".intake-reused summary")).toHaveText("1 项复用定理库");
  await expect(page.getByRole("button", { name: "确认起始范围" })).toBeVisible();
  await expect(page.getByText("待确认的起始范围", { exact: false }).first()).toBeVisible();
});
